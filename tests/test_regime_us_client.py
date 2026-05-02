from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from glostat.data.regime_us_client import (
    RegimeDataError,
    RegimeKey,
    RegimeUsClient,
    UstCurveSlope,
    VixTermStructure,
    _closes_on_or_before,
    _momentum,
    _percentile_rank,
)
from glostat.data.yfinance_types import OhlcvBar, OhlcvSeries

# v1.10 — regime_us_client tests. Pure-helper tests + a fake YFinance
# integration to avoid hitting the live network. Mirrors test_commodity_client.


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


class TestClosesOnOrBefore:
    def _bar(self, day: int, close: float) -> OhlcvBar:
        ts = datetime(2026, 1, day, tzinfo=UTC)
        return OhlcvBar(ts=ts, open=close, high=close, low=close,
                        close=close, volume=1)

    def test_no_as_of_returns_all(self) -> None:
        bars = tuple(self._bar(d, float(d)) for d in (1, 2, 3))
        assert _closes_on_or_before(bars, None) == (1.0, 2.0, 3.0)

    def test_as_of_truncates(self) -> None:
        bars = tuple(self._bar(d, float(d)) for d in (1, 2, 3, 4, 5))
        as_of = datetime(2026, 1, 3, tzinfo=UTC).date()
        assert _closes_on_or_before(bars, as_of) == (1.0, 2.0, 3.0)


# ── Integration with a fake YFinance client ───────────────────────────────


class _FakeYFinance:
    last_snapshot_id = "fake-regime-snapshot"

    def __init__(
        self, *,
        bars_per_call: dict[str, list[OhlcvBar]] | None = None,
        fail_for: tuple[str, ...] = (),
    ) -> None:
        self._bars = bars_per_call or {}
        self._fail_for = fail_for

    async def get_ohlcv(
        self, ticker: str, *, start: Any, end: Any, interval: str = "1d",
    ) -> OhlcvSeries:
        if ticker in self._fail_for:
            raise RuntimeError(f"fake yfinance error for {ticker}")
        bars = tuple(self._bars.get(ticker, []))
        return OhlcvSeries(ticker=ticker, interval=interval, bars=bars)


def _flat_bars(close: float, n: int, *, start_day: int = 1) -> list[OhlcvBar]:
    out: list[OhlcvBar] = []
    base = datetime(2026, 1, start_day, tzinfo=UTC)
    for i in range(n):
        ts = base + timedelta(days=i)
        out.append(OhlcvBar(
            ts=ts, open=close, high=close, low=close, close=close, volume=1,
        ))
    return out


class TestRegimeUsClient:
    @pytest.mark.asyncio
    async def test_get_level_returns_metrics(self) -> None:
        bars = _flat_bars(20.0, 60)
        client = RegimeUsClient(
            yfinance_client=_FakeYFinance(bars_per_call={"^VIX": bars}),  # type: ignore[arg-type]
        )
        level = await client.get_level(RegimeKey.VIX)
        assert level.last_close == pytest.approx(20.0)
        assert level.n_observations == 60

    @pytest.mark.asyncio
    async def test_get_level_failure_raises_typed(self) -> None:
        client = RegimeUsClient(
            yfinance_client=_FakeYFinance(fail_for=("^VIX",)),  # type: ignore[arg-type]
        )
        with pytest.raises(RegimeDataError):
            await client.get_level(RegimeKey.VIX)

    @pytest.mark.asyncio
    async def test_vix_term_contango(self) -> None:
        # VIX9D=15, VIX3M=20 → ratio=0.75, contango (calm regime).
        client = RegimeUsClient(
            yfinance_client=_FakeYFinance(bars_per_call={  # type: ignore[arg-type]
                "^VIX9D": _flat_bars(15.0, 30),
                "^VIX3M": _flat_bars(20.0, 30),
            }),
        )
        v = await client.get_vix_term()
        assert v.ratio == pytest.approx(0.75, abs=1e-6)
        assert v.in_backwardation is False

    @pytest.mark.asyncio
    async def test_vix_term_backwardation(self) -> None:
        # VIX9D=30, VIX3M=20 → ratio=1.50, backwardation (stress).
        client = RegimeUsClient(
            yfinance_client=_FakeYFinance(bars_per_call={  # type: ignore[arg-type]
                "^VIX9D": _flat_bars(30.0, 30),
                "^VIX3M": _flat_bars(20.0, 30),
            }),
        )
        v = await client.get_vix_term()
        assert v.ratio == pytest.approx(1.5, abs=1e-6)
        assert v.in_backwardation is True

    @pytest.mark.asyncio
    async def test_vix_term_no_alignment_raises(self) -> None:
        # Non-overlapping date ranges → no aligned closes → typed error.
        client = RegimeUsClient(
            yfinance_client=_FakeYFinance(bars_per_call={  # type: ignore[arg-type]
                "^VIX9D": _flat_bars(15.0, 1, start_day=1),
                "^VIX3M": _flat_bars(20.0, 1, start_day=10),
            }),
        )
        with pytest.raises(RegimeDataError):
            await client.get_vix_term()

    @pytest.mark.asyncio
    async def test_curve_inverted(self) -> None:
        # 3m=5%, 10y=4% → -100bps inverted.
        client = RegimeUsClient(
            yfinance_client=_FakeYFinance(bars_per_call={  # type: ignore[arg-type]
                "^IRX": _flat_bars(5.0, 30),
                "^TNX": _flat_bars(4.0, 30),
            }),
        )
        c = await client.get_curve_slope()
        assert c.slope_bps == pytest.approx(-100.0, abs=1e-6)
        assert c.inverted is True

    @pytest.mark.asyncio
    async def test_curve_steep(self) -> None:
        # 3m=2%, 10y=4.5% → +250bps steep.
        client = RegimeUsClient(
            yfinance_client=_FakeYFinance(bars_per_call={  # type: ignore[arg-type]
                "^IRX": _flat_bars(2.0, 30),
                "^TNX": _flat_bars(4.5, 30),
            }),
        )
        c = await client.get_curve_slope()
        assert c.slope_bps == pytest.approx(250.0, abs=1e-6)
        assert c.inverted is False

    @pytest.mark.asyncio
    async def test_cache_reuses_within_ttl(self) -> None:
        bars = _flat_bars(20.0, 30)
        fake = _FakeYFinance(bars_per_call={"^VIX": bars})
        client = RegimeUsClient(yfinance_client=fake)  # type: ignore[arg-type]
        a = await client.get_level(RegimeKey.VIX)
        # Mutate the fake; cache should still serve original.
        fake._bars["^VIX"] = []
        b = await client.get_level(RegimeKey.VIX)
        assert a.last_close == b.last_close

    @pytest.mark.asyncio
    async def test_point_in_time_get_level_slices(self) -> None:
        # Trending up: day 0..59 closes = 10..20.
        bars = []
        for i in range(60):
            close = 10.0 + (10.0 * i / 59)
            ts = datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=i)
            bars.append(OhlcvBar(
                ts=ts, open=close, high=close, low=close, close=close, volume=1,
            ))
        client = RegimeUsClient(
            yfinance_client=_FakeYFinance(bars_per_call={"^VIX": bars}),  # type: ignore[arg-type]
        )
        as_of = datetime(2026, 1, 30, tzinfo=UTC).date()
        level_at = await client.get_level(RegimeKey.VIX, as_of=as_of)
        # Last bar at as_of is day 29; close ≈ 14.92.
        assert level_at.last_close < 16.0
        assert level_at.last_close > 13.0
        # Without as_of, last close = 20.
        level_full = await client.get_level(RegimeKey.VIX)
        assert level_full.last_close > 19.0


# ── Dataclass identity ────────────────────────────────────────────────────


class TestDataclasses:
    def test_vix_term_immutable(self) -> None:
        v = VixTermStructure(
            vix9d=15.0, vix3m=20.0, ratio=0.75, in_backwardation=False,
        )
        with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
            v.ratio = 1.0  # type: ignore[misc]

    def test_curve_immutable(self) -> None:
        c = UstCurveSlope(
            front_yield_pct=4.0, back_yield_pct=4.5, slope_bps=50.0,
            inverted=False,
        )
        with pytest.raises((AttributeError, TypeError, FrozenInstanceError)):
            c.slope_bps = 100.0  # type: ignore[misc]
