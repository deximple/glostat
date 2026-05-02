from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Final

import structlog

from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey
from glostat.data.yfinance_client import YFinanceClient
from glostat.data.yfinance_types import OhlcvBar, OhlcvSeries

if TYPE_CHECKING:
    pass

# v1.5 P6 — Commodity-cycle data client.
# Pure read wrapper around yfinance commodity futures so cyclical-sector
# experts (E_FUNDAMENTAL_KR_CYCLICAL, E_COMMODITY_INDEX_KR) can score against
# percentile-of-cycle without re-implementing OHLCV plumbing. Snapshot broker
# integration is mandatory (INV-GS-022).
#
# Sources (all yfinance, free):
#   - WTI crude        → CL=F
#   - Brent            → BZ=F
#   - Gasoline (RBOB)  → RB=F   (used to compute crack spread vs WTI)
#   - Iron ore 62% Fe  → TIO=F
#   - Copper           → HG=F
#   - Dry bulk index   → BDRY (ETF proxy — Breakwave Dry Bulk)
#
# All commodities are dollar-denominated US futures so the same fetch path
# works for every cycle indicator. KR-specific exchange data is intentionally
# avoided here — yfinance's free coverage is the v1.5 baseline.

log: Final = structlog.get_logger(__name__)

_PCTILE_LOOKBACK_DAYS: Final[int] = 730   # ~2 years of daily bars
_MOMENTUM_LOOKBACK_DAYS: Final[int] = 30
_CACHE_TTL_HOURS: Final[float] = 6.0


class CommodityKey(StrEnum):
    WTI       = "WTI"
    BRENT     = "BRENT"
    GASOLINE  = "GASOLINE"
    IRON_ORE  = "IRON_ORE"
    COPPER    = "COPPER"
    DRY_BULK  = "DRY_BULK"


_YFINANCE_TICKER: Final[dict[CommodityKey, str]] = {
    CommodityKey.WTI:      "CL=F",
    CommodityKey.BRENT:    "BZ=F",
    CommodityKey.GASOLINE: "RB=F",
    CommodityKey.IRON_ORE: "TIO=F",
    CommodityKey.COPPER:   "HG=F",
    CommodityKey.DRY_BULK: "BDRY",
}


@dataclass(frozen=True, slots=True)
class CommodityCycle:
    key: CommodityKey
    last_close: float
    cycle_percentile: float       # [0, 1] — where last_close sits in 730d distribution
    momentum_30d: float           # (last - 30d_ago) / 30d_ago
    snapshot_id: str | None = None
    n_observations: int = 0

    @property
    def cycle_position(self) -> str:
        # Coarse label for human readers / metadata.
        if self.cycle_percentile >= 0.75:
            return "high"
        if self.cycle_percentile >= 0.50:
            return "mid_high"
        if self.cycle_percentile >= 0.25:
            return "mid_low"
        return "low"


@dataclass(frozen=True, slots=True)
class CrackSpread:
    # WHY: gasoline crack spread is the textbook refining margin proxy.
    #   spread_bbl = 42 * gasoline_$_per_gal - wti_$_per_bbl
    # 42 is the bbl→gallon conversion. Negative spread = refiners losing money.
    last_spread: float
    cycle_percentile: float
    momentum_30d: float
    n_observations: int = 0


class CommodityClient:
    """Fetch commodity OHLCV via yfinance and compute cycle metrics.

    Uses a per-process cache (TTL = 6h) so multiple experts in one prediction
    call share a single round-trip. Snapshot broker writes are honoured
    (INV-GS-022) because the underlying yfinance client already records them.
    """

    def __init__(
        self,
        *,
        yfinance_client: YFinanceClient,
        snapshot_broker: SnapshotBroker | None = None,
    ) -> None:
        self._yf = yfinance_client
        self._broker = snapshot_broker
        self._cache: dict[CommodityKey, tuple[datetime, OhlcvSeries]] = {}

    async def get_cycle(
        self,
        key: CommodityKey,
        *,
        as_of: date | None = None,
    ) -> CommodityCycle:
        series = await self._fetch_series(key, as_of=as_of)
        bars = series.bars
        if not bars:
            raise CommodityDataError(f"empty OHLCV series for {key.value}")
        closes = tuple(b.close for b in bars if b.close is not None)
        if not closes:
            raise CommodityDataError(f"no close prices for {key.value}")
        last = closes[-1]
        pctile = _percentile_rank(closes, last)
        momentum = _momentum(closes, _MOMENTUM_LOOKBACK_DAYS)
        return CommodityCycle(
            key=key,
            last_close=last,
            cycle_percentile=pctile,
            momentum_30d=momentum,
            snapshot_id=getattr(self._yf, "last_snapshot_id", None),
            n_observations=len(closes),
        )

    async def get_crack_spread(
        self, *, as_of: date | None = None,
    ) -> CrackSpread:
        wti, gasoline = await asyncio.gather(
            self._fetch_series(CommodityKey.WTI, as_of=as_of),
            self._fetch_series(CommodityKey.GASOLINE, as_of=as_of),
        )
        spreads = _aligned_crack_spreads(wti, gasoline)
        if not spreads:
            raise CommodityDataError(
                "crack spread: no aligned WTI + gasoline closes"
            )
        last_spread = spreads[-1]
        pctile = _percentile_rank(spreads, last_spread)
        momentum = _momentum(spreads, _MOMENTUM_LOOKBACK_DAYS)
        return CrackSpread(
            last_spread=last_spread,
            cycle_percentile=pctile,
            momentum_30d=momentum,
            n_observations=len(spreads),
        )

    async def _fetch_series(
        self, key: CommodityKey, *, as_of: date | None = None,
    ) -> OhlcvSeries:
        cached = self._cache.get(key)
        now = datetime.now(tz=UTC)
        if cached is not None and (now - cached[0]) < timedelta(hours=_CACHE_TTL_HOURS):
            return cached[1]
        end = as_of or now.date()
        start = end - timedelta(days=_PCTILE_LOOKBACK_DAYS)
        ticker = _YFINANCE_TICKER[key]
        try:
            series = await self._yf.get_ohlcv(ticker, start=start, end=end, interval="1d")
        except Exception as exc:
            log.warning(
                "commodity_client.fetch_failed",
                key=key.value, ticker=ticker, err=str(exc),
            )
            raise CommodityDataError(
                f"yfinance fetch failed for {key.value} ({ticker}): {exc}"
            ) from exc
        self._cache[key] = (now, series)
        self._record_snapshot(key, series, ticker)
        return series

    def _record_snapshot(
        self, key: CommodityKey, series: OhlcvSeries, ticker: str,
    ) -> None:
        if self._broker is None:
            return
        last_bar = series.bars[-1] if series.bars else None
        if last_bar is None:
            return
        ts = last_bar.ts if last_bar.ts.tzinfo else last_bar.ts.replace(tzinfo=UTC)
        snap_key = SnapshotKey(
            uaid=f"COMMODITY.{key.value}",
            edge_type="commodity_cycle",
            ts_utc=ts,
            tool="commodity_client.cycle",
            params_canon=f'{{"ticker":"{ticker}","lookback_days":{_PCTILE_LOOKBACK_DAYS}}}',
        )
        try:
            self._broker.save_snapshot(
                snap_key,
                {
                    "ticker": ticker,
                    "last_close": float(last_bar.close) if last_bar.close is not None else 0.0,
                    "n_bars": len(series.bars),
                    "first_ts": series.bars[0].ts.isoformat(),
                    "last_ts": last_bar.ts.isoformat(),
                },
            )
        except Exception as exc:
            log.info("commodity_client.snapshot_skip", err=str(exc))


class CommodityDataError(RuntimeError):
    pass


def _percentile_rank(values: tuple[float, ...], target: float) -> float:
    # Empirical CDF: fraction of observations strictly less than target.
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


def _aligned_crack_spreads(
    wti: OhlcvSeries, gasoline: OhlcvSeries,
) -> tuple[float, ...]:
    # WHY: yfinance returns daily bars indexed by date; we align by date so
    # missing days on one leg don't pull stale prices into the spread.
    wti_by_date = _close_by_date(wti.bars)
    gas_by_date = _close_by_date(gasoline.bars)
    common = sorted(set(wti_by_date.keys()) & set(gas_by_date.keys()))
    out: list[float] = []
    for d in common:
        w = wti_by_date[d]
        g = gas_by_date[d]
        if w is None or g is None:
            continue
        # 42 gallons per barrel; gasoline price is $/gal, WTI is $/bbl.
        spread = 42.0 * g - w
        out.append(spread)
    return tuple(out)


def _close_by_date(bars: tuple[OhlcvBar, ...]) -> dict[date, float | None]:
    return {b.ts.date(): b.close for b in bars}


__all__ = [
    "CommodityClient",
    "CommodityCycle",
    "CommodityDataError",
    "CommodityKey",
    "CrackSpread",
]
