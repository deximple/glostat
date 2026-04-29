from __future__ import annotations

import json

import pytest

from glostat.core.errors import ConfigError
from glostat.replay.sprint4_gate import (
    Sprint4Gate,
    evaluate_sprint4_gate,
    render_gate_table,
)


def _kwargs(**overrides: float | int | str) -> dict[str, float | int | str]:
    base: dict[str, float | int | str] = {
        "sharpe":          1.0,
        "oos_degradation": 0.10,
        "auc":             0.66,
        "cost_passed_pct": 0.55,
        "maxdd":           0.10,
        "reproducibility": 1.0,
        "n_verdicts":      100,
    }
    base.update(overrides)
    return base


def test_pass_when_all_thresholds_met() -> None:
    gate = evaluate_sprint4_gate(**_kwargs())  # type: ignore[arg-type]
    assert isinstance(gate, Sprint4Gate)
    assert gate.pass_status == "PASS"
    assert all(c.passed for c in gate.per_metric_breakdown)


def test_fail_when_sharpe_below_threshold() -> None:
    gate = evaluate_sprint4_gate(**_kwargs(sharpe=0.4))  # type: ignore[arg-type]
    assert gate.pass_status == "FAIL"
    failed_names = {c.name for c in gate.per_metric_breakdown if not c.passed}
    assert "sharpe" in failed_names


def test_fail_when_auc_below_threshold() -> None:
    gate = evaluate_sprint4_gate(**_kwargs(auc=0.50))  # type: ignore[arg-type]
    assert gate.pass_status == "FAIL"
    failed_names = {c.name for c in gate.per_metric_breakdown if not c.passed}
    assert "auc" in failed_names


def test_ambiguous_when_one_borderline() -> None:
    # Sharpe just under 0.8 within tol=0.05 → 1 borderline metric → AMBIGUOUS.
    gate = evaluate_sprint4_gate(**_kwargs(sharpe=0.78))  # type: ignore[arg-type]
    assert gate.pass_status == "AMBIGUOUS"
    assert "1 retry allowed" in gate.reasoning


def test_v031_pivot_strong_pass() -> None:
    gate = evaluate_sprint4_gate(  # type: ignore[arg-type]
        **_kwargs(sharpe=1.30, oos_degradation=0.10, auc=0.68, cost_passed_pct=0.55)
    )
    assert gate.pass_status == "PASS"
    assert gate.v031_pivot_eligible is True


def test_v031_pivot_not_eligible_when_only_passes_cautious() -> None:
    # Cautious passes but doesn't meet stricter v0.3.1 thresholds (sharpe 1.0 < 1.2).
    gate = evaluate_sprint4_gate(**_kwargs())  # type: ignore[arg-type]
    assert gate.pass_status == "PASS"
    assert gate.v031_pivot_eligible is False


def test_fail_when_oos_degradation_high() -> None:
    gate = evaluate_sprint4_gate(**_kwargs(oos_degradation=0.50))  # type: ignore[arg-type]
    assert gate.pass_status == "FAIL"


def test_fail_when_cost_passed_outside_band() -> None:
    gate = evaluate_sprint4_gate(**_kwargs(cost_passed_pct=0.20))  # type: ignore[arg-type]
    assert gate.pass_status == "FAIL"


def test_reproducibility_under_threshold_fails() -> None:
    gate = evaluate_sprint4_gate(**_kwargs(reproducibility=0.85))  # type: ignore[arg-type]
    assert gate.pass_status == "FAIL"


def test_serialization_roundtrip() -> None:
    gate = evaluate_sprint4_gate(**_kwargs())  # type: ignore[arg-type]
    payload = gate.to_json()
    parsed = json.loads(payload)
    assert parsed["pass_status"] == "PASS"
    assert parsed["profile"] == "cautious"
    assert "per_metric_breakdown" in parsed
    assert len(parsed["per_metric_breakdown"]) == 5


def test_render_gate_table_includes_all_metrics() -> None:
    gate = evaluate_sprint4_gate(**_kwargs())  # type: ignore[arg-type]
    text = render_gate_table(gate)
    assert "sharpe" in text
    assert "oos_degradation" in text
    assert "auc" in text
    assert "cost_passed_pct" in text
    assert "reproducibility" in text
    assert "[PASS]" in text


def test_unknown_profile_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        evaluate_sprint4_gate(**_kwargs(profile="bogus"))  # type: ignore[arg-type]


def test_balanced_profile_more_lenient() -> None:
    # Balanced sharpe_min = 0.6 < 0.8 → metric values that fail cautious can pass balanced.
    gate = evaluate_sprint4_gate(**_kwargs(sharpe=0.65, profile="balanced"))  # type: ignore[arg-type]
    assert gate.pass_status == "PASS"


def test_fail_returns_inv_gs_033_reason() -> None:
    gate = evaluate_sprint4_gate(**_kwargs(sharpe=0.4, auc=0.40))  # type: ignore[arg-type]
    assert gate.pass_status == "FAIL"
    assert "INV-GS-033" in gate.reasoning
