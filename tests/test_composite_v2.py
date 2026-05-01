from __future__ import annotations

from datetime import date

import pytest

from glostat.predictor.calibration import CalibrationTable, ThesisCalibration
from glostat.predictor.composite import predict
from glostat.predictor.types import SignalContribution


def _signal(
    *, name: str, value: float | None = 1.0, direction: str = "up",
    auc: float = 0.586, sharpe: float = 0.629, n: int = 298,
) -> SignalContribution:
    return SignalContribution(
        name=name, value=value, direction=direction,  # type: ignore[arg-type]
        calibration_auc=auc, calibration_sharpe=sharpe, n_samples=n,
    )


def _cal(
    name: str, *, auc: float = 0.586, sharpe: float = 0.629, n: int = 298,
    oos_deg: float = 0.0, end: date = date(2026, 4, 1),
) -> ThesisCalibration:
    return ThesisCalibration(
        name=name, auc=auc, sharpe=sharpe, n_samples=n,
        oos_degradation=oos_deg,
        period_start=date(2024, 1, 1), period_end=end,
    )


# ── composite + confidence_v2 (INV-GS-112) ───────────────────────────────


def test_high_n_stable_thesis_keeps_higher_weight() -> None:
    # Two theses with identical (auc, sharpe, direction) but different n.
    # The high-n one should drive p_up further from base_rate.
    sig_low = _signal(name="LOW", direction="up", auc=0.586, n=60, value=1.0)
    sig_high = _signal(name="HIGH", direction="up", auc=0.586, n=2000, value=1.0)
    table_low = CalibrationTable()
    table_low.entries["LOW"] = _cal("LOW", auc=0.586, n=60)
    table_high = CalibrationTable()
    table_high.entries["HIGH"] = _cal("HIGH", auc=0.586, n=2000)
    p_low = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig_low,), cal_table=table_low,
    )
    p_high = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig_high,), cal_table=table_high,
    )
    assert p_high.up_probability > p_low.up_probability


def test_stale_calibration_dampens_weight() -> None:
    # Same thesis (auc, n, sharpe) — only period_end differs. The fresh entry
    # should drive p_up closer to mass; the stale entry should drift toward
    # the base rate.
    sig = _signal(name="X", direction="up", auc=0.586, n=500, value=1.0)
    fresh_table = CalibrationTable()
    fresh_table.entries["X"] = _cal(
        "X", auc=0.586, n=500, end=date(2026, 4, 1),
    )
    stale_table = CalibrationTable()
    stale_table.entries["X"] = _cal(
        "X", auc=0.586, n=500, end=date(2024, 1, 1),
    )
    p_fresh = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig,), cal_table=fresh_table,
    )
    p_stale = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig,), cal_table=stale_table,
    )
    # Fresh signal pushes p_up further above base rate than the stale one.
    assert (p_fresh.up_probability - p_fresh.base_rate_up) > (
        p_stale.up_probability - p_stale.base_rate_up
    )


def test_oos_degraded_thesis_dampens_weight() -> None:
    # Same thesis with high vs zero OOS degradation. High degradation should
    # reduce return_consistency → composite confidence → effective weight.
    sig = _signal(name="X", direction="up", auc=0.586, n=500, value=1.0)
    clean_table = CalibrationTable()
    clean_table.entries["X"] = _cal(
        "X", auc=0.586, n=500, oos_deg=0.0, end=date(2026, 4, 1),
    )
    degraded_table = CalibrationTable()
    degraded_table.entries["X"] = _cal(
        "X", auc=0.586, n=500, oos_deg=2.0, end=date(2026, 4, 1),
    )
    p_clean = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig,), cal_table=clean_table,
    )
    p_degraded = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig,), cal_table=degraded_table,
    )
    assert (p_clean.up_probability - p_clean.base_rate_up) > (
        p_degraded.up_probability - p_degraded.base_rate_up
    )


# ── confidence_v2 attached to per-thesis SignalContribution ──────────────


def test_each_active_signal_has_confidence_v2_attached() -> None:
    sig = _signal(name="A", direction="up", value=1.0)
    table = CalibrationTable()
    table.entries["A"] = _cal("A")
    p = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig,), cal_table=table,
    )
    for s in p.contributing_signals:
        assert s.confidence_v2 is not None


def test_confidence_v2_components_reflect_thesis_quality() -> None:
    sig_strong = _signal(name="A", direction="up", auc=0.65, n=2000)
    table_strong = CalibrationTable()
    table_strong.entries["A"] = _cal("A", auc=0.65, n=2000)
    p_strong = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig_strong,), cal_table=table_strong,
    )
    conf = p_strong.contributing_signals[0].confidence_v2
    assert conf is not None
    assert conf.sample_quality > 0.7
    assert conf.effective_size_factor > 0.9


# ── SK이노베이션-style mock case (KR ticker, high-n stable) ──────────────


def test_sk_innovation_mock_high_n_thesis_gets_higher_weight() -> None:
    # Mock a "SK이노베이션" prediction: high-n stable AUC fundamental thesis.
    # Verify that confidence_v2 modulates the Brier weight upward (vs a
    # low-n same-AUC counterpart).
    high_n_sig = _signal(
        name="E_FUNDAMENTAL_KR", value=2.5, direction="up",
        auc=0.62, sharpe=0.7, n=1500,
    )
    high_n_table = CalibrationTable()
    high_n_table.entries["E_FUNDAMENTAL_KR"] = _cal(
        "E_FUNDAMENTAL_KR", auc=0.62, sharpe=0.7, n=1500,
        end=date(2026, 4, 1),
    )
    low_n_sig = _signal(
        name="E_FUNDAMENTAL_KR", value=2.5, direction="up",
        auc=0.62, sharpe=0.7, n=80,
    )
    low_n_table = CalibrationTable()
    low_n_table.entries["E_FUNDAMENTAL_KR"] = _cal(
        "E_FUNDAMENTAL_KR", auc=0.62, sharpe=0.7, n=80,
        end=date(2026, 4, 1),
    )
    p_high = predict(
        ticker="096770", horizon="swing_30d",
        contributions=(high_n_sig,), cal_table=high_n_table,
    )
    p_low = predict(
        ticker="096770", horizon="swing_30d",
        contributions=(low_n_sig,), cal_table=low_n_table,
    )
    # Higher-n thesis carries more lift over baseline.
    assert (p_high.up_probability - p_high.base_rate_up) > (
        p_low.up_probability - p_low.base_rate_up
    )


def test_dca_sizing_attached_after_predict() -> None:
    # Composite should attach SizingRecommendation as INFORMATION-only.
    sig = _signal(name="E_FUNDAMENTAL", direction="up", value=2.0)
    table = CalibrationTable()
    table.entries["E_FUNDAMENTAL"] = _cal("E_FUNDAMENTAL")
    p = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig,), cal_table=table,
    )
    assert p.dca_sizing is not None
    assert p.dca_sizing.tier in {
        "wait", "explore", "base", "active", "aggressive",
    }


# ── INV-GS-103 + INV-GS-112 interaction ──────────────────────────────────


def test_brier_weight_multiplied_by_confidence_v2() -> None:
    # If confidence_v2 is artificially low (n=0 placeholder thesis), the
    # composite must collapse to base rate even if direction is "up".
    sig_low_conf = _signal(
        name="PLACEHOLDER", value=2.0, direction="up", auc=0.51, n=0,
    )
    table = CalibrationTable()
    table.entries["PLACEHOLDER"] = _cal(
        "PLACEHOLDER", auc=0.51, n=0,
    )
    p = predict(
        ticker="X", horizon="swing_30d",
        contributions=(sig_low_conf,), cal_table=table,
    )
    # n=0 → confidence_v2 ≈ 0 → effective weight ≈ 0 → p_up ≈ base rate.
    assert p.up_probability == pytest.approx(p.base_rate_up, abs=0.05)
