from __future__ import annotations

from datetime import UTC, datetime

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.commodity_client import (
    CommodityCycle,
    CommodityDataError,
    CommodityKey,
    CrackSpread,
)
from glostat.experts.e_commodity_index_kr import (
    ECommodityIndexKrExpert,
    _score,
)

# v1.5 P6 — commodity-index-KR expert tests.


class TestScore:
    def _wti(self, momentum: float) -> CommodityCycle:
        return CommodityCycle(
            key=CommodityKey.WTI, last_close=80.0,
            cycle_percentile=0.5, momentum_30d=momentum,
            n_observations=500,
        )

    def _crack(self, momentum: float) -> CrackSpread:
        return CrackSpread(
            last_spread=25.0, cycle_percentile=0.5,
            momentum_30d=momentum, n_observations=500,
        )

    def test_both_up_long(self) -> None:
        score = _score(self._wti(0.10), self._crack(0.15))
        assert score.direction == "LONG"
        assert score.net_score > 0.4

    def test_both_down_short(self) -> None:
        score = _score(self._wti(-0.10), self._crack(-0.15))
        assert score.direction == "SHORT"
        assert score.net_score < -0.4

    def test_flat_neutral(self) -> None:
        score = _score(self._wti(0.0), self._crack(0.0))
        assert score.direction == "NEUTRAL"
        assert score.net_score == pytest.approx(0.0, abs=1e-6)

    def test_mixed_partially_offsets(self) -> None:
        # WTI strongly up, crack mildly down → small positive net.
        score = _score(self._wti(0.20), self._crack(-0.05))
        assert score.net_score > 0.0
        assert abs(score.net_score) < 1.5

    def test_clip_at_score_clip(self) -> None:
        score = _score(self._wti(0.50), self._crack(0.50))
        assert -2.0 <= score.net_score <= 2.0


# ── Expert.compute integration ────────────────────────────────────────────


class _FakeCommodityClient:
    def __init__(
        self, *, wti: CommodityCycle | None = None,
        crack: CrackSpread | None = None, fail: bool = False,
    ) -> None:
        self._wti = wti
        self._crack = crack
        self._fail = fail

    async def get_cycle(self, key: CommodityKey) -> CommodityCycle:
        if self._fail or self._wti is None:
            raise CommodityDataError("fake error")
        return self._wti

    async def get_crack_spread(self) -> CrackSpread:
        if self._fail or self._crack is None:
            raise CommodityDataError("fake error")
        return self._crack


class TestExpertCompute:
    @pytest.mark.asyncio
    async def test_skips_non_refining_ticker(self) -> None:
        # 005930 = 삼성전자 — not refining.
        expert = ECommodityIndexKrExpert(
            commodity_client=_FakeCommodityClient(),  # type: ignore[arg-type]
        )
        with pytest.raises(ExpertSkipError, match="not in KR refining universe"):
            await expert.compute("005930", datetime.now(tz=UTC))

    @pytest.mark.asyncio
    async def test_skips_steel_ticker(self) -> None:
        # 005490 = POSCO holdings — STEEL, not REFINING.
        expert = ECommodityIndexKrExpert(
            commodity_client=_FakeCommodityClient(),  # type: ignore[arg-type]
        )
        with pytest.raises(ExpertSkipError):
            await expert.compute("005490", datetime.now(tz=UTC))

    @pytest.mark.asyncio
    async def test_refiner_with_oil_uptrend_long(self) -> None:
        wti = CommodityCycle(
            key=CommodityKey.WTI, last_close=85.0,
            cycle_percentile=0.7, momentum_30d=0.10,
            n_observations=500,
        )
        crack = CrackSpread(
            last_spread=28.0, cycle_percentile=0.6,
            momentum_30d=0.05, n_observations=500,
        )
        expert = ECommodityIndexKrExpert(
            commodity_client=_FakeCommodityClient(wti=wti, crack=crack),  # type: ignore[arg-type]
        )
        sig = await expert.compute("096770", datetime.now(tz=UTC))
        assert sig.direction == "LONG"
        assert sig.expert_name == "E_COMMODITY_INDEX_KR"

    @pytest.mark.asyncio
    async def test_refiner_with_oil_downtrend_short(self) -> None:
        wti = CommodityCycle(
            key=CommodityKey.WTI, last_close=70.0,
            cycle_percentile=0.3, momentum_30d=-0.12,
            n_observations=500,
        )
        crack = CrackSpread(
            last_spread=15.0, cycle_percentile=0.2,
            momentum_30d=-0.08, n_observations=500,
        )
        expert = ECommodityIndexKrExpert(
            commodity_client=_FakeCommodityClient(wti=wti, crack=crack),  # type: ignore[arg-type]
        )
        sig = await expert.compute("096770", datetime.now(tz=UTC))
        assert sig.direction == "SHORT"

    @pytest.mark.asyncio
    async def test_commodity_fail_skips_cleanly(self) -> None:
        expert = ECommodityIndexKrExpert(
            commodity_client=_FakeCommodityClient(fail=True),  # type: ignore[arg-type]
        )
        with pytest.raises(ExpertSkipError):
            await expert.compute("096770", datetime.now(tz=UTC))
