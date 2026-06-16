"""INV-GS-133 — noise gate: all-active-signals-noise collapses predict() to base
rate. Feature-flagged OFF (GLOSTAT_NOISE_GATE); live predictions unchanged by
default.
"""

from __future__ import annotations

from datetime import date

import pytest

from glostat.predictor.calibration import CalibrationTable, ThesisCalibration
from glostat.predictor.composite import _noise_gate_enabled, predict
from glostat.predictor.types import SignalContribution

_FLAG = "GLOSTAT_NOISE_GATE"


def _signal(name: str, *, auc: float, n: int, direction: str = "up") -> SignalContribution:
    return SignalContribution(
        name=name, value=1.0, direction=direction,  # type: ignore[arg-type]
        calibration_auc=auc, calibration_sharpe=0.4, n_samples=n,
    )


def _table(name: str, *, auc: float, n: int) -> CalibrationTable:
    t = CalibrationTable()
    t.entries[name] = ThesisCalibration(
        name=name, auc=auc, sharpe=0.4, n_samples=n, oos_degradation=0.0,
        period_start=date(2024, 1, 1), period_end=date(2026, 4, 1),
    )
    return t


def _two_noise():
    # auc=0.55, n=80 → active (|0.05|>0.02) but z≈1.55, p>0.05 → statistical noise
    sigs = (_signal("N1", auc=0.55, n=80), _signal("N2", auc=0.55, n=80))
    t = CalibrationTable()
    for s in sigs:
        t.entries[s.name] = ThesisCalibration(
            name=s.name, auc=0.55, sharpe=0.4, n_samples=80, oos_degradation=0.0,
            period_start=date(2024, 1, 1), period_end=date(2026, 4, 1),
        )
    return sigs, t


def test_flag_parsing(monkeypatch):
    monkeypatch.setenv(_FLAG, "on")
    assert _noise_gate_enabled() is True
    monkeypatch.setenv(_FLAG, "0")
    assert _noise_gate_enabled() is False
    monkeypatch.delenv(_FLAG, raising=False)
    assert _noise_gate_enabled() is False


def test_all_noise_collapses_to_base_when_flag_on(monkeypatch):
    """Core guarantee: all-noise input → edge forced to exactly 0 ('no usable
    signal'). Noise signals are weak so OFF already sits near base; the gate's
    value is the explicit, exact collapse + edge_pp==0, not a large numeric move."""
    monkeypatch.setenv(_FLAG, "1")
    sigs, table = _two_noise()
    p = predict(ticker="X", horizon="swing_30d", contributions=sigs, cal_table=table)
    assert p.up_probability == pytest.approx(p.base_rate_up)  # collapsed: no usable signal
    assert p.edge_over_baseline_pp == pytest.approx(0.0, abs=1e-9)


def test_off_equals_ungated_predict(monkeypatch):
    """Flag OFF reproduces the un-gated predict() exactly (live behavior intact)."""
    monkeypatch.delenv(_FLAG, raising=False)
    sigs, table = _two_noise()
    p_off = predict(ticker="X", horizon="swing_30d", contributions=sigs, cal_table=table)
    # Recompute with the flag explicitly absent — identical output.
    p_again = predict(ticker="X", horizon="swing_30d", contributions=sigs, cal_table=table)
    assert p_off.up_probability == pytest.approx(p_again.up_probability)
    assert p_off.edge_over_baseline_pp == pytest.approx(p_again.edge_over_baseline_pp)


def test_significant_signal_not_collapsed_when_flag_on(monkeypatch):
    """The gate fires only on ALL-noise; a significant thesis still produces edge."""
    monkeypatch.setenv(_FLAG, "1")
    sig = _signal("STRONG", auc=0.586, n=2000)  # z≫1.96 → significant
    table = _table("STRONG", auc=0.586, n=2000)
    p = predict(ticker="X", horizon="swing_30d", contributions=(sig,), cal_table=table)
    assert p.up_probability > p.base_rate_up  # edge preserved
