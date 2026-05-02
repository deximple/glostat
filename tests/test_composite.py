from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from glostat.predictor.calibration import (
    CalibrationTable,
    ThesisCalibration,
    synthetic_calibration_for_mock,
)
from glostat.predictor.composite import (
    _OOS_STABILITY_FLOOR,
    _brier_to_weight,
    _oos_stability_factor,
    _weight_for,
    horizon_to_days,
    horizon_to_timedelta,
    predict,
)
from glostat.predictor.types import SignalContribution


def _signal(
    *,
    name: str = "X",
    value: float | None = 1.0,
    direction: str = "up",
    auc: float = 0.586,
    sharpe: float = 0.629,
    n: int = 298,
) -> SignalContribution:
    return SignalContribution(
        name=name, value=value, direction=direction,  # type: ignore[arg-type]
        calibration_auc=auc, calibration_sharpe=sharpe, n_samples=n,
    )


def _table_with(*entries: ThesisCalibration) -> CalibrationTable:
    t = CalibrationTable()
    for e in entries:
        t.entries[e.name] = e
    return t


def _cal(
    name: str, *, auc: float = 0.586, sharpe: float = 0.629, n: int = 298
) -> ThesisCalibration:
    return ThesisCalibration(
        name=name, auc=auc, sharpe=sharpe, n_samples=n,
        oos_degradation=0.0,
        period_start=date(2024, 1, 1), period_end=date(2026, 3, 31),
    )


# ── basic predict() construction ──────────────────────────────────────────


def test_predict_returns_normalized_probabilities() -> None:
    contribs = (_signal(name="A", direction="up"),)
    table = _table_with(_cal("A"))
    p = predict(
        ticker="AAPL", horizon="swing_30d",
        contributions=contribs, cal_table=table,
    )
    assert abs(p.up_probability + p.down_probability + p.sideways_probability - 1.0) < 1e-6


def test_predict_default_base_rate_for_swing30d() -> None:
    p = predict(
        ticker="AAPL", horizon="swing_30d",
        contributions=(_signal(name="X", direction="neutral"),),
        cal_table=_table_with(_cal("X")),
    )
    assert p.base_rate_up == pytest.approx(0.52)


def test_predict_long3y_baseline_higher() -> None:
    p = predict(
        ticker="AAPL", horizon="long_3y",
        contributions=(_signal(name="X", direction="neutral"),),
        cal_table=_table_with(_cal("X")),
    )
    assert p.base_rate_up > 0.55


def test_predict_intraday_baseline_symmetric() -> None:
    p = predict(
        ticker="AAPL", horizon="intraday",
        contributions=(_signal(name="X", direction="neutral"),),
        cal_table=_table_with(_cal("X")),
    )
    assert p.base_rate_up == pytest.approx(0.50)


# ── direction handling ────────────────────────────────────────────────────


def test_strong_up_signal_increases_up_probability() -> None:
    contribs = (_signal(name="A", value=2.5, direction="up", auc=0.62, n=500),)
    table = _table_with(_cal("A", auc=0.62, n=500))
    p = predict(
        ticker="X", horizon="swing_30d", contributions=contribs, cal_table=table,
    )
    assert p.up_probability > p.base_rate_up


def test_under_random_thesis_flips_direction() -> None:
    # AUC=0.35 → bias=-1 → "up" signal becomes effectively "down".
    # v1.4 (INV-GS-112): Brier weight is multiplied by confidence_v2; bump
    # the strength of the under-random thesis (more samples, lower AUC) to
    # confirm the directional flip survives the dampening.
    contribs = (_signal(name="FOMC", direction="up", auc=0.35, n=2000),)
    table = _table_with(_cal("FOMC", auc=0.35, n=2000))
    p = predict(
        ticker="X", horizon="swing_30d", contributions=contribs, cal_table=table,
    )
    # The flipped contribution drags down_probability above the down baseline.
    assert p.down_probability > p.up_probability


def test_skipped_signals_do_not_move_probability() -> None:
    base = predict(
        ticker="X", horizon="swing_30d",
        contributions=(_signal(name="A", direction="neutral", auc=0.586, n=298),),
        cal_table=_table_with(_cal("A")),
    )
    skip_signal = SignalContribution(
        name="A", value=None, direction="skip",
        calibration_auc=0.586, calibration_sharpe=0.629, n_samples=298,
        skip_reason="universe filter",
    )
    with_skip = predict(
        ticker="X", horizon="swing_30d",
        contributions=(
            skip_signal,
            _signal(name="A", direction="neutral", auc=0.586, n=298),
        ),
        cal_table=_table_with(_cal("A")),
    )
    assert base.up_probability == pytest.approx(with_skip.up_probability, abs=0.05)


def test_no_active_signals_falls_back_to_base_rate() -> None:
    # All theses inactive (n too small)
    weak = _signal(name="W", direction="up", auc=0.51, n=5)
    p = predict(
        ticker="X", horizon="swing_30d",
        contributions=(weak,),
        cal_table=_table_with(_cal("W", auc=0.51, n=5)),
    )
    assert p.up_probability == pytest.approx(p.base_rate_up, abs=0.03)


# ── expected return + CI ──────────────────────────────────────────────────


def test_expected_return_zero_for_neutral_signal() -> None:
    contribs = (_signal(name="A", value=0.0, direction="neutral"),)
    p = predict(
        ticker="X", horizon="swing_30d",
        contributions=contribs, cal_table=_table_with(_cal("A")),
    )
    assert p.expected_return_bps == pytest.approx(0.0, abs=1.0)


def test_confidence_interval_widens_with_dispersion() -> None:
    # v1.4 (INV-GS-112): confidence_v2 dampens Brier weights, so absolute CI
    # width shrinks. The invariant being tested is the relative widening from
    # disagreement, not the absolute magnitude.
    contribs = (
        _signal(name="A", value=2.0, direction="up", auc=0.586, n=300),
        _signal(name="B", value=-2.0, direction="down", auc=0.586, n=300),
    )
    table = CalibrationTable()
    table.entries["A"] = _cal("A")
    table.entries["B"] = _cal("B")
    p = predict(
        ticker="X", horizon="swing_30d",
        contributions=contribs, cal_table=table,
    )
    low, high = p.confidence_interval_bps
    assert high - low > 30.0  # disagreeing signals → wide CI (post-v1.4 dampening)


def test_confidence_interval_low_le_high_invariant() -> None:
    contribs = (_signal(name="A", value=2.5, direction="up"),)
    p = predict(
        ticker="X", horizon="swing_30d",
        contributions=contribs, cal_table=_table_with(_cal("A")),
    )
    low, high = p.confidence_interval_bps
    assert low <= high


# ── edge_over_baseline ────────────────────────────────────────────────────


def test_edge_over_baseline_is_pp_difference() -> None:
    contribs = (_signal(name="A", direction="up"),)
    p = predict(
        ticker="X", horizon="swing_30d",
        contributions=contribs, cal_table=_table_with(_cal("A")),
    )
    expected_edge = (p.up_probability - p.base_rate_up) * 100.0
    assert p.edge_over_baseline_pp == pytest.approx(expected_edge, abs=0.001)


# ── deterministic outputs ─────────────────────────────────────────────────


def test_predict_deterministic_for_same_inputs() -> None:
    issued = datetime(2026, 4, 29, 12, 0, tzinfo=UTC)
    contribs = (_signal(name="A", direction="up"),)
    table = _table_with(_cal("A"))
    p1 = predict(
        ticker="X", horizon="swing_30d", contributions=contribs,
        cal_table=table, issued_at=issued,
    )
    p2 = predict(
        ticker="X", horizon="swing_30d", contributions=contribs,
        cal_table=table, issued_at=issued,
    )
    assert p1.up_probability == p2.up_probability
    assert p1.expected_return_bps == p2.expected_return_bps
    assert p1.evidence_hash == p2.evidence_hash


# ── helpers ───────────────────────────────────────────────────────────────


def test_horizon_to_days_swing30d() -> None:
    assert horizon_to_days("swing_30d") == 30


def test_horizon_to_days_long3y() -> None:
    assert horizon_to_days("long_3y") == 1095


def test_horizon_to_timedelta_returns_correct_delta() -> None:
    td = horizon_to_timedelta("swing_5d")
    assert td.days == 5


# ── full mock pipeline ────────────────────────────────────────────────────


def test_predict_with_synthetic_calibration_and_mixed_signals() -> None:
    cal = synthetic_calibration_for_mock()
    contribs = (
        SignalContribution(
            name="E_FUNDAMENTAL", value=2.0, direction="up",
            calibration_auc=cal.entries["E_FUNDAMENTAL"].auc,
            calibration_sharpe=cal.entries["E_FUNDAMENTAL"].sharpe,
            n_samples=cal.entries["E_FUNDAMENTAL"].n_samples,
        ),
        SignalContribution(
            name="E_TIME", value=1.0, direction="up",
            calibration_auc=cal.entries["E_TIME"].auc,
            calibration_sharpe=cal.entries["E_TIME"].sharpe,
            n_samples=cal.entries["E_TIME"].n_samples,
        ),
        SignalContribution(
            name="E_FOMC_DRIFT", value=None, direction="skip",
            calibration_auc=cal.entries["E_FOMC_DRIFT"].auc,
            calibration_sharpe=cal.entries["E_FOMC_DRIFT"].sharpe,
            n_samples=cal.entries["E_FOMC_DRIFT"].n_samples,
            skip_reason="not in universe",
        ),
    )
    p = predict(
        ticker="AAPL", horizon="swing_30d",
        contributions=contribs, cal_table=cal,
    )
    assert p.ticker == "AAPL"
    assert p.up_probability > 0.45
    assert p.up_probability < 0.85
    assert p.active_signal_count == 2
    assert p.total_signal_count == 3


def test_predict_raises_on_empty_contributions() -> None:
    with pytest.raises(ValueError):
        predict(
            ticker="X", horizon="swing_30d",
            contributions=(),
            cal_table=CalibrationTable(),
        )


# ── v1.10.4: OOS-stability factor (INV-GS-133) ───────────────────────────


class TestOosStabilityFactor:
    def _cal_with_deg(
        self, *, deg: float, auc: float = 0.6, n: int = 200,
    ) -> ThesisCalibration:
        return ThesisCalibration(
            name="X", auc=auc, sharpe=0.5, n_samples=n,
            oos_degradation=deg,
            period_start=date(2024, 1, 1), period_end=date(2026, 3, 31),
        )

    def test_zero_degradation_no_penalty(self) -> None:
        cal = self._cal_with_deg(deg=0.0)
        assert _oos_stability_factor(cal) == 1.0

    def test_negative_degradation_no_penalty(self) -> None:
        # OOS Sharpe > IS Sharpe (rare but possible) — treat like zero deg.
        cal = self._cal_with_deg(deg=-0.3)
        assert _oos_stability_factor(cal) == 1.0

    def test_full_degradation_floors_to_min(self) -> None:
        cal = self._cal_with_deg(deg=1.0)
        assert _oos_stability_factor(cal) == pytest.approx(
            _OOS_STABILITY_FLOOR, abs=1e-9,
        )

    def test_extreme_degradation_clipped_to_floor(self) -> None:
        # E_PEAD-style 115% degradation → still floor.
        # E_FUNDING_CARRY-style 457% degradation → still floor.
        for deg in (1.156, 4.5741, 10.0):
            cal = self._cal_with_deg(deg=deg)
            assert _oos_stability_factor(cal) == pytest.approx(
                _OOS_STABILITY_FLOOR, abs=1e-9,
            )

    def test_moderate_degradation_linear_ramp(self) -> None:
        # deg=0.5 → factor = 1.0 - 0.9*0.5 = 0.55
        cal = self._cal_with_deg(deg=0.5)
        assert _oos_stability_factor(cal) == pytest.approx(0.55, abs=1e-6)


class TestWeightForAppliesOosFactor:
    def _cal(
        self, *, auc: float, n: int, oos_deg: float,
    ) -> ThesisCalibration:
        return ThesisCalibration(
            name="X", auc=auc, sharpe=0.5, n_samples=n,
            oos_degradation=oos_deg,
            period_start=date(2024, 1, 1), period_end=date(2026, 3, 31),
        )

    def test_high_auc_low_oos_deg_keeps_weight(self) -> None:
        cal = self._cal(auc=0.586, n=298, oos_deg=0.0)
        base = _brier_to_weight(cal.brier_score)
        final = _weight_for(cal)
        # No OOS degradation → final weight equals brier weight.
        assert final == pytest.approx(base, abs=1e-9)

    def test_high_auc_high_oos_deg_collapses_weight(self) -> None:
        # E_PEAD-style: AUC 0.586, n=298, oos_deg=1.156
        cal = self._cal(auc=0.586, n=298, oos_deg=1.156)
        base = _brier_to_weight(cal.brier_score)
        final = _weight_for(cal)
        # OOS degradation > 100% → final weight floored to base * 0.10
        assert final == pytest.approx(base * 0.10, abs=1e-6)
        # Concrete check: base ≈ 0.172, final ≈ 0.0172
        assert final < base * 0.15

    def test_inactive_thesis_still_zero(self) -> None:
        # n=10 < 50 threshold → is_active False → weight 0 regardless of OOS.
        cal = self._cal(auc=0.6, n=10, oos_deg=0.0)
        assert _weight_for(cal) == 0.0


class TestCompositeWeightingShiftFromV1101:
    """Regression tests anchoring the v1.10.4 OOS penalty in the composite."""

    def test_e_pead_weight_drops_after_oos_penalty(self) -> None:
        cal = synthetic_calibration_for_mock().entries["E_PEAD"]
        # E_PEAD has oos_degradation=1.156 → final weight floored.
        # Pre-v1.10.4: weight = 0.172. Post-v1.10.4: weight ≈ 0.0172.
        w = _weight_for(cal)
        assert w < 0.025

    def test_e_foreign_reversal_weight_unchanged(self) -> None:
        cal = synthetic_calibration_for_mock().entries["E_FOREIGN_REVERSAL"]
        # oos_degradation=0.0 → factor=1.0 → weight unchanged from brier_w.
        assert _weight_for(cal) == pytest.approx(
            _brier_to_weight(cal.brier_score), abs=1e-9,
        )

    def test_e_fundamental_weight_mildly_reduced(self) -> None:
        cal = synthetic_calibration_for_mock().entries["E_FUNDAMENTAL"]
        # oos_degradation=0.20 → factor=0.82 → mild reduction.
        base = _brier_to_weight(cal.brier_score)
        final = _weight_for(cal)
        assert 0.65 * base < final < 0.95 * base
