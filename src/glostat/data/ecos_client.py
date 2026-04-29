from __future__ import annotations

import asyncio
import json
import os
import time
from collections.abc import Mapping
from datetime import UTC, date, datetime
from typing import Any, Final

import httpx
import structlog

from glostat.core.errors import GlostatError
from glostat.data.ecos_types import (
    EcosApiKeyMissingError,
    EcosObservation,
    EcosSeries,
    _parse_value,
)
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

# v1.3 M2 — ECOS (Bank of Korea Economic Statistics System) OpenAPI client.
# Source: https://ecos.bok.or.kr/
#
# WHY: KR macro context (BoK base rate, KRW/USD, CPI, FX reserves, KOSPI index)
# is the canonical macro overlay for KR equities. ECOS is the official BoK
# OpenAPI — free with 10,000 calls/day per registered key. Mirrors dart_client
# pattern: graceful skip on missing key + Snapshot Broker integration + 10 req/sec
# self-throttle.

log: Final = structlog.get_logger(__name__)

_BASE: Final = "https://ecos.bok.or.kr/api"
_RATE_LIMIT_PER_SEC: Final[int] = 10
_MIN_INTERVAL_S: Final[float] = 1.0 / _RATE_LIMIT_PER_SEC
_DEFAULT_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(20.0, connect=5.0)
_MAX_ROWS_PER_CALL: Final[int] = 1000

# Canonical statistic codes used by E_MACRO_KR. Each is (stat_code, item_code, cycle).
STAT_BASE_RATE: Final[tuple[str, str, str]] = ("722Y001", "0101000", "M")
STAT_KRW_USD: Final[tuple[str, str, str]] = ("731Y001", "0000001", "D")
STAT_CPI: Final[tuple[str, str, str]] = ("901Y009", "0", "M")
STAT_FX_RESERVES: Final[tuple[str, str, str]] = ("732Y001", "99", "M")
STAT_KOSPI: Final[tuple[str, str, str]] = ("802Y001", "0001000", "D")


class EcosApiError(GlostatError):
    """Raised when ECOS returns an error or non-recoverable HTTP failure."""


def _resolve_api_key(*, override: str | None = None) -> str:
    candidate = override or os.environ.get("GLOSTAT_ECOS_API_KEY")
    if not candidate or not candidate.strip():
        raise EcosApiKeyMissingError.make()
    return candidate.strip()


def is_ecos_configured() -> bool:
    """Cheap check used by experts to decide whether to attempt an ECOS call."""
    val = os.environ.get("GLOSTAT_ECOS_API_KEY")
    return bool(val and val.strip())


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


def _fmt_period(d: date, cycle: str) -> str:
    # ECOS expects YYYYMM for monthly, YYYYMMDD for daily.
    if cycle.upper() in {"D"}:
        return d.strftime("%Y%m%d")
    if cycle.upper() in {"Q"}:
        q = (d.month - 1) // 3 + 1
        return f"{d.year}Q{q}"
    if cycle.upper() in {"A"}:
        return f"{d.year}"
    return d.strftime("%Y%m")


class EcosClient:
    """ECOS OpenAPI client. Raises EcosApiKeyMissingError if no key is configured."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        client: httpx.AsyncClient | None = None,
        snapshot_broker: SnapshotBroker | None = None,
    ) -> None:
        self._api_key = _resolve_api_key(override=api_key)
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

    # ── core fetch ────────────────────────────────────────────────────────

    async def get_statistic(
        self,
        stat_code: str,
        item_code: str,
        period_start: date,
        period_end: date,
        *,
        cycle: str = "D",
        max_rows: int = _MAX_ROWS_PER_CALL,
    ) -> EcosSeries:
        # WHY: ECOS series endpoint format —
        # /api/StatisticSearch/{KEY}/json/kr/{start}/{count}/{stat_code}/{cycle}/
        #   {period_start}/{period_end}/{item_code}
        await self._throttle.acquire()
        ps = _fmt_period(period_start, cycle)
        pe = _fmt_period(period_end, cycle)
        path = (
            f"{_BASE}/StatisticSearch/{self._api_key}/json/kr/1/{max_rows}/"
            f"{stat_code}/{cycle.upper()}/{ps}/{pe}/{item_code}"
        )
        rows = await self._get_rows(path)
        observations = tuple(_row_to_observation(r, stat_code, item_code) for r in rows)
        observations = tuple(o for o in observations if o is not None)
        series = EcosSeries(
            stat_code=stat_code, item_code=item_code, cycle=cycle.upper(),
            observations=observations,
        )
        self._record_snapshot(
            tool="ecos.StatisticSearch",
            uaid=f"ECOS.{stat_code}.{item_code}",
            edge_type="macro_series",
            ts=datetime.now(tz=UTC),
            params={
                "stat_code": stat_code, "item_code": item_code, "cycle": cycle.upper(),
                "period_start": ps, "period_end": pe,
            },
            payload={
                "stat_code": stat_code, "item_code": item_code,
                "n_obs": len(observations),
                "first_period": observations[0].period if observations else "",
                "last_period": observations[-1].period if observations else "",
            },
        )
        return series

    # ── named convenience methods ─────────────────────────────────────────

    async def get_base_rate(self, start: date, end: date) -> EcosSeries:
        sc, ic, cycle = STAT_BASE_RATE
        return await self.get_statistic(sc, ic, start, end, cycle=cycle)

    async def get_krw_usd(self, start: date, end: date) -> EcosSeries:
        sc, ic, cycle = STAT_KRW_USD
        return await self.get_statistic(sc, ic, start, end, cycle=cycle)

    async def get_cpi(self, start: date, end: date) -> EcosSeries:
        sc, ic, cycle = STAT_CPI
        return await self.get_statistic(sc, ic, start, end, cycle=cycle)

    async def get_fx_reserves(self, start: date, end: date) -> EcosSeries:
        sc, ic, cycle = STAT_FX_RESERVES
        return await self.get_statistic(sc, ic, start, end, cycle=cycle)

    async def get_kospi_index(self, start: date, end: date) -> EcosSeries:
        sc, ic, cycle = STAT_KOSPI
        return await self.get_statistic(sc, ic, start, end, cycle=cycle)

    # ── http + snapshot helpers ──────────────────────────────────────────

    async def _get_rows(self, url: str) -> list[dict[str, Any]]:
        try:
            resp = await self._client.get(url)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise EcosApiError(f"ECOS GET failed url={url}: {exc}") from exc
        try:
            data = resp.json()
        except ValueError as exc:
            raise EcosApiError(f"ECOS non-JSON response from {url}: {exc}") from exc
        # Two response shapes:
        #   {"StatisticSearch": {"list_total_count": N, "row": [...]}}
        # or error:
        #   {"RESULT": {"CODE": "INFO-100", "MESSAGE": "..."}}
        if "RESULT" in data:
            result = data["RESULT"]
            code = str(result.get("CODE", "?"))
            msg = result.get("MESSAGE", "unknown")
            # INFO-200 = "no data" — treat as empty, not error.
            if code == "INFO-200":
                return []
            raise EcosApiError(f"ECOS API error code={code} message={msg}")
        block = data.get("StatisticSearch")
        if not isinstance(block, dict):
            raise EcosApiError(f"ECOS unexpected payload shape: keys={list(data)}")
        rows = block.get("row")
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
            log.warning("ecos.snapshot_failed", uaid=uaid, err=str(exc))


def _row_to_observation(
    row: Mapping[str, Any], stat_code: str, item_code: str,
) -> EcosObservation | None:
    if not isinstance(row, Mapping):
        return None
    period = str(row.get("TIME", "")).strip()
    if not period:
        return None
    value = _parse_value(str(row.get("DATA_VALUE", "")))
    unit = str(row.get("UNIT_NAME", "")).strip()
    return EcosObservation(
        stat_code=str(row.get("STAT_CODE", stat_code)),
        item_code=str(row.get("ITEM_CODE1", item_code)),
        period=period, value=value, unit=unit,
        ts_fetched=datetime.now(tz=UTC),
    )


__all__ = [
    "STAT_BASE_RATE",
    "STAT_CPI",
    "STAT_FX_RESERVES",
    "STAT_KOSPI",
    "STAT_KRW_USD",
    "EcosApiError",
    "EcosApiKeyMissingError",
    "EcosClient",
    "is_ecos_configured",
]
