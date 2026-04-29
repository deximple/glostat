from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import structlog

# CCXT-backed Binance perpetual futures client (Phase 1D Thesis E7 — funding carry).
# Public REST only — no auth required for historical funding/OHLCV.
# Self-throttle: 5 req/sec to stay well below Binance public limit (~20/s).

log: Final = structlog.get_logger(__name__)

_RATE_LIMIT_PER_SEC: Final[int] = 5
_MIN_INTERVAL_S: Final[float] = 1.0 / _RATE_LIMIT_PER_SEC

_DEFAULT_CACHE_DIR: Final[Path] = Path("cache") / "ccxt"


class CcxtUnavailableError(NotImplementedError):
    """Raised when ccxt package isn't installed."""


class CcxtDataError(RuntimeError):
    """Raised on empty/malformed ccxt response."""


@dataclass(frozen=True, slots=True)
class FundingRateBar:
    ts: datetime           # 8h interval timestamp UTC
    funding_rate: float    # decimal e.g. 0.0001 = 0.01%
    mark_price: float | None = None


@dataclass(frozen=True, slots=True)
class FundingRateSeries:
    symbol: str
    bars: tuple[FundingRateBar, ...]

    def __len__(self) -> int:
        return len(self.bars)


@dataclass(frozen=True, slots=True)
class CcxtOhlcvBar:
    ts: datetime           # bar open time UTC
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class CcxtOhlcvSeries:
    symbol: str
    timeframe: str
    bars: tuple[CcxtOhlcvBar, ...]

    def __len__(self) -> int:
        return len(self.bars)


class _Throttle:
    def __init__(self, *, rate_per_sec: int = _RATE_LIMIT_PER_SEC) -> None:
        self._sem = asyncio.Semaphore(rate_per_sec)
        self._lock = asyncio.Lock()
        self._next_slot: float = 0.0

    async def acquire(self) -> None:
        await self._sem.acquire()
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + _MIN_INTERVAL_S
        if wait > 0:
            await asyncio.sleep(wait)

    def release(self) -> None:
        self._sem.release()


def _import_ccxt() -> Any:
    try:
        import ccxt.async_support as ccxt_async  # noqa: PLC0415
    except ImportError as exc:
        raise CcxtUnavailableError(
            "ccxt not installed. Install with: pip install ccxt>=4.5"
        ) from exc
    return ccxt_async


class CcxtBinanceClient:
    def __init__(self, *, cache_dir: Path | None = None) -> None:
        self._throttle = _Throttle()
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._exchange: Any = None

    async def __aenter__(self) -> CcxtBinanceClient:
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    def _ex(self) -> Any:
        if self._exchange is None:
            ccxt = _import_ccxt()
            self._exchange = ccxt.binanceusdm({
                "enableRateLimit": True,
                "options": {"defaultType": "swap"},
            })
        return self._exchange

    async def close(self) -> None:
        if self._exchange is not None:
            try:
                await self._exchange.close()
            except Exception as exc:
                log.warning("ccxt.close_failed", err=str(exc))
            self._exchange = None

    async def fetch_funding_history(
        self,
        symbol: str,
        *,
        since_ms: int | None = None,
        limit: int = 1000,
    ) -> list[FundingRateBar]:
        await self._throttle.acquire()
        try:
            ex = self._ex()
            raw = await ex.fetch_funding_rate_history(symbol, since=since_ms, limit=limit)
        except Exception as exc:
            raise CcxtDataError(f"funding_history fetch failed for {symbol}: {exc}") from exc
        finally:
            self._throttle.release()
        bars: list[FundingRateBar] = []
        for r in raw or []:
            ts_ms = r.get("timestamp")
            rate = r.get("fundingRate")
            if ts_ms is None or rate is None:
                continue
            bars.append(FundingRateBar(
                ts=datetime.fromtimestamp(ts_ms / 1000, tz=UTC),
                funding_rate=float(rate),
                mark_price=_safe_float(r.get("markPrice") or r.get("info", {}).get("markPrice")),
            ))
        return bars

    async def fetch_funding_history_paginated(
        self,
        symbol: str,
        *,
        since_ms: int,
        until_ms: int,
        page_limit: int = 1000,
    ) -> list[FundingRateBar]:
        # Binance returns up to 1000 entries per page (8h cadence → ~333 days/page).
        # Walk forward until we pass until_ms or get an empty page.
        out: list[FundingRateBar] = []
        cursor = since_ms
        last_ts: int | None = None
        empty_pages = 0
        while cursor < until_ms and empty_pages < 2:
            page = await self.fetch_funding_history(symbol, since_ms=cursor, limit=page_limit)
            if not page:
                empty_pages += 1
                cursor += 8 * 3600 * 1000 * page_limit
                continue
            empty_pages = 0
            for bar in page:
                bar_ms = int(bar.ts.timestamp() * 1000)
                if last_ts is not None and bar_ms <= last_ts:
                    continue
                if bar_ms > until_ms:
                    return out
                out.append(bar)
                last_ts = bar_ms
            # advance cursor past the latest bar received
            if last_ts is None or last_ts <= cursor:
                cursor += 8 * 3600 * 1000  # nudge forward
            else:
                cursor = last_ts + 1
            await asyncio.sleep(0.1)  # gentle pacing on top of throttle
        return out

    async def fetch_ohlcv(
        self,
        symbol: str,
        *,
        timeframe: str = "8h",
        since_ms: int | None = None,
        limit: int = 1000,
    ) -> list[CcxtOhlcvBar]:
        await self._throttle.acquire()
        try:
            ex = self._ex()
            raw = await ex.fetch_ohlcv(symbol, timeframe=timeframe, since=since_ms, limit=limit)
        except Exception as exc:
            raise CcxtDataError(f"ohlcv fetch failed for {symbol}: {exc}") from exc
        finally:
            self._throttle.release()
        bars: list[CcxtOhlcvBar] = []
        for row in raw or []:
            if len(row) < 6:
                continue
            ts_ms, o, h, l, c, v = row[:6]
            bars.append(CcxtOhlcvBar(
                ts=datetime.fromtimestamp(int(ts_ms) / 1000, tz=UTC),
                open=float(o), high=float(h), low=float(l), close=float(c),
                volume=float(v),
            ))
        return bars

    async def fetch_ohlcv_paginated(
        self,
        symbol: str,
        *,
        timeframe: str = "8h",
        since_ms: int,
        until_ms: int,
        page_limit: int = 1000,
    ) -> list[CcxtOhlcvBar]:
        bar_ms = _timeframe_ms(timeframe)
        out: list[CcxtOhlcvBar] = []
        cursor = since_ms
        last_ts: int | None = None
        empty_pages = 0
        while cursor < until_ms and empty_pages < 2:
            page = await self.fetch_ohlcv(
                symbol, timeframe=timeframe, since_ms=cursor, limit=page_limit
            )
            if not page:
                empty_pages += 1
                cursor += bar_ms * page_limit
                continue
            empty_pages = 0
            for bar in page:
                bar_t = int(bar.ts.timestamp() * 1000)
                if last_ts is not None and bar_t <= last_ts:
                    continue
                if bar_t > until_ms:
                    return out
                out.append(bar)
                last_ts = bar_t
            if last_ts is None or last_ts <= cursor:
                cursor += bar_ms
            else:
                cursor = last_ts + 1
            await asyncio.sleep(0.1)
        return out


def _timeframe_ms(tf: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000}
    if tf and tf[-1] in units and tf[:-1].isdigit():
        return int(tf[:-1]) * units[tf[-1]]
    return 8 * 3_600_000  # default 8h


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


__all__ = [
    "CcxtBinanceClient",
    "CcxtDataError",
    "CcxtOhlcvBar",
    "CcxtOhlcvSeries",
    "CcxtUnavailableError",
    "FundingRateBar",
    "FundingRateSeries",
]
