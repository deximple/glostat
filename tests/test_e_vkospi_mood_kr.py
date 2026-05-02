from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.vkospi_client import VkospiBar, VkospiClient
from glostat.experts.e_vkospi_mood_kr import (
    EVkospiMoodKrExpert,
    ReturnResolver,
    SmallCapResolver,
    VkospiMoodInputs,
    score_vkospi_mood,
)
from glostat.predictor.calibration import (
    is_active,
    synthetic_calibration_for_mock,
)

# ── Pure scoring (paper-derived 4-quadrant matrix + small-cap) ───────────


class TestRegimeClassification:
    def test_below_threshold_when_return_too_small(self) -> None:
        inputs = VkospiMoodInputs(return_t=0.05, delta_pct=0.10, small_cap=False)
        assert inputs.regime() == "below_threshold"

    def test_drift_aligned_vkospi_down_price_up(self) -> None:
        inputs = VkospiMoodInputs(return_t=0.12, delta_pct=-0.05, small_cap=False)
        assert inputs.regime() == "drift_aligned"

    def test_reversal_aligned_vkospi_up_price_down(self) -> None:
        inputs = VkospiMoodInputs(return_t=-0.12, delta_pct=0.05, small_cap=False)
        assert inputs.regime() == "reversal_aligned"

    def test_misaligned_up_up(self) -> None:
        inputs = VkospiMoodInputs(return_t=0.12, delta_pct=0.05, small_cap=False)
        assert inputs.regime() == "misaligned_up_up"

    def test_misaligned_down_down(self) -> None:
        inputs = VkospiMoodInputs(return_t=-0.12, delta_pct=-0.05, small_cap=False)
        assert inputs.regime() == "misaligned_down_down"


class TestScoreVkospiMood:
    def test_below_threshold_neutral_zero(self) -> None:
        s = score_vkospi_mood(
            VkospiMoodInputs(return_t=0.05, delta_pct=0.10, small_cap=False)
        )
        assert s.regime == "below_threshold"
        assert s.direction == "NEUTRAL"
        assert s.net_score == 0.0
        assert s.raw_score == 0.0

    def test_drift_aligned_long_with_positive_score(self) -> None:
        # Paper: VKOSPI↓ + r↑ → drift continuation, LONG.
        # |r|=0.15, |Δ|=0.10 → magnitude=0.5, vol_term=0.5, raw=0.25 — small.
        # Push beyond direction threshold with stronger inputs:
        s = score_vkospi_mood(
            VkospiMoodInputs(return_t=0.20, delta_pct=-0.15, small_cap=False)
        )
        assert s.regime == "drift_aligned"
        # magnitude = (0.20 - 0.10) * 10 = 1.0
        # vol_term  = min(0.20, 0.15) * 5 = 0.75
        # raw       = 1.0 * 0.75 * 1.0 = 0.75
        assert s.raw_score == pytest.approx(0.75, abs=1e-6)
        assert s.direction == "LONG"
        assert s.net_score > 0.6

    def test_reversal_aligned_long_with_positive_score(self) -> None:
        # Paper: VKOSPI↑ + r↓ → reversal, LONG.
        s = score_vkospi_mood(
            VkospiMoodInputs(return_t=-0.20, delta_pct=0.15, small_cap=False)
        )
        assert s.regime == "reversal_aligned"
        assert s.raw_score == pytest.approx(0.75, abs=1e-6)
        assert s.direction == "LONG"

    def test_misaligned_neutral_even_with_high_magnitude(self) -> None:
        # Paper: misaligned cells have no significant cumulative return.
        # Score may be non-zero internally but direction MUST be NEUTRAL.
        s = score_vkospi_mood(
            VkospiMoodInputs(return_t=0.20, delta_pct=0.15, small_cap=False)
        )
        assert s.regime == "misaligned_up_up"
        assert s.direction == "NEUTRAL"
        assert s.net_score == 0.0
        # raw_score is still computed (telemetry) but net_score is zeroed.
        assert s.raw_score > 0.0

    def test_small_cap_multiplier_amplifies_drift(self) -> None:
        # Paper Table 5A: small-cap drift 20d +9.21% raw vs ~2.11% large-cap.
        # Expert encodes this via 1.5x multiplier on aligned cases.
        large = score_vkospi_mood(
            VkospiMoodInputs(return_t=0.20, delta_pct=-0.15, small_cap=False)
        )
        small = score_vkospi_mood(
            VkospiMoodInputs(return_t=0.20, delta_pct=-0.15, small_cap=True)
        )
        assert small.raw_score == pytest.approx(large.raw_score * 1.5, abs=1e-6)
        assert small.direction == "LONG"

    def test_score_clip_at_three(self) -> None:
        # Saturating inputs in an ALIGNED case (drift): r=+1.10, ΔVKOSPI=-0.30.
        # magnitude=(1.10-0.10)*10=10, vol_term=min(0.20,0.30)*5=1.0,
        # multiplier=1.5 (small) → raw=15 → clipped to 3.0.
        s = score_vkospi_mood(
            VkospiMoodInputs(return_t=1.10, delta_pct=-0.30, small_cap=True)
        )
        assert s.regime == "drift_aligned"
        assert s.raw_score > 3.0
        assert s.net_score == pytest.approx(3.0, abs=1e-9)
        assert s.clipped is True
        assert s.direction == "LONG"

    def test_below_threshold_in_misaligned_quadrant_still_neutral(self) -> None:
        # |r|=0.05 < threshold → below_threshold regardless of VKOSPI sign.
        s = score_vkospi_mood(
            VkospiMoodInputs(return_t=-0.05, delta_pct=0.05, small_cap=False)
        )
        assert s.regime == "below_threshold"
        assert s.direction == "NEUTRAL"


# ── Confidence + clipped flags on VkospiMoodScore ────────────────────────


class TestVkospiMoodScoreFlags:
    def test_confidence_bounded(self) -> None:
        # Synthetic strong LONG signal → confidence should be > 0.
        s = score_vkospi_mood(
            VkospiMoodInputs(return_t=0.30, delta_pct=-0.20, small_cap=True)
        )
        assert 0.0 <= s.confidence <= 1.0
        assert s.confidence > 0.5

    def test_neutral_signal_zero_confidence(self) -> None:
        s = score_vkospi_mood(
            VkospiMoodInputs(return_t=0.20, delta_pct=0.10, small_cap=False)
        )
        # misaligned → direction=NEUTRAL → net_score=0 → confidence=0.
        assert s.confidence == 0.0


# ── Expert integration with fakes ────────────────────────────────────────


class _FakeReturnResolver(ReturnResolver):
    def __init__(self, return_t: float | None) -> None:
        self._r = return_t

    async def get_recent_daily_return(
        self, code: str, as_of: date,
    ) -> float | None:
        return self._r


class _FakeSmallCapResolver(SmallCapResolver):
    def __init__(self, small: bool) -> None:
        self._small = small

    async def is_small_cap(self, code: str, as_of: date) -> bool:
        return self._small


def _vkospi_client_with(
    *, t: date, t_close: float, prev_close: float,
) -> VkospiClient:
    bars: tuple[VkospiBar, ...] = (
        VkospiBar(bar_date=t.replace(day=t.day - 1), close=prev_close),
        VkospiBar(bar_date=t, close=t_close),
    )

    async def provider(_s: date, _e: date) -> tuple[VkospiBar, ...]:
        return bars

    return VkospiClient(history_provider=provider)


class TestExpertCompute:
    @pytest.mark.asyncio
    async def test_skips_non_kr_ticker(self) -> None:
        expert = EVkospiMoodKrExpert(
            vkospi_client=VkospiClient(),
            return_resolver=_FakeReturnResolver(0.15),
        )
        with pytest.raises(ExpertSkipError, match="not KR"):
            await expert.compute("AAPL", datetime(2026, 5, 2, tzinfo=UTC))

    @pytest.mark.asyncio
    async def test_skips_when_below_threshold(self) -> None:
        expert = EVkospiMoodKrExpert(
            vkospi_client=_vkospi_client_with(
                t=date(2026, 5, 2), t_close=18.0, prev_close=20.0,
            ),
            return_resolver=_FakeReturnResolver(0.05),  # |r|=5% < 10%
        )
        with pytest.raises(ExpertSkipError, match="threshold"):
            await expert.compute("005930", datetime(2026, 5, 2, tzinfo=UTC))

    @pytest.mark.asyncio
    async def test_skips_when_vkospi_unavailable(self) -> None:
        expert = EVkospiMoodKrExpert(
            vkospi_client=VkospiClient(),  # no provider
            return_resolver=_FakeReturnResolver(0.20),
        )
        with pytest.raises(ExpertSkipError, match="VKOSPI"):
            await expert.compute("005930", datetime(2026, 5, 2, tzinfo=UTC))

    @pytest.mark.asyncio
    async def test_drift_aligned_emits_long_signal(self) -> None:
        # r=+15%, ΔVKOSPI=-10% → drift_aligned → LONG
        expert = EVkospiMoodKrExpert(
            vkospi_client=_vkospi_client_with(
                t=date(2026, 5, 2), t_close=18.0, prev_close=20.0,
            ),
            return_resolver=_FakeReturnResolver(0.15),
            small_cap_resolver=_FakeSmallCapResolver(False),
        )
        sig = await expert.compute("005930", datetime(2026, 5, 2, tzinfo=UTC))
        assert sig.expert_name == "E_VKOSPI_MOOD_KR"
        assert sig.archetype == "continuation"
        meta = dict(sig.metadata)
        assert meta["regime"] == "drift_aligned"
        assert meta["small_cap"] == "False"
        # 30 days expiry from horizon.

    @pytest.mark.asyncio
    async def test_reversal_aligned_emits_long_with_contrarian_archetype(self) -> None:
        # r=-15%, ΔVKOSPI=+10% → reversal_aligned → LONG, contrarian.
        expert = EVkospiMoodKrExpert(
            vkospi_client=_vkospi_client_with(
                t=date(2026, 5, 2), t_close=22.0, prev_close=20.0,
            ),
            return_resolver=_FakeReturnResolver(-0.15),
        )
        sig = await expert.compute("005930", datetime(2026, 5, 2, tzinfo=UTC))
        assert sig.archetype == "contrarian"
        meta = dict(sig.metadata)
        assert meta["regime"] == "reversal_aligned"

    @pytest.mark.asyncio
    async def test_misaligned_skips_via_neutral_direction(self) -> None:
        # r=+15%, ΔVKOSPI=+10% → misaligned_up_up → NEUTRAL direction
        # → score.direction == NEUTRAL but score.regime != below_threshold.
        # Expert path: regime != below_threshold, no skip raised, but
        # signal direction is NEUTRAL — composite handles as no-vote.
        expert = EVkospiMoodKrExpert(
            vkospi_client=_vkospi_client_with(
                t=date(2026, 5, 2), t_close=22.0, prev_close=20.0,
            ),
            return_resolver=_FakeReturnResolver(0.15),
        )
        sig = await expert.compute("005930", datetime(2026, 5, 2, tzinfo=UTC))
        assert sig.direction == "NEUTRAL"
        meta = dict(sig.metadata)
        assert meta["regime"] == "misaligned_up_up"

    @pytest.mark.asyncio
    async def test_no_return_skips(self) -> None:
        expert = EVkospiMoodKrExpert(
            vkospi_client=_vkospi_client_with(
                t=date(2026, 5, 2), t_close=22.0, prev_close=20.0,
            ),
            return_resolver=_FakeReturnResolver(None),
        )
        with pytest.raises(ExpertSkipError, match="no recent daily return"):
            await expert.compute("005930", datetime(2026, 5, 2, tzinfo=UTC))


# ── Calibration registration ─────────────────────────────────────────────


class TestCalibrationRegistration:
    def test_e_vkospi_mood_kr_in_synthetic_table(self) -> None:
        table = synthetic_calibration_for_mock()
        assert "E_VKOSPI_MOOD_KR" in table.entries
        cal = table.entries["E_VKOSPI_MOOD_KR"]
        assert cal.auc == 0.50
        assert cal.n_samples == 0
        assert cal.calibration_status == "bootstrap"

    def test_e_vkospi_mood_kr_inactive_until_hindcast(self) -> None:
        cal = synthetic_calibration_for_mock().entries["E_VKOSPI_MOOD_KR"]
        # n=0 → composite weight = 0 (INV-GS-103, INV-GS-133 floor preserved).
        assert is_active(cal) is False
