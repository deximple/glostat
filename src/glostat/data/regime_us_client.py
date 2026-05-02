from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Final

import structlog

from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey
from glostat.data.yfinance_client import YFinanceClient
from glostat.data.yfinance_types import OhlcvBar, OhlcvSeries

# v1.10 Data/Signal — US regime client (VIX term structure + UST yield curve).
#
# WHY: GLOSTAT calibration table has E_MACRO_KR (ECOS-backed BoK rate / KRW /
# CPI / KOSPI) but no US peer. The note in e_macro_kr.py:25 explicitly says
# the US analog "FRED-backed, deferred Phase 2" was never built. This client
# closes that asymmetry without taking a paid-data dep — every series here is
# free via yfinance.
#
# Series (all yfinance, free, no API key):
#   - ^VIX        spot 30d implied vol on SPX
#   - ^VIX9D      9-day SPX implied vol  (front of term curve)
#   - ^VIX3M      3-month SPX implied vol (back of term curve)
#   - ^IRX        13-week T-bill yield   (front of UST curve)
#   - ^FVX        5-year UST yield
#   - ^TNX        10-year UST yield
#   - ^TYX        30-year UST yield
#
# Two derived metrics:
#   - vix_term_ratio = VIX9D / VIX3M
#       <  1.0  → contango (calm regime, vol mean-reverting upward)
#       >= 1.0  → backwardation (stress regime, near-term > far-term vol)
#   - curve_slope_2y10y_bps = (TNX - IRX) * 100  (basis points)
#       Negative = inverted curve → recession signal
#       Positive = steepening → recovery / late-cycle
#
# Design mirrors commodity_client.py:
#   - Per-process cache with TTL
#   - Point-in-time `as_of` slicing — cache covers the full lookback window
#   - Snapshot broker writes mandatory (INV-GS-022)
#   - Hindcast helper `prefetch()` for one-shot bulk fetch

log: Final = structlog.get_logger(__name__)

_LOOKBACK_DAYS: Final[int] = 730       # ~2 years of daily bars for percentile baseline
_MOMENTUM_LOOKBACK_DAYS: Final[int] = 30
_CACHE_TTL_HOURS: Final[float] = 6.0


class RegimeKey(StrEnum):
    VIX        = "VIX"
    VIX9D      = "VIX9D"
    VIX3M      = "VIX3M"
    UST_3M     = "UST_3M"
    UST_5Y     = "UST_5Y"
    UST_10Y    = "UST_10Y"
    UST_30Y    = "UST_30Y"


_YFINANCE_TICKER: Final[dict[RegimeKey, str]] = {
    RegimeKey.VIX:     "^VIX",
    RegimeKey.VIX9D:   "^VIX9D",
    RegimeKey.VIX3M:   "^VIX3M",
    RegimeKey.UST_3M:  "^IRX",
    RegimeKey.UST_5Y:  "^FVX",
    RegimeKey.UST_10Y: "^TNX",
    RegimeKey.UST_30Y: "^TYX",
}


@dataclass(frozen=True, slots=True)
class RegimeLevel:
    key: RegimeKey
    last_close: float
    cycle_percentile: float       # [0, 1] vs trailing 730d
    momentum_30d: float           # (last - 30d_ago) / 30d_ago
    snapshot_id: str | None = None
    n_observations: int = 0


@dataclass(frozen=True, slots=True)
class VixTermStructure:
    # WHY: contango/backwardation of the SPX vol term curve is a textbook
    # regime indicator. ratio < 1.0 (VIX9D < VIX3M) → calm. ratio >= 1.0 →
    # near-term stress dominates.
    vix9d: float
    vix3m: float
    ratio: float
    in_backwardation: bool
    n_observations: int = 0


@dataclass(frozen=True, slots=True)
class UstCurveSlope:
    # WHY: the 2y10y curve is the most-cited recession indicator. We use the
    # 3m-10y proxy (^IRX vs ^TNX) because yfinance does not expose the 2y
    # constant-maturity series for free under a stable symbol.
    front_yield_pct: float        # ^IRX (13-week T-bill)
    back_yield_pct: float         # ^TNX (10-year)
    slope_bps: float              # (back - front) * 100
    inverted: bool
    n_observations: int = 0


class RegimeDataError(RuntimeError):
    pass


class RegimeUsClient:
    """Fetch US regime indicators (VIX term + UST curve) via yfinance.

    Mirrors CommodityClient: per-process cache with point-in-time `as_of`
    slicing so hindcast callers reuse the same fetch across sample days.
    """

    def __init__(
        self,
        *,
        yfinance_client: YFinanceClient,
        snapshot_broker: SnapshotBroker | None = None,
    ) -> None:
        self._yf = yfinance_client
        self._broker = snapshot_broker
        self._cache: dict[RegimeKey, tuple[datetime, OhlcvSeries]] = {}

    async def get_level(
        self, key: RegimeKey, *, as_of: date | None = None,
    ) -> RegimeLevel:
        series = await self._fetch_series(key, as_of=as_of)
        closes = _closes_on_or_before(series.bars, as_of)
        if not closes:
            raise RegimeDataError(
                f"no close prices for {key.value}"
                + (f" on or before {as_of.isoformat()}" if as_of else "")
            )
        last = closes[-1]
        pctile = _percentile_rank(closes, last)
        momentum = _momentum(closes, _MOMENTUM_LOOKBACK_DAYS)
        return RegimeLevel(
            key=key,
            last_close=last,
            cycle_percentile=pctile,
            momentum_30d=momentum,
            snapshot_id=getattr(self._yf, "last_snapshot_id", None),
            n_observations=len(closes),
        )

    async def get_vix_term(
        self, *, as_of: date | None = None,
    ) -> VixTermStructure:
        vix9d_series, vix3m_series = await asyncio.gather(
            self._fetch_series(RegimeKey.VIX9D, as_of=as_of),
            self._fetch_series(RegimeKey.VIX3M, as_of=as_of),
        )
        # WHY: align by date so a missing day on one leg doesn't pull a stale
        # value into the ratio.
        front = _close_by_date(_bars_on_or_before(vix9d_series.bars, as_of))
        back = _close_by_date(_bars_on_or_before(vix3m_series.bars, as_of))
        common = sorted(set(front.keys()) & set(back.keys()))
        if not common:
            raise RegimeDataError(
                "vix_term: no aligned VIX9D + VIX3M closes"
                + (f" on or before {as_of.isoformat()}" if as_of else "")
            )
        last_day = common[-1]
        front_v = front[last_day]
        back_v = back[last_day]
        if front_v is None or back_v is None or back_v <= 0:
            raise RegimeDataError(
                f"vix_term: invalid closes on {last_day.isoformat()}: "
                f"VIX9D={front_v}, VIX3M={back_v}"
            )
        ratio = front_v / back_v
        return VixTermStructure(
            vix9d=front_v,
            vix3m=back_v,
            ratio=ratio,
            in_backwardation=ratio >= 1.0,
            n_observations=len(common),
        )

    async def get_curve_slope(
        self, *, as_of: date | None = None,
    ) -> UstCurveSlope:
        front_series, back_series = await asyncio.gather(
            self._fetch_series(RegimeKey.UST_3M, as_of=as_of),
            self._fetch_series(RegimeKey.UST_10Y, as_of=as_of),
        )
        front = _close_by_date(_bars_on_or_before(front_series.bars, as_of))
        back = _close_by_date(_bars_on_or_before(back_series.bars, as_of))
        common = sorted(set(front.keys()) & set(back.keys()))
        if not common:
            raise RegimeDataError(
                "curve_slope: no aligned ^IRX + ^TNX closes"
                + (f" on or before {as_of.isoformat()}" if as_of else "")
            )
        last_day = common[-1]
        front_v = front[last_day]
        back_v = back[last_day]
        if front_v is None or back_v is None:
            raise RegimeDataError(
                f"curve_slope: missing yield on {last_day.isoformat()}"
            )
        slope = (back_v - front_v) * 100.0
        return UstCurveSlope(
            front_yield_pct=front_v,
            back_yield_pct=back_v,
            slope_bps=slope,
            inverted=slope < 0.0,
            n_observations=len(common),
        )

    async def prefetch(
        self,
        keys: tuple[RegimeKey, ...],
        *,
        earliest_as_of: date,
    ) -> None:
        # Hindcast helper: fetch each key once with a window covering all
        # planned sample days. Mirrors CommodityClient.prefetch.
        for k in keys:
            await self._fetch_series(k, as_of=earliest_as_of)

    async def _fetch_series(
        self, key: RegimeKey, *, as_of: date | None = None,
    ) -> OhlcvSeries:
        cached = self._cache.get(key)
        now = datetime.now(tz=UTC)
        if (
            cached is not None
            and (now - cached[0]) < timedelta(hours=_CACHE_TTL_HOURS)
            and _cache_covers_window(cached[1], as_of=as_of)
        ):
            return cached[1]
        end = now.date()
        anchor = as_of or end
        start = anchor - timedelta(days=_LOOKBACK_DAYS)
        ticker = _YFINANCE_TICKER[key]
        try:
            series = await self._yf.get_ohlcv(
                ticker, start=start, end=end, interval="1d",
            )
        except Exception as exc:
            log.warning(
                "regime_us_client.fetch_failed",
                key=key.value, ticker=ticker, err=str(exc),
            )
            raise RegimeDataError(
                f"yfinance fetch failed for {key.value} ({ticker}): {exc}"
            ) from exc
        self._cache[key] = (now, series)
        self._record_snapshot(key, series, ticker)
        return series

    def _record_snapshot(
        self, key: RegimeKey, series: OhlcvSeries, ticker: str,
    ) -> None:
        if self._broker is None:
            return
        last_bar = series.bars[-1] if series.bars else None
        if last_bar is None:
            return
        ts = last_bar.ts if last_bar.ts.tzinfo else last_bar.ts.replace(tzinfo=UTC)
        snap_key = SnapshotKey(
            uaid=f"REGIME_US.{key.value}",
            edge_type="regime_us_level",
            ts_utc=ts,
            tool="regime_us_client.level",
            params_canon=f'{{"ticker":"{ticker}","lookback_days":{_LOOKBACK_DAYS}}}',
        )
        try:
            self._broker.save_snapshot(
                snap_key,
                {
                    "ticker": ticker,
                    "last_close": float(last_bar.close)
                    if last_bar.close is not None else 0.0,
                    "n_bars": len(series.bars),
                    "first_ts": series.bars[0].ts.isoformat(),
                    "last_ts": last_bar.ts.isoformat(),
                },
            )
        except Exception as exc:
            log.info("regime_us_client.snapshot_skip", err=str(exc))


# ── pure helpers (testable without network) ────────────────────────────────


def _bars_on_or_before(
    bars: tuple[OhlcvBar, ...], as_of: date | None,
) -> list[OhlcvBar]:
    if as_of is None:
        return list(bars)
    return [b for b in bars if b.ts.date() <= as_of]


def _closes_on_or_before(
    bars: tuple[OhlcvBar, ...], as_of: date | None,
) -> tuple[float, ...]:
    out: list[float] = []
    for b in _bars_on_or_before(bars, as_of):
        if b.close is not None:
            out.append(b.close)
    return tuple(out)


def _cache_covers_window(series: OhlcvSeries, *, as_of: date | None) -> bool:
    if as_of is None:
        return True
    if not series.bars:
        return False
    first_bar_day = series.bars[0].ts.date()
    needed_start = as_of - timedelta(days=_LOOKBACK_DAYS)
    return first_bar_day <= needed_start


def _percentile_rank(values: tuple[float, ...], target: float) -> float:
    if not values:
        return 0.5
    below = sum(1 for v in values if v < target)
    return min(1.0, max(0.0, below / len(values)))


def _momentum(values: tuple[float, ...], lookback_days: int) -> float:
    if len(values) <= lookback_days:
        return 0.0
    earlier = values[-(lookback_days + 1)]
    if earlier == 0:
        return 0.0
    return (values[-1] - earlier) / abs(earlier)


def _close_by_date(bars: list[OhlcvBar]) -> dict[date, float | None]:
    return {b.ts.date(): b.close for b in bars}


__all__ = [
    "RegimeDataError",
    "RegimeKey",
    "RegimeLevel",
    "RegimeUsClient",
    "UstCurveSlope",
    "VixTermStructure",
    "_bars_on_or_before",
    "_cache_covers_window",
    "_closes_on_or_before",
    "_percentile_rank",
]
