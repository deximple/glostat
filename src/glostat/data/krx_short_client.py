from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

import httpx
import structlog

from glostat.core.errors import GlostatError
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

# v1.4 N2 — KRX 공매도 통계 client.
#
# Source: https://data.krx.co.kr (KRX 정보데이터시스템). KRX exposes a public
# AJAX endpoint at /comm/bldAttendant/getJsonData.cmd that returns short-balance
# and short-volume time series in JSON. No API key required.
#
# Endpoints used:
#   - dbms/MDC/STAT/srt/MDCSTAT30401  → daily short volume per ticker (전종목 일별)
#   - dbms/MDC/STAT/srt/MDCSTAT30501  → daily short balance per ticker
#
# Self-throttle: 5 req/sec to be polite (no published rate limit, but KRX is a
# regulator-run public service — keep load low).

log: Final = structlog.get_logger(__name__)

_BASE: Final = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Referer": "http://data.krx.co.kr/",
    "X-Requested-With": "XMLHttpRequest",
}
_BLD_VOLUME: Final = "dbms/MDC/STAT/srt/MDCSTAT30401"
_BLD_BALANCE: Final = "dbms/MDC/STAT/srt/MDCSTAT30501"

_RATE_LIMIT_PER_SEC: Final[int] = 5
_MIN_INTERVAL_S: Final[float] = 1.0 / _RATE_LIMIT_PER_SEC
_DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(20.0, connect=5.0)


class KrxShortError(GlostatError):
    """Raised when KRX returns an unrecoverable error or malformed payload."""


@dataclass(frozen=True, slots=True)
class KrxShortBalanceBar:
    bar_date: date
    code: str
    short_balance_qty: float       # 공매도 잔고 수량
    short_balance_won: float       # 공매도 잔고 금액 (KRW)
    listed_qty: float              # 상장주식 수
    short_balance_ratio: float     # 잔고 / 상장 (%)


@dataclass(frozen=True, slots=True)
class KrxShortVolumeBar:
    bar_date: date
    code: str
    short_volume: float            # 공매도 거래량 (shares)
    short_value_won: float         # 공매도 거래대금 (KRW)
    total_volume: float            # 전체 거래량
    short_ratio_pct: float         # 공매도/전체 (%)


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


class KrxShortClient:
    """KRX 공매도 통계 client (free, public AJAX endpoint).

    Skip behaviour: HTTP errors and malformed payloads raise KrxShortError so
    the caller can fall through to a graceful expert-skip. KRX occasionally
    rate-limits aggressive scrapers; the throttle keeps us under their visible
    floor.
    """

    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        snapshot_broker: SnapshotBroker | None = None,
    ) -> None:
        self._client = client or httpx.AsyncClient(timeout=_DEFAULT_TIMEOUT)
        self._broker = snapshot_broker
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

    # ── public read methods ──────────────────────────────────────────────

    async def get_short_balance(
        self, ticker: str, *, days_back: int = 30, end: date | None = None,
    ) -> tuple[KrxShortBalanceBar, ...]:
        code = _normalize_code(ticker)
        end_d = end or date.today()
        start_d = end_d - timedelta(days=days_back)
        rows = await self._fetch_rows(_BLD_BALANCE, code, start_d, end_d)
        out: list[KrxShortBalanceBar] = []
        for r in rows:
            bar = _row_to_balance(r, code)
            if bar is not None:
                out.append(bar)
        out.sort(key=lambda b: b.bar_date)
        self._record_snapshot(
            tool="krx.MDCSTAT30501",
            uaid=f"XKRX.{code}",
            edge_type="short_balance",
            ts=datetime.now(tz=UTC),
            params={"code": code, "start": start_d.isoformat(),
                    "end": end_d.isoformat()},
            payload={"code": code, "n_rows": len(out)},
        )
        return tuple(out)

    async def get_short_volume(
        self, ticker: str, *, days_back: int = 30, end: date | None = None,
    ) -> tuple[KrxShortVolumeBar, ...]:
        code = _normalize_code(ticker)
        end_d = end or date.today()
        start_d = end_d - timedelta(days=days_back)
        rows = await self._fetch_rows(_BLD_VOLUME, code, start_d, end_d)
        out: list[KrxShortVolumeBar] = []
        for r in rows:
            bar = _row_to_volume(r, code)
            if bar is not None:
                out.append(bar)
        out.sort(key=lambda b: b.bar_date)
        self._record_snapshot(
            tool="krx.MDCSTAT30401",
            uaid=f"XKRX.{code}",
            edge_type="short_volume",
            ts=datetime.now(tz=UTC),
            params={"code": code, "start": start_d.isoformat(),
                    "end": end_d.isoformat()},
            payload={"code": code, "n_rows": len(out)},
        )
        return tuple(out)

    # ── http + snapshot helpers ──────────────────────────────────────────

    async def _fetch_rows(
        self, bld: str, code: str, start: date, end: date,
    ) -> list[dict[str, Any]]:
        await self._throttle.acquire()
        payload = {
            "bld": bld,
            "locale": "ko_KR",
            "tboxisuCd_finder_stkisu0_0": code,
            "isuCd": code,
            "isuCd2": code,
            "strtDd": start.strftime("%Y%m%d"),
            "endDd": end.strftime("%Y%m%d"),
            "share": "1",
            "money": "1",
            "csvxls_isNo": "false",
        }
        try:
            resp = await self._client.post(
                _BASE, data=payload, headers=_HEADERS,
            )
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise KrxShortError(f"KRX POST failed bld={bld} code={code}: {exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise KrxShortError(f"KRX non-JSON response bld={bld}: {exc}") from exc
        rows = data.get("output", []) or data.get("OutBlock_1", []) or []
        if not isinstance(rows, list):
            return []
        return rows

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
            log.warning("krx_short.snapshot_failed", uaid=uaid, err=str(exc))


def _normalize_code(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        t = t[:-3]
    if not (len(t) == 6 and t.isdigit()):
        raise KrxShortError(
            f"KRX expects 6-digit KRX code, got {ticker!r}"
        )
    return t


def _row_to_balance(row: dict[str, Any], code: str) -> KrxShortBalanceBar | None:
    bd = _parse_krx_date(row.get("TRD_DD") or row.get("trd_dd") or "")
    if bd is None:
        return None
    try:
        return KrxShortBalanceBar(
            bar_date=bd, code=code,
            short_balance_qty=_parse_signed(row.get("BAL_QTY", "0")),
            short_balance_won=_parse_signed(row.get("BAL_AMT", "0")),
            listed_qty=_parse_signed(row.get("LIST_SHRS", "0")),
            short_balance_ratio=_parse_signed(row.get("BAL_RTO", "0")),
        )
    except (KeyError, ValueError):
        return None


def _row_to_volume(row: dict[str, Any], code: str) -> KrxShortVolumeBar | None:
    bd = _parse_krx_date(row.get("TRD_DD") or row.get("trd_dd") or "")
    if bd is None:
        return None
    try:
        return KrxShortVolumeBar(
            bar_date=bd, code=code,
            short_volume=_parse_signed(row.get("CVSRTSELL_TRDVOL", "0")),
            short_value_won=_parse_signed(row.get("CVSRTSELL_TRDVAL", "0")),
            total_volume=_parse_signed(row.get("ACC_TRDVOL", "0")),
            short_ratio_pct=_parse_signed(row.get("TRDVOL_WT", "0")),
        )
    except (KeyError, ValueError):
        return None


def _parse_krx_date(s: str) -> date | None:
    s = (s or "").strip().replace("/", "").replace("-", "")
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _parse_signed(raw: Any) -> float:
    if raw is None:
        return 0.0
    s = str(raw).strip().replace(",", "").replace("+", "")
    if not s or s == "-":
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


__all__ = [
    "KrxShortBalanceBar",
    "KrxShortClient",
    "KrxShortError",
    "KrxShortVolumeBar",
]
