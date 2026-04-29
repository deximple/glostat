from __future__ import annotations

from datetime import UTC, date, datetime

from glostat.phase1b.orchestrator import render_comparison_md
from glostat.phase1b.types import PhaseHindcastReport
from glostat.replay.sprint4_gate import evaluate_sprint4_gate


def _make_report(name: str, sharpe: float, auc: float, cost: float) -> PhaseHindcastReport:
    return PhaseHindcastReport(
        expert=name,
        universe_size=10,
        n_trades=50,
        n_signals=50,
        n_skipped=0,
        is_sharpe=sharpe,
        oos_sharpe=sharpe * 0.7,
        overall_sharpe=sharpe,
        is_auc=auc,
        oos_auc=auc - 0.02,
        overall_auc=auc,
        cost_passed_pct=cost,
        expert_skip_pct=0.0,
        is_maxdd=0.05,
        oos_maxdd=0.07,
        determinism_verified=True,
        rows=(),
        notes=(f"test report for {name}",),
        sample_dates=(date(2024, 1, 1),),
        timestamp=datetime.now(tz=UTC),
    )


def _make_gate(rep: PhaseHindcastReport):
    return evaluate_sprint4_gate(
        sharpe=rep.overall_sharpe,
        oos_degradation=rep.oos_degradation,
        auc=rep.overall_auc,
        cost_passed_pct=rep.cost_passed_pct,
        maxdd=max(rep.is_maxdd, rep.oos_maxdd),
        reproducibility=1.0,
        n_verdicts=rep.n_signals,
        compliance_clean_days=90,
        profile="cautious",
    )


def test_render_comparison_md_includes_all_sections():
    a = _make_report("E_TEST_A", sharpe=1.0, auc=0.65, cost=0.50)
    b = _make_report("E_TEST_B", sharpe=-0.5, auc=0.45, cost=1.0)
    results = {
        "E_SECTOR_ROTATION": (a, _make_gate(a)),
        "E_PEAD": (b, _make_gate(b)),
    }
    md = render_comparison_md(
        results, start=date(2024, 1, 1), end=date(2026, 3, 29),
    )
    assert "Phase 1B" in md
    assert "Comparative gate table" in md
    assert "E_SECTOR_ROTATION" in md
    assert "E_PEAD" in md
    assert "PASS" in md or "FAIL" in md or "AMBIGUOUS" in md
    assert "Phase 2 promotion recommendation" in md


def test_recommendation_promotes_when_thresholds_met():
    # Rep A meets all criteria; Rep B fails Sharpe.
    a = _make_report("E_TEST_GOOD", sharpe=0.9, auc=0.62, cost=0.45)
    b = _make_report("E_TEST_BAD", sharpe=0.1, auc=0.50, cost=0.50)
    results = {
        "E_TEST_GOOD": (a, _make_gate(a)),
        "E_TEST_BAD": (b, _make_gate(b)),
    }
    md = render_comparison_md(results, start=date(2024, 1, 1), end=date(2024, 6, 1))
    assert "Promote to Phase 2 study" in md
    assert "E_TEST_GOOD" in md
    assert "Rejected" in md
    assert "E_TEST_BAD" in md
