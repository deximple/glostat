from __future__ import annotations

import asyncio
import io
import json
import os
import time
import zipfile
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final
from xml.etree import ElementTree as ET

import httpx
import structlog

from glostat.core.errors import GlostatError
from glostat.data.dart_types import (
    CorpCodeEntry,
    DartCompanyOverview,
    DartExecutiveTransaction,
    DartFinancialItem,
    DartFinancialStatements,
    _parse_number,
)
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

# v1.2 L2 — DART (Korea Financial Supervisory Service Open API) client.
# Source: https://opendart.fss.or.kr/
#
# WHY: yfinance covers KR PER/dividend yield partially but ROE / EPS / 임원 거래
# are unreliable. DART is the official KR equivalent of SEC EDGAR — free for
# 10,000 calls/day per registered key.
#
# Graceful degradation: if GLOSTAT_DART_API_KEY is unset, every call raises
# DartApiKeyMissingError so callers can branch cleanly to a yfinance fallback
# without faking DART data.

log: Final = structlog.get_logger(__name__)

_BASE: Final = "https://opendart.fss.or.kr/api"
_RATE_LIMIT_PER_SEC: Final[int] = 10
_MIN_INTERVAL_S: Final[float] = 1.0 / _RATE_LIMIT_PER_SEC
_DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(20.0, connect=5.0)
_DEFAULT_CORP_CODE_CACHE: Final[Path] = Path("cache") / "dart" / "corp_code.parquet"


class DartApiKeyMissingError(NotImplementedError):
    """Raised when GLOSTAT_DART_API_KEY is not configured."""

    @classmethod
    def make(cls) -> DartApiKeyMissingError:
        return cls(
            "DART API key missing. Register at https://opendart.fss.or.kr/ "
            "(free, 10,000 calls/day) and export GLOSTAT_DART_API_KEY=<key>. "
            "Without this, KR insider transactions + DART fundamentals are skipped."
        )


class DartApiError(GlostatError):
    """Raised when DART API returns a non-recoverable error."""


def _resolve_api_key(*, override: str | None = None) -> str:
    candidate = override or os.environ.get("GLOSTAT_DART_API_KEY")
    if not candidate or not candidate.strip():
        raise DartApiKeyMissingError.make()
    return candidate.strip()


class _Throttle:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._next_slot: float = 0.0
        self.acquire_count: int = 0
        self.throttled_count: int = 0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            if wait > 0:
                self.throttled_count += 1
            self._next_slot = max(now, self._next_slot) + _MIN_INTERVAL_S
            self.acquire_count += 1
        if wait > 0:
            await asyncio.sleep(wait)


class DartClient:
    """DART OpenAPI client. Raises DartApiKeyMissingError if no key is configured."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        snapshot_broker: SnapshotBroker | None = None,
        corp_code_cache: Path | None = None,
    ) -> None:
        self._api_key = _resolve_api_key(override=api_key)
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        self._broker = snapshot_broker
        self._corp_code_cache = corp_code_cache or _DEFAULT_CORP_CODE_CACHE
        self._stock_to_corp: dict[str, str] | None = None
        self._throttle = _Throttle()
        self._last_snapshot_id: str | None = None

    @property
    def last_snapshot_id(self) -> str | None:
        return self._last_snapshot_id

    @property
    def throttle(self) -> _Throttle:
        return self._throttle

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── corp_code lookup ──────────────────────────────────────────────────

    async def get_corp_code(self, stock_code_6digit: str) -> str:
        normalized = stock_code_6digit.strip()
        if normalized.endswith(".KS") or normalized.endswith(".KQ"):
            normalized = normalized[:-3]
        if not (len(normalized) == 6 and normalized.isdigit()):
            raise DartApiError(
                f"DART get_corp_code: expected 6-digit KRX code, got {stock_code_6digit!r}"
            )
        if self._stock_to_corp is None:
            self._stock_to_corp = await self._load_or_fetch_corp_codes()
        corp_code = self._stock_to_corp.get(normalized)
        if not corp_code:
            raise DartApiError(
                f"DART get_corp_code: no DART corp_code for KRX {normalized!r} "
                "(may be unlisted or delisted)"
            )
        return corp_code

    async def _load_or_fetch_corp_codes(self) -> dict[str, str]:
        cached = self._read_corp_code_cache()
        if cached:
            return cached
        await self._throttle.acquire()
        url = f"{_BASE}/corpCode.xml"
        params = {"crtfc_key": self._api_key}
        try:
            resp = await self._client.get(url, params=params)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DartApiError(f"DART corpCode fetch failed: {exc}") from exc
        entries = _parse_corp_code_zip(resp.content)
        mapping = {e.stock_code: e.corp_code for e in entries if e.stock_code}
        self._write_corp_code_cache(entries)
        return mapping

    def _read_corp_code_cache(self) -> dict[str, str] | None:
        path = self._corp_code_cache
        if not path.exists():
            return None
        try:
            import pyarrow.parquet as pq  # noqa: PLC0415

            table = pq.read_table(path)
            rows = table.to_pylist()
            return {
                str(r["stock_code"]): str(r["corp_code"])
                for r in rows if r.get("stock_code")
            }
        except Exception as exc:
            log.warning("dart.corp_code_cache_load_failed", err=str(exc))
            return None

    def _write_corp_code_cache(self, entries: list[CorpCodeEntry]) -> None:
        try:
            import pyarrow as pa  # noqa: PLC0415
            import pyarrow.parquet as pq  # noqa: PLC0415

            self._corp_code_cache.parent.mkdir(parents=True, exist_ok=True)
            payload = [
                {
                    "corp_code": e.corp_code, "corp_name": e.corp_name,
                    "stock_code": e.stock_code, "modify_date": e.modify_date,
                } for e in entries
            ]
            table = pa.Table.from_pylist(payload)
            pq.write_table(table, self._corp_code_cache, compression="zstd")
        except Exception as exc:
            log.warning("dart.corp_code_cache_save_failed", err=str(exc))

    # ── financial statements ──────────────────────────────────────────────

    async def get_financial_statements(
        self, corp_code: str, *, year: int, reprt_code: str = "11011",
        fs_div: str = "CFS",
    ) -> DartFinancialStatements:
        await self._throttle.acquire()
        url = f"{_BASE}/fnlttSinglAcntAll.json"
        params = {
            "crtfc_key": self._api_key,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        }
        data = await self._get_json(url, params)
        items = _parse_financial_items(data.get("list", []) or [])
        statements = DartFinancialStatements(
            corp_code=corp_code,
            bsns_year=str(year),
            reprt_code=reprt_code,
            items=tuple(items),
        )
        self._record_snapshot(
            tool="dart.fnlttSinglAcntAll",
            uaid=f"DART.CORP.{corp_code}",
            edge_type="financial_statements",
            ts=datetime.now(tz=UTC),
            params={"corp_code": corp_code, "bsns_year": str(year),
                    "reprt_code": reprt_code, "fs_div": fs_div},
            payload={"corp_code": corp_code, "n_items": len(items)},
        )
        return statements

    async def get_company_overview(self, corp_code: str) -> DartCompanyOverview:
        await self._throttle.acquire()
        url = f"{_BASE}/company.json"
        params = {"crtfc_key": self._api_key, "corp_code": corp_code}
        data = await self._get_json(url, params)
        ov = DartCompanyOverview(
            corp_code=str(data.get("corp_code", corp_code)),
            corp_name=str(data.get("corp_name", "")),
            corp_name_eng=str(data.get("corp_name_eng", "")),
            stock_code=str(data.get("stock_code", "")),
            ceo_nm=str(data.get("ceo_nm", "")),
            est_dt=str(data.get("est_dt", "")),
            induty_code=str(data.get("induty_code", "")),
            market=_market_from_code(str(data.get("corp_cls", ""))),
        )
        self._record_snapshot(
            tool="dart.company",
            uaid=f"DART.CORP.{corp_code}",
            edge_type="company_overview",
            ts=datetime.now(tz=UTC),
            params={"corp_code": corp_code},
            payload={"corp_code": corp_code, "corp_name": ov.corp_name},
        )
        return ov

    async def get_executive_transactions(
        self, corp_code: str, *, days_back: int = 180,
    ) -> tuple[DartExecutiveTransaction, ...]:
        await self._throttle.acquire()
        end = date.today()
        start = end - timedelta(days=days_back)
        url = f"{_BASE}/elestock.json"
        params = {
            "crtfc_key": self._api_key,
            "corp_code": corp_code,
            "bgn_de": start.strftime("%Y%m%d"),
            "end_de": end.strftime("%Y%m%d"),
        }
        data = await self._get_json(url, params)
        rows = data.get("list", []) or []
        out: list[DartExecutiveTransaction] = []
        for row in rows:
            txn = _build_executive_txn(corp_code, row)
            if txn is not None:
                out.append(txn)
        self._record_snapshot(
            tool="dart.elestock",
            uaid=f"DART.CORP.{corp_code}",
            edge_type="executive_transactions",
            ts=datetime.now(tz=UTC),
            params={"corp_code": corp_code, "days_back": days_back},
            payload={"corp_code": corp_code, "n_txns": len(out)},
        )
        return tuple(out)

    # ── http + snapshot helpers ──────────────────────────────────────────

    async def _get_json(self, url: str, params: Mapping[str, str]) -> dict[str, Any]:
        try:
            resp = await self._client.get(url, params=dict(params))
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise DartApiError(f"DART GET failed url={url}: {exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise DartApiError(f"DART non-JSON response from {url}: {exc}") from exc
        status = str(data.get("status", "000"))
        # 000 = OK, 013 = no data — both treated as success.
        if status not in {"000", "013"}:
            msg = data.get("message", "unknown")
            raise DartApiError(f"DART API error status={status} message={msg}")
        return data

    def _record_snapshot(
        self, *, tool: str, uaid: str, edge_type: str, ts: datetime,
        params: dict[str, Any], payload: dict[str, Any],
    ) -> None:
        if self._broker is None:
            return
        try:
            key = SnapshotKey(
                uaid=uaid, edge_type=edge_type, ts_utc=ts, tool=tool,
                params_canon=json.dumps(params, sort_keys=True, separators=(",", ":")),
            )
            record = self._broker.save_snapshot(key, payload)
            self._last_snapshot_id = record.leaf.leaf_hash
        except Exception as exc:
            log.warning("dart.snapshot_failed", uaid=uaid, err=str(exc))


def _parse_corp_code_zip(zipped_bytes: bytes) -> list[CorpCodeEntry]:
    out: list[CorpCodeEntry] = []
    try:
        with zipfile.ZipFile(io.BytesIO(zipped_bytes)) as zf:
            xml_name = next((n for n in zf.namelist() if n.endswith(".xml")), None)
            if xml_name is None:
                return out
            with zf.open(xml_name) as fh:
                tree = ET.parse(fh)
    except (zipfile.BadZipFile, ET.ParseError) as exc:
        log.warning("dart.corp_code_parse_failed", err=str(exc))
        return out
    root = tree.getroot()
    for el in root.findall("list"):
        out.append(CorpCodeEntry(
            corp_code=(el.findtext("corp_code") or "").strip(),
            corp_name=(el.findtext("corp_name") or "").strip(),
            stock_code=(el.findtext("stock_code") or "").strip(),
            modify_date=(el.findtext("modify_date") or "").strip(),
        ))
    return out


def _parse_financial_items(rows: list[dict[str, Any]]) -> list[DartFinancialItem]:
    out: list[DartFinancialItem] = []
    for r in rows:
        out.append(DartFinancialItem(
            account_id=str(r.get("account_id", "")),
            account_name=str(r.get("account_nm", "")),
            fs_div=str(r.get("fs_div", "")),
            sj_div=str(r.get("sj_div", "")),
            thstrm_amount=str(r.get("thstrm_amount", "")),
            frmtrm_amount=str(r.get("frmtrm_amount", "")),
            bfefrmtrm_amount=str(r.get("bfefrmtrm_amount", "")),
            thstrm_nm=str(r.get("thstrm_nm", "")),
            currency=str(r.get("currency", "KRW")),
        ))
    return out


def _market_from_code(corp_cls: str) -> str:
    return {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}.get(corp_cls.upper(), "OTHER")


def _build_executive_txn(
    corp_code: str, row: dict[str, Any],
) -> DartExecutiveTransaction | None:
    trd_kind = str(row.get("trd_kind", "")).strip()
    irds_cnt = _parse_number(str(row.get("sp_stock_lmp_irds_cnt", "")))
    if irds_cnt is None:
        irds_cnt = 0.0
    is_buy = irds_cnt > 0 or "취득" in trd_kind or "매수" in trd_kind
    is_sell = irds_cnt < 0 or "처분" in trd_kind or "매도" in trd_kind
    return DartExecutiveTransaction(
        corp_code=corp_code,
        repror=str(row.get("repror", "")),
        isu_exctv_rgist_at=str(row.get("isu_exctv_rgist_at", "")),
        isu_exctv_ofcps=str(row.get("isu_exctv_ofcps", "")),
        isu_main_shrholdr=str(row.get("isu_main_shrholdr", "")),
        sp_stock_lmp_cnt=str(row.get("sp_stock_lmp_cnt", "")),
        sp_stock_lmp_irds_cnt=str(row.get("sp_stock_lmp_irds_cnt", "")),
        sp_stock_lmp_irds_rate=str(row.get("sp_stock_lmp_irds_rate", "")),
        bsis_dt=str(row.get("bsis_dt", "")),
        rcept_dt=str(row.get("rcept_dt", "")),
        trd_kind=trd_kind,
        is_buy=is_buy,
        is_sell=is_sell,
    )


def is_dart_configured() -> bool:
    """Cheap check used by experts to decide whether to attempt a DART call."""
    val = os.environ.get("GLOSTAT_DART_API_KEY")
    return bool(val and val.strip())


__all__ = [
    "DartApiError",
    "DartApiKeyMissingError",
    "DartClient",
    "is_dart_configured",
]
