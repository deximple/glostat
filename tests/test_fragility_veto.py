"""INV-GS-132 — fragility VETO axis (orthogonal to strength), feature-flagged OFF.

A thesis whose OOS edge has collapsed (oos_degradation >= threshold) is vetoed to
zero weight when GLOSTAT_FRAGILITY_VETO is enabled; live weights are unchanged
when the flag is off (the default).
"""

from __future__ import annotations

from datetime import date

import pytest

from glostat.predictor.calibration import ThesisCalibration
from glostat.predictor.composite import (
    OOS_DEGRADATION_VETO_THRESHOLD,
    _fragility_veto_enabled,
    _weight_for,
    _weight_for_v2,
    fragility_veto,
)
from glostat.predictor.confidence_v2 import confidence_v2_from_calibration

_FLAG = "GLOSTAT_FRAGILITY_VETO"


def _cal(*, oos_deg: float, auc: float = 0.586, n: int = 298) -> ThesisCalibration:
    return ThesisCalibration(
        name="E_TEST",
        auc=auc,
        sharpe=0.629,
        n_samples=n,
        oos_degradation=oos_deg,
        period_start=date(2024, 1, 1),
        period_end=date(2026, 4, 1),
    )


# ── pure veto ────────────────────────────────────────────────────────────────


def test_veto_passes_below_threshold():
    assert fragility_veto(_cal(oos_deg=0.0)) == 1.0
    assert fragility_veto(_cal(oos_deg=0.99)) == 1.0


def test_veto_fires_at_and_above_threshold():
    assert fragility_veto(_cal(oos_deg=1.0)) == 0.0  # boundary inclusive
    assert fragility_veto(_cal(oos_deg=1.156)) == 0.0  # E_FOREIGN_REVERSAL case
    assert fragility_veto(_cal(oos_deg=4.57)) == 0.0


def test_veto_threshold_is_configurable():
    assert fragility_veto(_cal(oos_deg=0.6), oos_threshold=0.5) == 0.0
    assert fragility_veto(_cal(oos_deg=0.6), oos_threshold=0.7) == 1.0


def test_threshold_constant_is_one():
    assert OOS_DEGRADATION_VETO_THRESHOLD == 1.0


# ── env flag parsing ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "val,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("", False),
        ("nope", False),
    ],
)
def test_flag_parsing(monkeypatch, val, expected):
    monkeypatch.setenv(_FLAG, val)
    assert _fragility_veto_enabled() is expected


def test_flag_absent_is_disabled(monkeypatch):
    monkeypatch.delenv(_FLAG, raising=False)
    assert _fragility_veto_enabled() is False


# ── _weight_for_v2 behaviour under the flag ──────────────────────────────────


def test_weight_unchanged_when_flag_off(monkeypatch):
    """Flag OFF (default): a fragile thesis keeps its un-vetoed strength weight."""
    monkeypatch.delenv(_FLAG, raising=False)
    cal = _cal(oos_deg=1.5)  # fragile
    conf = confidence_v2_from_calibration(cal)
    expected = _weight_for(cal) * conf.composite_confidence
    assert _weight_for_v2(cal, conf) == pytest.approx(expected)


def test_fragile_thesis_vetoed_to_zero_when_flag_on(monkeypatch):
    monkeypatch.setenv(_FLAG, "1")
    cal = _cal(oos_deg=1.5)  # fragile → vetoed
    conf = confidence_v2_from_calibration(cal)
    assert _weight_for_v2(cal, conf) == 0.0


def test_healthy_thesis_unaffected_by_flag(monkeypatch):
    """A non-fragile thesis is identical with the flag on or off (veto == 1)."""
    cal = _cal(oos_deg=0.0)  # healthy
    conf = confidence_v2_from_calibration(cal)
    monkeypatch.delenv(_FLAG, raising=False)
    off = _weight_for_v2(cal, conf)
    monkeypatch.setenv(_FLAG, "1")
    on = _weight_for_v2(cal, conf)
    assert off == pytest.approx(on)
    assert on > 0.0
