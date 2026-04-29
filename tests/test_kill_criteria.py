from __future__ import annotations

import json

import pytest

from glostat.core.errors import ConfigError
from glostat.replay.kill_criteria import (
    HindcastMetricsView,
    KillCriteriaMonitor,
    KillDecision,
    KillThresholds,
)


def _passing_metrics() -> HindcastMetricsView:
    return HindcastMetricsView(
        sharpe=1.0,
        oos_degradation=0.10,
        auc=0.66,
        cost_passed_pct=0.55,
        maxdd=0.10,
        consecutive_violation_days=0,
        consecutive_oos_cycles_failed=0,
        compliance_clean_days=120,
    )


def test_evaluate_continue_when_all_pass() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    result = monitor.evaluate(_passing_metrics())
    assert result.decision is KillDecision.CONTINUE
    assert result.violated_metrics == ()
    assert result.borderline_metrics == ()


def test_evaluate_shutdown_when_sharpe_violation_sustained_5_days() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=0.5,                         # below 0.8
        oos_degradation=0.10,
        auc=0.66,
        cost_passed_pct=0.55,
        maxdd=0.10,
        consecutive_violation_days=5,       # sustained → SHUTDOWN
    )
    result = monitor.evaluate(metrics)
    assert result.decision is KillDecision.SHUTDOWN
    assert "sharpe_below_threshold_sustained" in result.violated_metrics


def test_evaluate_suspend_when_sharpe_violation_not_sustained() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=0.5,
        oos_degradation=0.10,
        auc=0.66,
        cost_passed_pct=0.55,
        maxdd=0.10,
        consecutive_violation_days=2,  # only 2 days < grace 5 → borderline
    )
    result = monitor.evaluate(metrics)
    assert result.decision is KillDecision.SUSPEND_7D
    assert "sharpe_below_threshold" in result.borderline_metrics


def test_evaluate_shutdown_immediate_on_maxdd_breach() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=1.0,
        oos_degradation=0.10,
        auc=0.66,
        cost_passed_pct=0.55,
        maxdd=0.20,                          # > 0.15 → immediate SHUTDOWN
        consecutive_violation_days=0,
    )
    result = monitor.evaluate(metrics)
    assert result.decision is KillDecision.SHUTDOWN
    assert "maxdd_exceeds_threshold" in result.violated_metrics


def test_evaluate_shutdown_immediate_on_auc_breach() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=1.0, oos_degradation=0.10, auc=0.55, cost_passed_pct=0.55,
        maxdd=0.10,
    )
    result = monitor.evaluate(metrics)
    assert result.decision is KillDecision.SHUTDOWN
    assert "auc_below_threshold" in result.violated_metrics


def test_evaluate_suspend_borderline_cost_passed() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=1.0, oos_degradation=0.10, auc=0.66,
        cost_passed_pct=0.30,                       # outside [0.40, 0.60]
        maxdd=0.10,
    )
    result = monitor.evaluate(metrics)
    assert result.decision is KillDecision.SUSPEND_7D
    assert "cost_passed_outside_band" in result.borderline_metrics


def test_inv_gs_033_no_silent_override() -> None:
    # SHUTDOWN cannot be downgraded to CONTINUE silently. Only explicit
    # defer_shutdown=True turns SHUTDOWN into SUSPEND_7D.
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=1.0, oos_degradation=0.10, auc=0.50, cost_passed_pct=0.55,
        maxdd=0.10,
    )
    no_override = monitor.evaluate(metrics, defer_shutdown=False)
    assert no_override.decision is KillDecision.SHUTDOWN

    with_override = monitor.evaluate(metrics, defer_shutdown=True)
    assert with_override.decision is KillDecision.SUSPEND_7D
    assert "DEFERRED-SHUTDOWN" in with_override.reason


def test_v031_pivot_eligibility_check_pass() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    # Strong-PASS thresholds: Sharpe ≥ 1.2, OOS ≤ 15%, AUC ≥ 0.65, cost ∈ [50%, 65%]
    metrics = HindcastMetricsView(
        sharpe=1.30, oos_degradation=0.10, auc=0.66,
        cost_passed_pct=0.55, maxdd=0.05,
        consecutive_violation_days=0, consecutive_oos_cycles_failed=0,
        compliance_clean_days=120,
    )
    result = monitor.evaluate(metrics)
    assert result.eligible_for_v031_pivot is True


def test_v031_pivot_eligibility_check_fail_when_sharpe_below() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=1.0, oos_degradation=0.10, auc=0.66,
        cost_passed_pct=0.55, maxdd=0.05,
        compliance_clean_days=120,
    )
    result = monitor.evaluate(metrics)
    assert result.eligible_for_v031_pivot is False


def test_v031_pivot_eligibility_fails_when_compliance_short() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=1.30, oos_degradation=0.10, auc=0.68,
        cost_passed_pct=0.55, maxdd=0.05,
        compliance_clean_days=10,                   # less than 90
    )
    result = monitor.evaluate(metrics)
    assert result.eligible_for_v031_pivot is False


def test_kill_decision_result_serialization() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    result = monitor.evaluate(_passing_metrics())
    payload = result.to_json()
    parsed = json.loads(payload)
    assert parsed["decision"] == "CONTINUE"
    assert parsed["profile"] == "cautious"
    assert "evidence" in parsed and isinstance(parsed["evidence"], dict)


def test_unknown_profile_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        KillCriteriaMonitor(profile="not_a_profile")  # type: ignore[arg-type]


def test_kill_thresholds_from_mapping_invalid_band_raises() -> None:
    bad = {
        "sharpe_min": 0.8,
        "oos_degradation_max": 0.30,
        "auc_min": 0.62,
        "cost_passed_band": [0.40],   # only one value
        "maxdd_max": 0.15,
    }
    with pytest.raises(ConfigError):
        KillThresholds.from_mapping(bad)


def test_oos_degradation_sustained_triggers_shutdown() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=1.0, oos_degradation=0.50, auc=0.66, cost_passed_pct=0.55,
        maxdd=0.10, consecutive_oos_cycles_failed=2,
    )
    result = monitor.evaluate(metrics)
    assert result.decision is KillDecision.SHUTDOWN
    assert "oos_degradation_sustained" in result.violated_metrics


def test_oos_degradation_single_cycle_only_borderline() -> None:
    monitor = KillCriteriaMonitor(profile="cautious")
    metrics = HindcastMetricsView(
        sharpe=1.0, oos_degradation=0.50, auc=0.66, cost_passed_pct=0.55,
        maxdd=0.10, consecutive_oos_cycles_failed=1,
    )
    result = monitor.evaluate(metrics)
    assert result.decision is KillDecision.SUSPEND_7D
    assert "oos_degradation_high" in result.borderline_metrics
