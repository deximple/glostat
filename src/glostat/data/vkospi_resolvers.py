from __future__ import annotations

from datetime import date, timedelta
from typing import Final

import structlog

from glostat.data.data_router import to_yfinance_kr_ticker
from glostat.data.yfinance_client import YFinanceClient
from glostat.experts.e_vkospi_mood_kr import (
    ReturnResolver,
    SmallCapResolver,
)

# v1.10.8 — concrete resolvers wired against YFinanceClient for the
# E_VKOSPI_MOOD_KR expert + phase_kr_vkospi_mood_hindcast harness.
#
# Two resolvers, one yfinance dependency:
#
# 1. YFinanceReturnResolver
#    Fetches daily OHLCV for the KR ticker (`.KS` suffixed via
#    to_yfinance_kr_ticker) over a bounded lookback window, finds the
#    most-recent close on/before `as_of`, computes daily simple return
#    against the prior trading-day close. Returns None on missing data
#    so the expert can skip cleanly.
#
# 2. KospiSmallCapResolver
#    Pulls market_cap from yfinance Fundamentals for the KR ticker.
#    Compares against a configurable threshold (default 5T KRW ≈ ~$3.6B)
#    that approximates the KOSPI 200 lower-third boundary. Cached per
#    process (market caps move slowly relative to the per-day hindcast
#    iteration).
#
# Design choices:
#   - Both resolvers degrade gracefully on yfinance failure (return None
#     / False rather than raising) so the expert + hindcast can proceed
#     with reduced inputs. This matches the pattern in commodity_client.
#   - The small-cap threshold is tunable via constructor, default
#     5_000_000_000_000.0 KRW (5T). Lee/Son/Lee 2024 used quartile
#     binning; 5T KRW threshold approximates the bottom 30-40% of
#     KOSPI 200 by market cap as of 2026.
#   - No live API calls in tests — both resolvers accept a YFinanceClient
#     instance and tests inject a fake.

log: Final = structlog.get_logger(__name__)

_DEFAULT_RETURN_LOOKBACK_DAYS: Final[int] = 14
_DEFAULT_SMALL_CAP_THRESHOLD_KRW: Final[float] = 5_000_000_000_000.0  # 5T KRW
_FUNDAMENTALS_CACHE_TTL_DAYS: Final[int] = 30


class YFinanceReturnResolver(ReturnResolver):
    """Fetch the most-recent daily simple return for a KR ticker.

    Caches per-(code, as_of) results to avoid duplicate yfinance pulls
    when the hindcast iterates closely-spaced sample days.
    """

    def __init__(
        self,
        *,
        yf_client: YFinanceClient,
        lookback_days: int = _DEFAULT_RETURN_LOOKBACK_DAYS,
    ) -> None:
        if lookback_days < 2:
            raise ValueError(
                f"lookback_days must be >= 2, got {lookback_days}"
            )
        self._yf = yf_client
        self._lookback = lookback_days
        self._cache: dict[tuple[str, date], float | None] = {}

    async def get_recent_daily_return(
        self, code: str, as_of: date,
    ) -> float | None:
        cache_key = (code, as_of)
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = await self._fetch_return(code, as_of)
        self._cache[cache_key] = result
        return result

    async def _fetch_return(
        self, code: str, as_of: date,
    ) -> float | None:
        yf_ticker = to_yfinance_kr_ticker(code)
        start = as_of - timedelta(days=self._lookback)
        end = as_of + timedelta(days=1)
        try:
            series = await self._yf.get_ohlcv(yf_ticker, start=start, end=end)
        except Exception as exc:
            log.info(
                "vkospi_return.fetch_failed",
                code=code, as_of=as_of.isoformat(), err=str(exc),
            )
            return None
        if not series.bars or len(series.bars) < 2:
            return None
        # Pick the most-recent bar on/before as_of and the bar before it.
        on_or_before: list[tuple[date, float]] = []
        for bar in series.bars:
            bar_date = bar.ts.date() if hasattr(bar.ts, "date") else bar.ts
            if not isinstance(bar_date, date):
                continue
            if bar_date > as_of:
                continue
            close = bar.close
            if close is None or close <= 0:
                continue
            on_or_before.append((bar_date, float(close)))
        if len(on_or_before) < 2:
            return None
        on_or_before.sort(key=lambda x: x[0])
        prev_close = on_or_before[-2][1]
        latest_close = on_or_before[-1][1]
        if prev_close <= 0:
            return None
        return (latest_close - prev_close) / prev_close


class KospiSmallCapResolver(SmallCapResolver):
    """Classify a KR ticker as small-cap based on market_cap threshold.

    Default threshold = 5T KRW (≈ $3.6B), approximating the bottom 30-40%
    of KOSPI 200 by market cap. Lee/Son/Lee 2024 small-cap effect uses
    quartile binning; this single threshold is a conservative proxy that
    captures the same qualitative split without requiring a quartile-
    snapshot dataset.

    Cached per-code. Market cap moves slowly enough that a per-process
    cache holds for the lifetime of a hindcast run.
    """

    def __init__(
        self,
        *,
        yf_client: YFinanceClient,
        threshold_krw: float = _DEFAULT_SMALL_CAP_THRESHOLD_KRW,
    ) -> None:
        if threshold_krw <= 0:
            raise ValueError(
                f"threshold_krw must be > 0, got {threshold_krw}"
            )
        self._yf = yf_client
        self._threshold = threshold_krw
        self._cache: dict[str, bool] = {}

    @property
    def threshold_krw(self) -> float:
        return self._threshold

    async def is_small_cap(self, code: str, as_of: date) -> bool:  # noqa: ARG002
        # `as_of` is part of the SmallCapResolver protocol; market caps move
        # slowly enough that a per-process cache keyed only on `code` is
        # sufficient. A future snapshot-aware variant could key on (code, as_of).
        if code in self._cache:
            return self._cache[code]
        result = await self._fetch_small_cap(code)
        self._cache[code] = result
        return result

    async def _fetch_small_cap(self, code: str) -> bool:
        yf_ticker = to_yfinance_kr_ticker(code)
        try:
            fundamentals = await self._yf.get_fundamentals(yf_ticker)
        except Exception as exc:
            log.info(
                "vkospi_small_cap.fetch_failed",
                code=code, err=str(exc),
            )
            # WHY: graceful default = treat as large-cap (no multiplier
            # applied) when fundamentals are missing. Avoids over-stating
            # the small-cap effect on data-error paths.
            return False
        market_cap = fundamentals.market_cap
        if market_cap is None or market_cap <= 0:
            return False
        return market_cap < self._threshold


__all__ = [
    "KospiSmallCapResolver",
    "YFinanceReturnResolver",
]
