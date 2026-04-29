from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from glostat.cli import _load_market_meta
from glostat.cli_hindcast import _MockHindcastVerdictBuilder
from glostat.cli_mock_universe import (
    derive_actual_return_seed,
    synthetic_actual_30d_return,
    synthetic_signal_seed,
)
from glostat.replay.metrics import (
    annualized_sharpe,
    auc_roc,
    cumulative_returns,
    degradation,
    max_drawdown,
)
from glostat.replay.validation_harness import (
    Hindcast,
    HindcastReport,
    HindcastSplit,
    PassCriteria,
)

# ── helpers ────────────────────────────────────────────────────────────────


def _market_meta() -> Any:
    return _load_market_meta("XNAS")


def _build_hindcast(
    *, universe: tuple[str, ...] | None = None, horizon: int = 30
) -> Hindcast:
    universe = universe or ("AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN")
    market_meta = _market_meta()
    builder = _MockHindcastVerdictBuilder(market_meta=market_meta, horizon_days=horizon)

    async def verdict_for_day(t: str, d: date) -> Any:
        return builder.build(t, d)

    async def actual_for(t: str, d: date, h: int) -> float:
        return synthetic_actual_30d_return(t, d, horizon_days=h)

    return Hindcast(
        pipeline=None,
        universe=universe,
        verdict_for_day=verdict_for_day,
        actual_return_for=actual_for,
        horizon_days=horizon,
    )


# ── HindcastSplit ──────────────────────────────────────────────────────────


def test_hindcast_split_70_30_ratio() -> None:
    split = HindcastSplit.from_range(date(2026, 1, 1), date(2026, 4, 1), ratio=0.7)
    total = (date(2026, 4, 1) - date(2026, 1, 1)).days
    assert split.in_sample_days == round(total * 0.7)
    assert split.out_sample_start == split.in_sample_end + _one_day()


def test_hindcast_split_invalid_ratio_raises() -> None:
    with pytest.raises(ValueError):
        HindcastSplit.from_range(date(2026, 1, 1), date(2026, 4, 1), ratio=0.95)


def test_hindcast_split_end_before_start_raises() -> None:
    with pytest.raises(ValueError):
        HindcastSplit.from_range(date(2026, 4, 1), date(2026, 1, 1))


# ── pure metric helpers ───────────────────────────────────────────────────


def test_sharpe_calculation_known_returns() -> None:
    # Constant 0.001 daily return → mean / std → infinite ratio → return 0 by guard.
    s = annualized_sharpe([0.001] * 50, risk_free_rate_annual=0.0)
    assert s == 0.0


def test_sharpe_calculation_realistic_returns() -> None:
    # Daily returns with mean ≈ 0.0008, std ≈ 0.01 → Sharpe ≈ 1.27
    rs = [0.001 + (i % 5) * 0.0001 - 0.0002 for i in range(252)]
    s = annualized_sharpe(rs, risk_free_rate_annual=0.0)
    assert s > 0.0


def test_sharpe_handles_short_series() -> None:
    assert annualized_sharpe([0.01], risk_free_rate_annual=0.0) == 0.0
    assert annualized_sharpe([], risk_free_rate_annual=0.0) == 0.0


def test_max_drawdown_known_curve() -> None:
    # +10%, -5%, +5%, -10% → cumulative 1.10, 1.045, 1.097, 0.9876 → peak 1.10
    rs = [0.10, -0.05, 0.05, -0.10]
    mdd = max_drawdown(rs)
    cum = cumulative_returns(rs)
    expected = (max(cum) - min(cum)) / max(cum)
    assert math.isclose(mdd, expected, rel_tol=1e-9)


def test_max_drawdown_monotonic_up() -> None:
    assert max_drawdown([0.01, 0.01, 0.01, 0.01]) == 0.0


def test_max_drawdown_empty_returns_zero() -> None:
    assert max_drawdown([]) == 0.0


def test_auc_perfect_discrimination() -> None:
    scores = [0.1, 0.2, 0.3, 0.4]
    labels = [0, 0, 1, 1]
    assert auc_roc(scores, labels) == 1.0


def test_auc_random_discrimination_near_half() -> None:
    scores = list(range(100))
    labels = [i % 2 for i in range(100)]
    auc = auc_roc(scores, labels)
    assert 0.45 <= auc <= 0.55


def test_auc_all_same_label_returns_half() -> None:
    assert auc_roc([0.1, 0.2, 0.3], [1, 1, 1]) == 0.5
    assert auc_roc([0.1, 0.2, 0.3], [0, 0, 0]) == 0.5


def test_auc_length_mismatch_raises() -> None:
    with pytest.raises(ValueError):
        auc_roc([0.1, 0.2], [1])


def test_degradation_no_loss_when_oos_better() -> None:
    assert degradation(0.8, 1.2) == 0.0


def test_degradation_calculation() -> None:
    assert math.isclose(degradation(1.0, 0.7), 0.30, rel_tol=1e-9)


def test_degradation_zero_is_returns_full_loss() -> None:
    assert degradation(0.0, 1.0) == 1.0


# ── Hindcast.run integration ──────────────────────────────────────────────


def test_hindcast_run_returns_report() -> None:
    hc = _build_hindcast()
    report = hc.run(start_date=date(2026, 1, 5), end_date=date(2026, 1, 30), split=0.7)
    assert isinstance(report, HindcastReport)
    assert report.n_verdicts > 0
    assert report.days_evaluated > 0


def test_hindcast_is_oos_split_70_30() -> None:
    hc = _build_hindcast()
    report = hc.run(start_date=date(2026, 1, 5), end_date=date(2026, 2, 28), split=0.7)
    is_count = sum(1 for r in report.rows if r.day <= report.split.in_sample_end)
    oos_count = sum(1 for r in report.rows if r.day > report.split.in_sample_end)
    assert is_count > 0 and oos_count > 0
    assert is_count > oos_count  # 70/30 split → IS larger


def test_hindcast_cost_passed_ratio_in_band() -> None:
    hc = _build_hindcast()
    report = hc.run(start_date=date(2026, 1, 5), end_date=date(2026, 4, 28), split=0.7)
    assert 0.30 <= report.cost_passed_pct <= 0.75


def test_hindcast_reproducibility_via_snapshot_replay() -> None:
    # Mock builder always sets snapshot_replay_match=True → reproducibility = 100%.
    hc = _build_hindcast()
    report = hc.run(start_date=date(2026, 1, 5), end_date=date(2026, 1, 30), split=0.7)
    assert report.reproducibility >= 0.999
    assert report.determinism_verified is True


def test_hindcast_deterministic_same_seed() -> None:
    hc1 = _build_hindcast()
    hc2 = _build_hindcast()
    r1 = hc1.run(start_date=date(2026, 1, 5), end_date=date(2026, 1, 30), split=0.7)
    r2 = hc2.run(start_date=date(2026, 1, 5), end_date=date(2026, 1, 30), split=0.7)
    assert r1.seed == r2.seed
    assert r1.n_verdicts == r2.n_verdicts
    assert math.isclose(r1.overall_sharpe, r2.overall_sharpe, rel_tol=1e-12)
    assert math.isclose(r1.overall_auc, r2.overall_auc, rel_tol=1e-12)


def test_hindcast_mock_passes_cautious_thresholds_50_universe() -> None:
    # The headline acceptance: 50-ticker mock universe over 90 days → cautious gate PASS.
    universe = ("AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "JPM",
                "V", "MA", "BRK.B", "UNH", "LLY", "JNJ", "ABBV", "MRK", "TMO",
                "ABT", "PG", "WMT", "COST", "KO", "PEP", "HD", "MCD", "AVGO",
                "ORCL", "CRM", "AMD", "ADBE", "CSCO", "ACN", "IBM", "NOW", "INTU",
                "NFLX", "DIS", "T", "BAC", "WFC", "AXP", "MS", "GS", "XOM",
                "CVX", "GE", "CAT", "RTX", "LIN", "ISRG")
    hc = _build_hindcast(universe=universe)
    report = hc.run(start_date=date(2026, 1, 29), end_date=date(2026, 4, 28), split=0.7)
    # Acceptance: Sharpe ≥ 0.8, ≤ 1.5; AUC ≥ 0.62; OOS deg ≤ 30%; reproducibility ≥ 99.9%.
    assert report.overall_sharpe >= 0.8, f"Sharpe {report.overall_sharpe:.4f}"
    assert report.overall_sharpe <= 1.5, f"Sharpe {report.overall_sharpe:.4f}"
    assert report.overall_auc >= 0.62, f"AUC {report.overall_auc:.4f}"
    assert report.degradation() <= 0.30, f"OOS deg {report.degradation():.4f}"
    assert report.reproducibility >= 0.999
    # Sprint 5 PR #1 cost_gate retune (NET_SCORE_TO_BPS halved 100 → 50)
    # nudges the mock cost_passed pct down ~50% → wider band 30-65%.
    assert 0.30 <= report.cost_passed_pct <= 0.65, (
        f"cost_passed {report.cost_passed_pct:.4f}"
    )


def test_hindcast_pipeline_none_returns_stub() -> None:
    hc = Hindcast(pipeline=None, universe=("AAPL",), verdict_for_day=None,
                  actual_return_for=None)
    report = hc.run(start_date=date(2026, 1, 5), end_date=date(2026, 1, 30))
    assert report.n_verdicts == 0
    assert "stub" in report.notes[0]


def test_hindcast_pass_criteria_evaluate_pass() -> None:
    report = _fake_report(is_sharpe=1.0, oos_sharpe=0.85, auc=0.65, cost=0.55)
    assert PassCriteria().evaluate(report) == "PASS"


def test_hindcast_pass_criteria_evaluate_fail() -> None:
    # All checks fail → FAIL. Force determinism off so every check is False.
    report = _fake_report(
        is_sharpe=0.1, oos_sharpe=0.05, auc=0.50, cost=0.10,
        determinism=False,
    )
    assert PassCriteria().evaluate(report) == "FAIL"


def test_hindcast_pass_criteria_evaluate_ambiguous() -> None:
    # Some pass, some fail → AMBIGUOUS.
    report = _fake_report(is_sharpe=0.1, oos_sharpe=0.05, auc=0.50, cost=0.10)
    assert PassCriteria().evaluate(report) == "AMBIGUOUS"


def test_hindcast_synthetic_seeds_correlate() -> None:
    # Signal seed and actual seed are different but related — both depend on
    # (ticker, day). Same seeds reproduce same returns.
    r1 = synthetic_actual_30d_return("AAPL", date(2026, 2, 1))
    r2 = synthetic_actual_30d_return("AAPL", date(2026, 2, 1))
    assert r1 == r2
    s1 = synthetic_signal_seed("AAPL", date(2026, 2, 1))
    s2 = synthetic_signal_seed("AAPL", date(2026, 2, 1))
    assert s1 == s2
    a_seed = derive_actual_return_seed("AAPL", date(2026, 2, 1), 30)
    assert "AAPL" in a_seed and "2026-02-01" in a_seed


# ── helpers ────────────────────────────────────────────────────────────────


def _one_day() -> Any:
    return timedelta(days=1)


def _fake_report(
    *,
    is_sharpe: float,
    oos_sharpe: float,
    auc: float,
    cost: float,
    determinism: bool = True,
) -> HindcastReport:
    split = HindcastSplit.from_range(date(2026, 1, 1), date(2026, 4, 1))
    return HindcastReport(
        split=split,
        is_sharpe=is_sharpe,
        oos_sharpe=oos_sharpe,
        is_auc=auc,
        oos_auc=auc,
        is_max_drawdown=0.05,
        oos_max_drawdown=0.05,
        cost_passed_pct=cost,
        determinism_verified=determinism,
        n_verdicts=100,
        seed=42,
        overall_sharpe=(is_sharpe + oos_sharpe) / 2,
        overall_auc=auc,
        overall_maxdd=0.05,
        reproducibility=1.0 if determinism else 0.5,
        days_evaluated=60,
    )


# Suppress unused-import warning for the asyncio module — Hindcast.run uses it under the hood.
_ = asyncio
_ = Awaitable
_ = datetime
_ = UTC
