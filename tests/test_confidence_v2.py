from __future__ import annotations

import math
from datetime import date

import pytest

from glostat.predictor.calibration import ThesisCalibration
from glostat.predictor.confidence_v2 import (
    ConfidenceV2,
    compute_confidence_v2,
    confidence_v2_from_calibration,
)


def _cal(
    *, name: str = "X", auc: float = 0.586, sharpe: float = 0.629,
    n: int = 298, oos_deg: float = 0.0, end: date = date(2026, 4, 1),
) -> ThesisCalibration:
    return ThesisCalibration(
        name=name, auc=auc, sharpe=sharpe, n_samples=n,
        oos_degradation=oos_deg,
        period_start=date(2024, 1, 1), period_end=end,
    )


# ── ConfidenceV2 invariants ──────────────────────────────────────────────


def test_confidence_components_in_unit_interval() -> None:
    c = compute_confidence_v2(
        n_samples=298, is_sharpe=0.629, oos_sharpe=0.629,
        days_since_last_calibration=10.0, rolling_aucs=[0.58, 0.59, 0.59, 0.6],
    )
    for v in (c.sample_quality, c.effective_size_factor, c.score_stability,
              c.return_consistency, c.recency_quality, c.composite_confidence):
        assert 0.0 <= v <= 1.0


def test_confidence_validates_unit_range() -> None:
    with pytest.raises(ValueError):
        ConfidenceV2(
            sample_quality=2.0, effective_size_factor=0.5,
            score_stability=0.5, return_consistency=0.5,
            recency_quality=0.5, composite_confidence=0.5,
        )


# ── component-by-component behaviour ─────────────────────────────────────


def test_sample_quality_zero_when_no_samples() -> None:
    c = compute_confidence_v2(
        n_samples=0, is_sharpe=0.0, oos_sharpe=0.0,
        days_since_last_calibration=0.0,
    )
    assert c.sample_quality == pytest.approx(0.0)


def test_sample_quality_caps_at_one_above_n_1000() -> None:
    c_below = compute_confidence_v2(
        n_samples=1000, is_sharpe=0.0, oos_sharpe=0.0,
        days_since_last_calibration=0.0,
    )
    c_above = compute_confidence_v2(
        n_samples=10_000, is_sharpe=0.0, oos_sharpe=0.0,
        days_since_last_calibration=0.0,
    )
    assert c_below.sample_quality == pytest.approx(c_above.sample_quality)
    assert c_below.sample_quality == pytest.approx(1.0)


def test_effective_size_factor_bayesian_shrinkage() -> None:
    # n=50 → ratio = 50/100 = 0.5 → sqrt = ~0.707
    c = compute_confidence_v2(
        n_samples=50, is_sharpe=0.0, oos_sharpe=0.0,
        days_since_last_calibration=0.0,
    )
    assert c.effective_size_factor == pytest.approx(math.sqrt(0.5), abs=1e-6)


def test_score_stability_perfect_when_aucs_constant() -> None:
    c = compute_confidence_v2(
        n_samples=100, is_sharpe=0.5, oos_sharpe=0.5,
        days_since_last_calibration=0.0,
        rolling_aucs=[0.6, 0.6, 0.6, 0.6],
    )
    assert c.score_stability == pytest.approx(1.0)


def test_score_stability_drops_with_high_variance() -> None:
    stable = compute_confidence_v2(
        n_samples=100, is_sharpe=0.5, oos_sharpe=0.5,
        days_since_last_calibration=0.0,
        rolling_aucs=[0.55, 0.56, 0.55, 0.55],
    )
    unstable = compute_confidence_v2(
        n_samples=100, is_sharpe=0.5, oos_sharpe=0.5,
        days_since_last_calibration=0.0,
        rolling_aucs=[0.30, 0.85, 0.20, 0.95],
    )
    assert stable.score_stability > unstable.score_stability


def test_return_consistency_perfect_when_is_eq_oos() -> None:
    c = compute_confidence_v2(
        n_samples=100, is_sharpe=0.8, oos_sharpe=0.8,
        days_since_last_calibration=0.0,
    )
    assert c.return_consistency == pytest.approx(1.0)


def test_return_consistency_collapses_with_large_gap() -> None:
    c = compute_confidence_v2(
        n_samples=100, is_sharpe=2.0, oos_sharpe=-2.0,
        days_since_last_calibration=0.0,
    )
    assert c.return_consistency == pytest.approx(0.0)


def test_recency_quality_decays_with_age() -> None:
    fresh = compute_confidence_v2(
        n_samples=100, is_sharpe=0.5, oos_sharpe=0.5,
        days_since_last_calibration=0.0,
    )
    stale = compute_confidence_v2(
        n_samples=100, is_sharpe=0.5, oos_sharpe=0.5,
        days_since_last_calibration=180.0,
    )
    assert fresh.recency_quality > stale.recency_quality
    # exp(-180/90) = exp(-2) ≈ 0.135
    assert stale.recency_quality == pytest.approx(math.exp(-2.0), abs=1e-6)


def test_recency_quality_at_zero_is_one() -> None:
    c = compute_confidence_v2(
        n_samples=100, is_sharpe=0.5, oos_sharpe=0.5,
        days_since_last_calibration=0.0,
    )
    assert c.recency_quality == pytest.approx(1.0)


def test_recency_quality_clamps_negative_age_to_zero() -> None:
    c = compute_confidence_v2(
        n_samples=100, is_sharpe=0.5, oos_sharpe=0.5,
        days_since_last_calibration=-100.0,
    )
    assert c.recency_quality == pytest.approx(1.0)


# ── composite (geometric mean) ───────────────────────────────────────────


def test_composite_is_geometric_mean_of_components() -> None:
    c = compute_confidence_v2(
        n_samples=400, is_sharpe=0.5, oos_sharpe=0.5,
        days_since_last_calibration=0.0,
        rolling_aucs=[0.6, 0.6, 0.6, 0.6],
    )
    expected = math.exp(
        (math.log(c.sample_quality) + math.log(c.effective_size_factor)
         + math.log(c.score_stability) + math.log(c.return_consistency)
         + math.log(c.recency_quality)) / 5.0
    )
    assert c.composite_confidence == pytest.approx(expected, abs=1e-6)


def test_composite_collapses_when_one_component_is_zero() -> None:
    # n=0 → sample_quality=0, effective_size_factor=0 → composite ≈ 0.
    c = compute_confidence_v2(
        n_samples=0, is_sharpe=0.5, oos_sharpe=0.5,
        days_since_last_calibration=0.0, rolling_aucs=[0.6],
    )
    assert c.composite_confidence < 0.01


# ── from_calibration helper ──────────────────────────────────────────────


def test_from_calibration_uses_oos_degradation_for_consistency() -> None:
    cal_clean = _cal(n=200, sharpe=0.5, oos_deg=0.0)
    cal_degraded = _cal(n=200, sharpe=0.5, oos_deg=1.0)
    c_clean = confidence_v2_from_calibration(
        cal_clean, days_since_last_calibration=0.0,
    )
    c_degraded = confidence_v2_from_calibration(
        cal_degraded, days_since_last_calibration=0.0,
    )
    assert c_clean.return_consistency > c_degraded.return_consistency


def test_from_calibration_uses_period_end_when_age_unset() -> None:
    cal = _cal(n=200, end=date(2026, 1, 1))
    c = confidence_v2_from_calibration(cal)
    # period_end 2026-01-01; today is around 2026-04..05 → recency < 1.
    assert 0.0 < c.recency_quality < 1.0


def test_from_calibration_handles_missing_rolling_aucs() -> None:
    cal = _cal(n=200, auc=0.6)
    c = confidence_v2_from_calibration(cal, days_since_last_calibration=0.0)
    # With a single AUC point, score_stability = 1.0 (no variance).
    assert c.score_stability == pytest.approx(1.0)


def test_from_calibration_zero_n_collapses_composite() -> None:
    cal = _cal(n=0)
    c = confidence_v2_from_calibration(cal, days_since_last_calibration=0.0)
    assert c.composite_confidence < 0.01


# ── boundary values ─────────────────────────────────────────────────────


def test_compute_handles_is_sharpe_near_zero() -> None:
    # |is_sharpe| < 0.1 falls back to denom=0.1 floor.
    c = compute_confidence_v2(
        n_samples=100, is_sharpe=0.05, oos_sharpe=0.0,
        days_since_last_calibration=0.0,
    )
    # diff = 0.05; denom = 0.1; consistency = 1 - 0.5 = 0.5
    assert c.return_consistency == pytest.approx(0.5, abs=1e-6)
