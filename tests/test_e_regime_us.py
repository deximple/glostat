from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.regime_us_client import (
    RegimeDataError,
    UstCurveSlope,
    VixTermStructure,
)
from glostat.experts.e_regime_us import (
    ERegimeUsExpert,
    RegimeUsInputs,
    score_regime_us,
)
from glostat.predictor.calibration import (
    is_active,
    synthetic_calibration_for_mock,
)

# ── Pure scoring tests (no network) ──────────────────────────────────────


class TestScoreRegimeUs:
    def test_neutral_when_no_inputs(self) -> None:
        s = score_regime_us(RegimeUsInputs(vix_term=None, curve=None))
        assert s.raw_score == 0.0
        assert s.net_score == 0.0
        assert s.direction == "NEUTRAL"

    def test_calm_contango_curve_steep_long(self) -> None:
        # ratio=0.85 → +1.5 z, scaled w_vix=1 → +1.5
        # slope=200bps → +2 z (capped), scaled w_curve=1 → +2.0
        # raw=3.5, net clipped to +3.0 → LONG
        s = score_regime_us(RegimeUsInputs(
            vix_term=VixTermStructure(vix9d=17.0, vix3m=20.0,
                                      ratio=0.85, in_backwardation=False),
            curve=UstCurveSlope(front_yield_pct=2.0, back_yield_pct=4.0,
                                slope_bps=200.0, inverted=False),
        ))
        assert s.vix_term_term > 1.0
        assert s.curve_term > 1.0
        assert s.net_score > 0.6
        assert s.direction == "LONG"

    def test_stress_backwardation_inverted_short(self) -> None:
        # ratio=1.15 → -1.5 z, term=-1.5
        # slope=-100bps → -1 z, term=-1
        # net=-2.5 → SHORT
        s = score_regime_us(RegimeUsInputs(
            vix_term=VixTermStructure(vix9d=23.0, vix3m=20.0,
                                      ratio=1.15, in_backwardation=True),
            curve=UstCurveSlope(front_yield_pct=5.0, back_yield_pct=4.0,
                                slope_bps=-100.0, inverted=True),
        ))
        assert s.vix_term_term < -1.0
        assert s.curve_term < -0.5
        assert s.net_score < -0.6
        assert s.direction == "SHORT"

    def test_clipping_at_score_cap(self) -> None:
        # Both terms at extreme deviation → would exceed ±3 raw.
        s = score_regime_us(RegimeUsInputs(
            vix_term=VixTermStructure(vix9d=10.0, vix3m=20.0,
                                      ratio=0.5, in_backwardation=False),
            curve=UstCurveSlope(front_yield_pct=0.5, back_yield_pct=4.5,
                                slope_bps=400.0, inverted=False),
        ))
        # Each term capped at ±2 z by the score helper → max raw = +2 + +2 = +4
        # net_score is clipped to +3.
        assert s.raw_score > 3.0 - 1e-9   # approaches/exceeds clip
        assert s.net_score == pytest.approx(3.0, abs=1e-9)
        assert s.clipped is True


# ── Expert integration with a fake regime client ─────────────────────────


class _FakeRegimeClient:
    """Minimal stand-in for RegimeUsClient so the expert tests stay offline."""

    class _YfStub:
        last_snapshot_id = "fake-snap"

    def __init__(
        self, *,
        vix: VixTermStructure | None = None,
        curve: UstCurveSlope | None = None,
        vix_fail: bool = False,
        curve_fail: bool = False,
    ) -> None:
        self._vix = vix
        self._curve = curve
        self._vix_fail = vix_fail
        self._curve_fail = curve_fail
        self._yf = self._YfStub()

    async def get_vix_term(self, *, as_of: Any = None) -> VixTermStructure:
        if self._vix_fail:
            raise RegimeDataError("vix unavailable")
        if self._vix is None:
            raise RegimeDataError("no vix data")
        return self._vix

    async def get_curve_slope(self, *, as_of: Any = None) -> UstCurveSlope:
        if self._curve_fail:
            raise RegimeDataError("curve unavailable")
        if self._curve is None:
            raise RegimeDataError("no curve data")
        return self._curve


class TestERegimeUsExpert:
    @pytest.mark.asyncio
    async def test_skips_kr_ticker(self) -> None:
        expert = ERegimeUsExpert(regime_client=_FakeRegimeClient())  # type: ignore[arg-type]
        with pytest.raises(ExpertSkipError, match="KR"):
            await expert.compute("005930", datetime(2026, 5, 2, tzinfo=UTC))

    @pytest.mark.asyncio
    async def test_skips_kr_ticker_with_suffix(self) -> None:
        expert = ERegimeUsExpert(regime_client=_FakeRegimeClient())  # type: ignore[arg-type]
        with pytest.raises(ExpertSkipError, match="KR"):
            await expert.compute("005930.KS", datetime(2026, 5, 2, tzinfo=UTC))

    @pytest.mark.asyncio
    async def test_skips_when_both_sources_fail(self) -> None:
        expert = ERegimeUsExpert(regime_client=_FakeRegimeClient(  # type: ignore[arg-type]
            vix_fail=True, curve_fail=True,
        ))
        with pytest.raises(ExpertSkipError, match="no usable"):
            await expert.compute("AAPL", datetime(2026, 5, 2, tzinfo=UTC))

    @pytest.mark.asyncio
    async def test_partial_data_still_emits_signal(self) -> None:
        # Curve fetch fails; VIX still works → signal emitted with curve_term=0.
        expert = ERegimeUsExpert(regime_client=_FakeRegimeClient(  # type: ignore[arg-type]
            vix=VixTermStructure(
                vix9d=15.0, vix3m=20.0, ratio=0.75, in_backwardation=False,
            ),
            curve_fail=True,
        ))
        sig = await expert.compute("AAPL", datetime(2026, 5, 2, tzinfo=UTC))
        assert sig.expert_name == "E_REGIME_US"
        assert sig.ticker == "AAPL"
        assert "VIX term" in sig.basis
        assert "UST" not in sig.basis  # curve missing

    @pytest.mark.asyncio
    async def test_full_signal_metadata_present(self) -> None:
        expert = ERegimeUsExpert(regime_client=_FakeRegimeClient(  # type: ignore[arg-type]
            vix=VixTermStructure(
                vix9d=15.0, vix3m=20.0, ratio=0.75, in_backwardation=False,
            ),
            curve=UstCurveSlope(
                front_yield_pct=4.0, back_yield_pct=4.5, slope_bps=50.0,
                inverted=False,
            ),
        ))
        sig = await expert.compute("MSFT", datetime(2026, 5, 2, tzinfo=UTC))
        meta = dict(sig.metadata)
        assert meta["vix_ratio"] == "0.7500"
        assert meta["vix_backwardation"] == "False"
        assert meta["curve_slope_bps"] == "50.0000"
        assert meta["curve_inverted"] == "False"
        assert sig.archetype == "continuation"
        # Net score positive → LONG (calm + steepening)
        assert sig.direction == "LONG"


# ── Calibration table registration ───────────────────────────────────────


class TestCalibrationRegistration:
    def test_e_regime_us_in_synthetic_table(self) -> None:
        table = synthetic_calibration_for_mock()
        assert "E_REGIME_US" in table.entries
        cal = table.entries["E_REGIME_US"]
        assert cal.auc == 0.50
        assert cal.n_samples == 0   # bootstrap until real hindcast lands
        assert cal.sharpe == 0.0

    def test_e_regime_us_inactive_until_n_samples(self) -> None:
        cal = synthetic_calibration_for_mock().entries["E_REGIME_US"]
        # n=0 → not active → composite weight = 0 (preserves INV-GS-103 safety).
        assert is_active(cal) is False
