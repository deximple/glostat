from __future__ import annotations

from datetime import date

import pytest

from glostat.predictor.calibration import CalibrationTable, ThesisCalibration
from glostat.predictor.composite import predict
from glostat.predictor.dca_sizing import (
    W_CAP,
    SizingRecommendation,
    build_sizing_recommendation,
    compute_w_value,
    w_to_sizing_recommendation,
)
from glostat.predictor.types import Prediction, SignalContribution


def _signal(
    *,
    name: str,
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


def _cal(
    name: str, *, auc: float = 0.586, sharpe: float = 0.629, n: int = 298
) -> ThesisCalibration:
    return ThesisCalibration(
        name=name, auc=auc, sharpe=sharpe, n_samples=n,
        oos_degradation=0.0,
        period_start=date(2024, 1, 1), period_end=date(2026, 3, 31),
    )


# ── tier mapping (TITAN parity) ───────────────────────────────────────────


def test_tier_wait_below_threshold() -> None:
    rec = w_to_sizing_recommendation(0.5)
    assert rec.tier == "wait"
    assert rec.suggested_entry_pct == pytest.approx(0.0)


def test_tier_explore_band() -> None:
    rec = w_to_sizing_recommendation(1.0)
    assert rec.tier == "explore"
    assert rec.suggested_entry_pct == pytest.approx(7.0)


def test_tier_base_band() -> None:
    rec = w_to_sizing_recommendation(1.5)
    assert rec.tier == "base"
    assert rec.suggested_entry_pct == pytest.approx(12.5)


def test_tier_active_band() -> None:
    rec = w_to_sizing_recommendation(2.0)
    assert rec.tier == "active"
    assert rec.suggested_entry_pct == pytest.approx(22.5)


def test_tier_aggressive_band() -> None:
    rec = w_to_sizing_recommendation(3.0)
    assert rec.tier == "aggressive"
    assert rec.suggested_entry_pct == pytest.approx(32.5)


def test_tier_thresholds_are_exclusive() -> None:
    # Boundary values: 0.8/1.2/1.8/2.5 are LOWER bounds for next tier.
    assert w_to_sizing_recommendation(0.799).tier == "wait"
    assert w_to_sizing_recommendation(0.8).tier == "explore"
    assert w_to_sizing_recommendation(1.199).tier == "explore"
    assert w_to_sizing_recommendation(1.2).tier == "base"
    assert w_to_sizing_recommendation(1.799).tier == "base"
    assert w_to_sizing_recommendation(1.8).tier == "active"
    assert w_to_sizing_recommendation(2.499).tier == "active"
    assert w_to_sizing_recommendation(2.5).tier == "aggressive"


# ── edge cases ────────────────────────────────────────────────────────────


def test_w_zero_is_wait() -> None:
    rec = w_to_sizing_recommendation(0.0)
    assert rec.tier == "wait"
    assert rec.suggested_entry_pct == pytest.approx(0.0)


def test_w_at_cap_is_aggressive() -> None:
    rec = w_to_sizing_recommendation(W_CAP)
    assert rec.tier == "aggressive"
    assert rec.w_value == pytest.approx(W_CAP)


def test_w_above_cap_is_clamped() -> None:
    rec = w_to_sizing_recommendation(99.0)
    assert rec.w_value == pytest.approx(W_CAP)
    assert rec.tier == "aggressive"


def test_w_negative_is_clamped_to_zero() -> None:
    rec = w_to_sizing_recommendation(-5.0)
    assert rec.tier == "wait"
    assert rec.w_value == pytest.approx(0.0)


def test_w_nan_falls_back_to_wait() -> None:
    rec = w_to_sizing_recommendation(float("nan"))
    assert rec.tier == "wait"


# ── SizingRecommendation invariants ───────────────────────────────────────


def test_sizing_recommendation_validates_pct_range() -> None:
    with pytest.raises(ValueError):
        SizingRecommendation(
            tier="base", suggested_entry_pct=150.0, w_value=1.5,
            w_components=(1.0, 1.0, 1.0, 1.0),
        )


def test_sizing_recommendation_validates_w_range() -> None:
    with pytest.raises(ValueError):
        SizingRecommendation(
            tier="base", suggested_entry_pct=12.5, w_value=999.0,
            w_components=(1.0, 1.0, 1.0, 1.0),
        )


def test_sizing_recommendation_requires_four_components() -> None:
    with pytest.raises(ValueError):
        SizingRecommendation(
            tier="base", suggested_entry_pct=12.5, w_value=1.5,
            w_components=(1.0, 1.0),  # type: ignore[arg-type]
        )


def test_sizing_disclaimer_references_inv_gs_111() -> None:
    rec = w_to_sizing_recommendation(1.5)
    assert "INV-GS-111" in rec.disclaimer


def test_sizing_disclaimer_does_not_say_buy_or_sell() -> None:
    # INV-GS-101 preservation — no action verbs in disclaimer.
    rec = w_to_sizing_recommendation(2.0)
    lower = rec.disclaimer.lower()
    assert "buy" not in lower
    assert "sell" not in lower


# ── compute_w_value from Prediction ───────────────────────────────────────


def _prediction_with(*signals: SignalContribution) -> Prediction:
    cal = CalibrationTable()
    for s in signals:
        cal.entries[s.name] = _cal(s.name, auc=s.calibration_auc, n=s.n_samples)
    return predict(
        ticker="X", horizon="swing_30d", contributions=tuple(signals),
        cal_table=cal,
    )


def test_compute_w_value_returns_in_cap_range() -> None:
    p = _prediction_with(_signal(name="E_FUNDAMENTAL", direction="up", value=2.0))
    w = compute_w_value(p)
    assert 0.0 <= w <= W_CAP


def test_build_sizing_recommendation_includes_components() -> None:
    p = _prediction_with(
        _signal(name="E_FUNDAMENTAL", direction="up", value=3.0),
        _signal(name="E_TIME", direction="up", value=1.5),
    )
    rec = build_sizing_recommendation(p)
    _r, t, v, _s = rec.w_components
    # E_TIME signal active → T > 0; E_FUNDAMENTAL signal active → V > 0.
    assert t > 0.0
    assert v > 0.0


def test_attached_sizing_present_on_prediction() -> None:
    p = _prediction_with(_signal(name="E_FUNDAMENTAL", direction="up"))
    assert p.dca_sizing is not None
    assert p.dca_sizing.tier in {"wait", "explore", "base", "active", "aggressive"}


# ── INV-GS-101 / INV-GS-111 preservation ─────────────────────────────────


def test_prediction_with_sizing_does_not_introduce_action_field() -> None:
    p = _prediction_with(_signal(name="E_FUNDAMENTAL", direction="up"))
    # Prediction must not gain an "action" field via dca_sizing attachment.
    assert not hasattr(p, "action")
    assert not hasattr(p, "target_price")
    assert not hasattr(p, "stop_price")
    # dca_sizing exists but its tier is INFORMATION, not action label.
    assert p.dca_sizing is not None
    assert p.dca_sizing.tier != "BUY"
    assert p.dca_sizing.tier != "SELL"


def test_high_conviction_prediction_yields_higher_tier() -> None:
    weak = _prediction_with(_signal(
        name="E_FUNDAMENTAL", direction="up", value=0.5, auc=0.51, n=80,
    ))
    strong = _prediction_with(
        _signal(name="E_FUNDAMENTAL", direction="up", value=3.0, auc=0.65, n=2000),
        _signal(name="E_TIME", direction="up", value=2.0, auc=0.62, n=2000),
    )
    weak_w = weak.dca_sizing.w_value if weak.dca_sizing else 0.0
    strong_w = strong.dca_sizing.w_value if strong.dca_sizing else 0.0
    assert strong_w > weak_w
