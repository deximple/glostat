from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from glostat.data.commodity_client import (
    CommodityClient,
    CommodityCycle,
    CommodityDataError,
    CommodityKey,
    CrackSpread,
    _aligned_crack_spreads,
    _momentum,
    _percentile_rank,
)
from glostat.data.yfinance_types import OhlcvBar, OhlcvSeries

# v1.5 P6 — commodity_client tests. Pure-helper tests + a fake YFinance
# client integration to avoid hitting the live network.


# ── Pure helper tests ─────────────────────────────────────────────────────


class TestPercentileRank:
    def test_min_returns_zero(self) -> None:
        assert _percentile_rank((1.0, 2.0, 3.0), 0.5) == 0.0

    def test_max_returns_one(self) -> None:
        assert _percentile_rank((1.0, 2.0, 3.0), 5.0) == 1.0

    def test_median_returns_half(self) -> None:
        rank = _percentile_rank((1.0, 2.0, 3.0, 4.0), 2.5)
        assert 0.4 <= rank <= 0.6

    def test_empty_returns_half(self) -> None:
        assert _percentile_rank((), 1.0) == 0.5


class TestMomentum:
    def test_thirty_day_diff(self) -> None:
        # 30 day-old value = 100, today = 110 → 10% momentum.
        values = tuple([100.0] * 30 + [110.0])
        assert _momentum(values, 30) == pytest.approx(0.10, abs=1e-6)

    def test_too_short_returns_zero(self) -> None:
        assert _momentum((1.0, 2.0), 30) == 0.0

    def test_zero_baseline_returns_zero(self) -> None:
        values = tuple([0.0] * 30 + [10.0])
        assert _momentum(values, 30) == 0.0


class TestAlignedCrackSpreads:
    def _bar(self, day: int, close: float) -> OhlcvBar:
        ts = datetime(2026, 1, day, tzinfo=UTC)
        return OhlcvBar(ts=ts, open=close, high=close, low=close, close=close, volume=1)

    def test_aligned_dates(self) -> None:
        wti = OhlcvSeries(
            ticker="CL=F", interval="1d",
            bars=tuple(self._bar(d, 80.0) for d in (1, 2, 3)),
        )
        gas = OhlcvSeries(
            ticker="RB=F", interval="1d",
            bars=tuple(self._bar(d, 2.5) for d in (1, 2, 3)),
        )
        spreads = _aligned_crack_spreads(wti, gas)
        # crack = 42 * 2.5 - 80 = 105 - 80 = 25
        assert spreads == (25.0, 25.0, 25.0)

    def test_misaligned_dates_intersect(self) -> None:
        wti = OhlcvSeries(
            ticker="CL=F", interval="1d",
            bars=tuple(self._bar(d, 80.0) for d in (1, 2, 3)),
        )
        gas = OhlcvSeries(
            ticker="RB=F", interval="1d",
            bars=tuple(self._bar(d, 2.5) for d in (2, 3, 4)),
        )
        spreads = _aligned_crack_spreads(wti, gas)
        # Only 2,3 overlap → 2 spreads.
        assert len(spreads) == 2


# ── CommodityCycle.cycle_position ─────────────────────────────────────────


class TestCycleCyclePosition:
    @pytest.mark.parametrize("p,label", [
        (0.10, "low"),
        (0.30, "mid_low"),
        (0.55, "mid_high"),
        (0.85, "high"),
    ])
    def test_label_buckets(self, p: float, label: str) -> None:
        c = CommodityCycle(
            key=CommodityKey.WTI, last_close=80.0,
            cycle_percentile=p, momentum_30d=0.0,
            n_observations=100,
        )
        assert c.cycle_position == label


# ── Integration with a fake YFinance client ───────────────────────────────


class _FakeYFinance:
    last_snapshot_id = "fake-snapshot-id"

    def __init__(self, *, bars_per_call: dict[str, list[OhlcvBar]] | None = None,
                 fail: bool = False) -> None:
        self._bars = bars_per_call or {}
        self._fail = fail

    async def get_ohlcv(
        self, ticker: str, *, start: Any, end: Any, interval: str = "1d",
    ) -> OhlcvSeries:
        if self._fail:
            raise RuntimeError("fake yfinance error")
        bars = tuple(self._bars.get(ticker, []))
        return OhlcvSeries(ticker=ticker, interval=interval, bars=bars)


def _bars_with_trend(start_close: float, end_close: float, n: int) -> list[OhlcvBar]:
    out: list[OhlcvBar] = []
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for i in range(n):
        ts = base + timedelta(days=i)
        c = start_close + (end_close - start_close) * i / max(1, n - 1)
        out.append(OhlcvBar(ts=ts, open=c, high=c, low=c, close=c, volume=1))
    return out


class TestCommodityClientIntegration:
    @pytest.mark.asyncio
    async def test_get_cycle_returns_metrics(self) -> None:
        wti_bars = _bars_with_trend(70.0, 90.0, 60)
        client = CommodityClient(
            yfinance_client=_FakeYFinance(bars_per_call={"CL=F": wti_bars}),  # type: ignore[arg-type]
        )
        cycle = await client.get_cycle(CommodityKey.WTI)
        assert cycle.last_close == pytest.approx(90.0, abs=0.01)
        assert cycle.cycle_percentile > 0.9   # uptrend → near top
        assert cycle.momentum_30d > 0.0
        assert cycle.n_observations == 60

    @pytest.mark.asyncio
    async def test_get_cycle_failure_raises_typed_error(self) -> None:
        client = CommodityClient(yfinance_client=_FakeYFinance(fail=True))  # type: ignore[arg-type]
        with pytest.raises(CommodityDataError):
            await client.get_cycle(CommodityKey.WTI)

    @pytest.mark.asyncio
    async def test_cache_reuses_within_ttl(self) -> None:
        wti_bars = _bars_with_trend(70.0, 90.0, 30)
        fake = _FakeYFinance(bars_per_call={"CL=F": wti_bars})
        client = CommodityClient(yfinance_client=fake)  # type: ignore[arg-type]
        cycle1 = await client.get_cycle(CommodityKey.WTI)
        # Mutate the fake; cache should still serve original.
        fake._bars["CL=F"] = []
        cycle2 = await client.get_cycle(CommodityKey.WTI)
        assert cycle1.last_close == cycle2.last_close

    @pytest.mark.asyncio
    async def test_get_crack_spread_aligns_legs(self) -> None:
        wti_bars = _bars_with_trend(80.0, 80.0, 30)
        gas_bars = _bars_with_trend(2.5, 2.5, 30)
        client = CommodityClient(yfinance_client=_FakeYFinance(  # type: ignore[arg-type]
            bars_per_call={"CL=F": wti_bars, "RB=F": gas_bars},
        ))
        crack = await client.get_crack_spread()
        # 42 * 2.5 - 80 = 25
        assert crack.last_spread == pytest.approx(25.0, abs=0.5)
        assert crack.n_observations == 30

    @pytest.mark.asyncio
    async def test_point_in_time_get_cycle_slices_by_as_of(self) -> None:
        # v1.6.2 wave 2: ascending bars 70 → 90 over 60 days. as_of = day 30
        # should see only days 0..30 (closes 70..80), so cycle_percentile of
        # the LAST close in the slice (~80) should be near top of [70..80].
        wti_bars = _bars_with_trend(70.0, 90.0, 60)
        client = CommodityClient(
            yfinance_client=_FakeYFinance(bars_per_call={"CL=F": wti_bars}),  # type: ignore[arg-type]
        )
        as_of = datetime(2026, 1, 30, tzinfo=UTC).date()
        cycle = await client.get_cycle(CommodityKey.WTI, as_of=as_of)
        # Last bar in slice is day 29 (~80.0), and it's near top of slice.
        assert cycle.last_close < 85.0
        assert cycle.cycle_percentile > 0.85   # near top of the 0..29 slice
        # Without as_of, would see full 60 bars and last_close = 90.
        cycle_full = await client.get_cycle(CommodityKey.WTI)
        assert cycle_full.last_close > 89.0

    @pytest.mark.asyncio
    async def test_point_in_time_crack_spread_slices(self) -> None:
        # Crack spread point-in-time: same slicing on both legs.
        wti_bars = _bars_with_trend(80.0, 80.0, 60)
        gas_bars = _bars_with_trend(2.5, 2.5, 60)
        client = CommodityClient(yfinance_client=_FakeYFinance(  # type: ignore[arg-type]
            bars_per_call={"CL=F": wti_bars, "RB=F": gas_bars},
        ))
        as_of = datetime(2026, 1, 15, tzinfo=UTC).date()
        crack = await client.get_crack_spread(as_of=as_of)
        # Only first 15 bars considered → n_observations capped accordingly.
        assert crack.n_observations <= 15
        assert crack.n_observations > 0


# ── CrackSpread basic ─────────────────────────────────────────────────────


class TestCrackSpread:
    def test_dataclass_holds_fields(self) -> None:
        c = CrackSpread(last_spread=25.0, cycle_percentile=0.5, momentum_30d=0.1, n_observations=60)
        assert c.last_spread == 25.0
        assert c.cycle_percentile == 0.5
        assert c.momentum_30d == 0.1
