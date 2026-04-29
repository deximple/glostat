from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Final

import structlog

from glostat.data.yfinance_client import (
    YFinanceClient,
    YFinanceDataError,
    YFinanceUnavailableError,
)
from glostat.data.yfinance_types import OhlcvSeries

# Lightweight per-ticker OHLCV cache for Phase 1B hindcasts. Avoids hammering
# yfinance on every (ticker, date) tuple and lets the 4 thesis hindcasts share
# one fetch pass.

log: Final = structlog.get_logger(__name__)

_CACHE_DIR_DEFAULT: Final[Path] = Path("cache") / "phase1b" / "ohlcv"
_PADDING_DAYS: Final[int] = 7


class PriceCache:
    def __init__(
        self,
        *,
        client: YFinanceClient,
        start: date,
        end: date,
        cache_dir: Path | None = None,
    ) -> None:
        self._client = client
        self._start = start - timedelta(days=_PADDING_DAYS)
        self._end = end + timedelta(days=_PADDING_DAYS)
        self._dir = cache_dir or _CACHE_DIR_DEFAULT
        self._dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, OhlcvSeries] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.fetch_count: int = 0
        self.cache_hit_count: int = 0
        self.miss_count: int = 0

    def _path(self, ticker: str) -> Path:
        return self._dir / f"{ticker.upper()}_{self._start.isoformat()}_{self._end.isoformat()}.json"

    def _lock(self, ticker: str) -> asyncio.Lock:
        key = ticker.upper()
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock

    async def get(self, ticker: str) -> OhlcvSeries | None:
        ticker_u = ticker.upper().strip()
        if ticker_u in self._mem:
            self.cache_hit_count += 1
            return self._mem[ticker_u]
        async with self._lock(ticker_u):
            if ticker_u in self._mem:
                self.cache_hit_count += 1
                return self._mem[ticker_u]
            disk = _load_from_disk(self._path(ticker_u))
            if disk is not None:
                self._mem[ticker_u] = disk
                self.cache_hit_count += 1
                return disk
            try:
                series = await self._client.get_ohlcv(
                    ticker_u, start=self._start, end=self._end
                )
            except (YFinanceUnavailableError, YFinanceDataError) as exc:
                log.warning(
                    "price_cache.fetch_failed",
                    ticker=ticker_u, err=str(exc),
                )
                self.miss_count += 1
                return None
            except Exception as exc:
                log.warning(
                    "price_cache.unexpected",
                    ticker=ticker_u, err=str(exc),
                )
                self.miss_count += 1
                return None
            self._mem[ticker_u] = series
            _save_to_disk(self._path(ticker_u), series)
            self.fetch_count += 1
            return series

    def close_at_or_before(self, ticker: str, day: date) -> float | None:
        ticker_u = ticker.upper().strip()
        series = self._mem.get(ticker_u)
        if series is None:
            return None
        best: float | None = None
        best_day: date | None = None
        for bar in series.bars:
            bar_day = bar.ts.date() if hasattr(bar.ts, "date") else bar.ts
            if not isinstance(bar_day, date):
                continue
            if bar_day > day:
                continue
            if best_day is None or bar_day > best_day:
                best_day = bar_day
                best = float(bar.close)
        return best

    def forward_return(
        self, ticker: str, day: date, horizon_days: int = 30
    ) -> float | None:
        c0 = self.close_at_or_before(ticker, day)
        c1 = self.close_at_or_before(ticker, day + timedelta(days=horizon_days))
        if c0 is None or c1 is None or c0 <= 0:
            return None
        return (c1 - c0) / c0

    def trading_days(self, ticker: str) -> list[date]:
        ticker_u = ticker.upper().strip()
        series = self._mem.get(ticker_u)
        if series is None:
            return []
        days: list[date] = []
        for bar in series.bars:
            bar_day = bar.ts.date() if hasattr(bar.ts, "date") else bar.ts
            if isinstance(bar_day, date):
                days.append(bar_day)
        return sorted(set(days))


def _load_from_disk(path: Path) -> OhlcvSeries | None:
    if not path.exists():
        return None
    try:
        from datetime import datetime as _dt  # noqa: PLC0415

        from glostat.data.yfinance_types import OhlcvBar  # noqa: PLC0415

        raw = json.loads(path.read_text())
        bars = tuple(
            OhlcvBar(
                ts=_dt.fromisoformat(b["ts"]),
                open=float(b["open"]),
                high=float(b["high"]),
                low=float(b["low"]),
                close=float(b["close"]),
                volume=int(b["volume"]),
                adj_close=float(b.get("adj_close") or b["close"]),
            )
            for b in raw["bars"]
        )
        return OhlcvSeries(
            ticker=str(raw["ticker"]),
            bars=bars,
            interval=str(raw.get("interval", "1d")),
        )
    except Exception as exc:
        log.warning("price_cache.load_failed", path=str(path), err=str(exc))
        return None


def _save_to_disk(path: Path, series: OhlcvSeries) -> None:
    try:
        payload = {
            "ticker": series.ticker,
            "interval": series.interval,
            "bars": [
                {
                    "ts": b.ts.isoformat(),
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "adj_close": b.adj_close,
                }
                for b in series.bars
            ],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")))
        tmp.replace(path)
    except OSError as exc:
        log.warning("price_cache.save_failed", path=str(path), err=str(exc))


__all__ = ["PriceCache"]
