from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.commodity_client import (
    CommodityCycle,
    CommodityDataError,
    CommodityKey,
    CrackSpread,
)
from glostat.data.sector_classifier_kr import KrSector
from glostat.data.yfinance_types import Fundamentals
from glostat.experts.e_fundamental_kr_cyclical import (
    EFundamentalKrCyclicalExpert,
    _derive_ev_ebitda,
    _score,
)

# v1.5 P6 — cyclical-fundamental expert tests.


# ── Pure scoring helpers ──────────────────────────────────────────────────


class TestDeriveEvEbitda:
    def test_empty_raw_returns_none(self) -> None:
        f = Fundamentals(
            ticker="X", pe_ratio=10.0, forward_pe=None, eps=None, forward_eps=None,
            roe=None, market_cap=None, dividend_yield=None, beta=None,
            fifty_two_week_high=None, fifty_two_week_low=None, raw=(),
        )
        assert _derive_ev_ebitda(f) is None

    def test_present_in_raw(self) -> None:
        f = Fundamentals(
            ticker="X", pe_ratio=None, forward_pe=None, eps=None, forward_eps=None,
            roe=None, market_cap=None, dividend_yield=None, beta=None,
            fifty_two_week_high=None, fifty_two_week_low=None,
            raw=(("enterpriseToEbitda", 5.5),),
        )
        assert _derive_ev_ebitda(f) == pytest.approx(5.5)

    def test_garbage_value_filtered(self) -> None:
        f = Fundamentals(
            ticker="X", pe_ratio=None, forward_pe=None, eps=None, forward_eps=None,
            roe=None, market_cap=None, dividend_yield=None, beta=None,
            fifty_two_week_high=None, fifty_two_week_low=None,
            raw=(("enterpriseToEbitda", 999.0),),
        )
        assert _derive_ev_ebitda(f) is None

    def test_negative_filtered(self) -> None:
        f = Fundamentals(
            ticker="X", pe_ratio=None, forward_pe=None, eps=None, forward_eps=None,
            roe=None, market_cap=None, dividend_yield=None, beta=None,
            fifty_two_week_high=None, fifty_two_week_low=None,
            raw=(("enterpriseToEbitda", -1.0),),
        )
        assert _derive_ev_ebitda(f) is None


class TestScore:
    def _f(self, ev_ebitda: float | None) -> Fundamentals:
        raw: tuple[tuple[str, float], ...] = (
            (("enterpriseToEbitda", ev_ebitda),) if ev_ebitda is not None else ()
        )
        return Fundamentals(
            ticker="X", pe_ratio=10.0, forward_pe=None, eps=None,
            forward_eps=None, roe=None, market_cap=None, dividend_yield=None,
            beta=None, fifty_two_week_high=None, fifty_two_week_low=None, raw=raw,
        )

    def test_cycle_trough_cheap_valuation_is_strong_long(self) -> None:
        # SK이노베이션-style: EV/EBITDA = 4.0 vs refining median 5.5 (cheap),
        # crack spread at 20th percentile (trough) → strong BUY.
        score = _score(KrSector.REFINING, self._f(4.0), cycle_percentile=0.20)
        assert score.direction == "LONG"
        assert score.net_score > 0.4

    def test_cycle_peak_expensive_valuation_is_short(self) -> None:
        # EV/EBITDA = 9.0 (expensive vs 5.5), pctile 0.85 (peak) → SELL.
        score = _score(KrSector.REFINING, self._f(9.0), cycle_percentile=0.85)
        assert score.direction == "SHORT"
        assert score.net_score < -0.4

    def test_neutral_zone_returns_neutral(self) -> None:
        score = _score(KrSector.REFINING, self._f(5.5), cycle_percentile=0.50)
        assert score.direction == "NEUTRAL"
        assert abs(score.net_score) < 0.6

    def test_missing_ev_ebitda_falls_back_to_cycle(self) -> None:
        # When EV/EBITDA is missing, only cycle term contributes — should
        # still produce a directional signal at extreme percentiles.
        score = _score(KrSector.REFINING, self._f(None), cycle_percentile=0.10)
        assert score.ev_ebitda is None
        assert score.net_score > 0.0   # trough → LONG

    def test_clip_at_score_clip(self) -> None:
        # Construct extreme inputs and verify clipping at ±3.0.
        score = _score(KrSector.REFINING, self._f(0.5), cycle_percentile=0.0)
        assert -3.0 <= score.net_score <= 3.0


# ── Expert.compute integration with fakes ─────────────────────────────────


class _FakeYFinance:
    last_snapshot_id = "fake-snapshot"

    def __init__(self, fundamentals: Fundamentals) -> None:
        self._f = fundamentals

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        return self._f


class _FakeRouter:
    def __init__(self, client: Any) -> None:
        self._client = client

    def route(self, _expert: str, _method: str) -> tuple[Any, str]:
        return self._client, "get_fundamentals"


class _FakeCommodityClient:
    def __init__(
        self,
        *,
        cycle: CommodityCycle | None = None,
        crack: CrackSpread | None = None,
        fail: bool = False,
    ) -> None:
        self._cycle = cycle
        self._crack = crack
        self._fail = fail

    async def get_cycle(self, key: CommodityKey) -> CommodityCycle:
        if self._fail or self._cycle is None:
            raise CommodityDataError("fake error")
        return self._cycle

    async def get_crack_spread(self) -> CrackSpread:
        if self._fail or self._crack is None:
            raise CommodityDataError("fake error")
        return self._crack


def _fundamentals_with_ev_ebitda(ev: float) -> Fundamentals:
    return Fundamentals(
        ticker="096770.KS", pe_ratio=12.0, forward_pe=None, eps=None,
        forward_eps=None, roe=0.08, market_cap=10_000_000_000,
        dividend_yield=0.025, beta=None,
        fifty_two_week_high=None, fifty_two_week_low=None,
        raw=(("enterpriseToEbitda", ev),),
    )


class TestExpertCompute:
    @pytest.mark.asyncio
    async def test_skips_non_cyclical_ticker(self) -> None:
        # 005930 = 삼성전자 (semiconductor — growth, not cyclical).
        expert = EFundamentalKrCyclicalExpert(
            router=_FakeRouter(_FakeYFinance(_fundamentals_with_ev_ebitda(8.0))),  # type: ignore[arg-type]
            commodity_client=_FakeCommodityClient(),  # type: ignore[arg-type]
        )
        with pytest.raises(ExpertSkipError, match="not classified as cyclical"):
            await expert.compute("005930", datetime.now(tz=UTC))

    @pytest.mark.asyncio
    async def test_refining_ticker_with_trough_cycle_long(self) -> None:
        # 096770 = SK이노베이션. Cheap EV/EBITDA + crack at 15th pctile → LONG.
        f = _fundamentals_with_ev_ebitda(4.0)   # cheap vs 5.5 median (-0.6z)
        expert = EFundamentalKrCyclicalExpert(
            router=_FakeRouter(_FakeYFinance(f)),  # type: ignore[arg-type]
            commodity_client=_FakeCommodityClient(  # type: ignore[arg-type]
                crack=CrackSpread(
                    last_spread=15.0, cycle_percentile=0.15,
                    momentum_30d=0.05, n_observations=500,
                ),
            ),
        )
        sig = await expert.compute("096770", datetime.now(tz=UTC))
        assert sig.direction == "LONG"
        assert sig.expert_name == "E_FUNDAMENTAL_KR_CYCLICAL"
        assert sig.net_score > 0.0

    @pytest.mark.asyncio
    async def test_refining_with_peak_cycle_short(self) -> None:
        f = _fundamentals_with_ev_ebitda(8.0)
        expert = EFundamentalKrCyclicalExpert(
            router=_FakeRouter(_FakeYFinance(f)),  # type: ignore[arg-type]
            commodity_client=_FakeCommodityClient(  # type: ignore[arg-type]
                crack=CrackSpread(
                    last_spread=40.0, cycle_percentile=0.90,
                    momentum_30d=-0.01, n_observations=500,
                ),
            ),
        )
        sig = await expert.compute("096770", datetime.now(tz=UTC))
        assert sig.direction == "SHORT"
        assert sig.net_score < 0.0

    @pytest.mark.asyncio
    async def test_steel_uses_iron_ore_cycle(self) -> None:
        # 005490 = POSCO. Iron ore at low pctile → LONG.
        f = _fundamentals_with_ev_ebitda(5.0)
        expert = EFundamentalKrCyclicalExpert(
            router=_FakeRouter(_FakeYFinance(f)),  # type: ignore[arg-type]
            commodity_client=_FakeCommodityClient(  # type: ignore[arg-type]
                cycle=CommodityCycle(
                    key=CommodityKey.IRON_ORE, last_close=80.0,
                    cycle_percentile=0.15, momentum_30d=0.02,
                    n_observations=500,
                ),
            ),
        )
        sig = await expert.compute("005490", datetime.now(tz=UTC))
        assert sig.direction == "LONG"

    @pytest.mark.asyncio
    async def test_commodity_failure_skips_cleanly(self) -> None:
        f = _fundamentals_with_ev_ebitda(5.0)
        expert = EFundamentalKrCyclicalExpert(
            router=_FakeRouter(_FakeYFinance(f)),  # type: ignore[arg-type]
            commodity_client=_FakeCommodityClient(fail=True),  # type: ignore[arg-type]
        )
        with pytest.raises(ExpertSkipError):
            await expert.compute("096770", datetime.now(tz=UTC))
