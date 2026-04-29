from __future__ import annotations

from datetime import date

import pytest

from glostat.phase1b.hindcast_runner import (
    HindcastConfig,
    build_report,
    compute_metrics,
    make_trade_row,
    split_is_oos,
    trading_days_between,
)
from glostat.phase1b.types import PhaseTradeRow


def _row(day, score=2.0, direction="LONG", fwd=0.05, cost_passed=True):
    return PhaseTradeRow(
        day=day, ticker="X",
        score=score, direction=direction,
        edge_bps=abs(score) * 50.0,
        cost_bps=2.52, cost_passed=cost_passed,
        actual_fwd_return=fwd,
    )


def test_make_trade_row_cost_gate_passes_when_score_high():
    row = make_trade_row(
        day=date(2024, 1, 1), ticker="X",
        score=2.0, direction="LONG", actual_fwd_return=0.05,
    )
    assert row.cost_passed
    assert row.direction == "LONG"
    assert row.edge_bps == 100.0


def test_make_trade_row_cost_gate_fails_when_score_tiny():
    row = make_trade_row(
        day=date(2024, 1, 1), ticker="X",
        score=0.01, direction="LONG", actual_fwd_return=0.05,
    )
    assert not row.cost_passed
    assert row.direction == "NEUTRAL"
    assert row.edge_bps == 0.5


def test_split_is_oos_70_30():
    rows = [_row(date(2024, 1, d)) for d in range(1, 11)]
    is_rows, oos_rows = split_is_oos(rows, ratio=0.7)
    assert len(is_rows) == 7
    assert len(oos_rows) == 3


def test_split_is_oos_handles_empty():
    is_rows, oos_rows = split_is_oos([])
    assert is_rows == ()
    assert oos_rows == ()


def test_compute_metrics_pure_long_with_alpha():
    rows = [
        _row(date(2024, 1, d), score=2.0, fwd=0.01 + (d % 3) * 0.005)
        for d in range(1, 21)
    ]
    sharpe, auc, mdd = compute_metrics(rows, horizon_days=30)
    assert sharpe > 0  # consistent positive (varying) returns → positive Sharpe
    # All actual_fwd_return positive → labels all 1 → AUC degenerate fallback 0.5.
    assert auc == 0.5


def test_compute_metrics_random_returns():
    rows = []
    for i in range(20):
        rows.append(
            _row(
                date(2024, 1, i + 1),
                score=2.0,
                fwd=0.01 if i % 2 == 0 else -0.01,
            )
        )
    sharpe, auc, mdd = compute_metrics(rows, horizon_days=30)
    # Mean ≈ 0, but variance > 0 — Sharpe driven entirely by rf shift, sign tied
    # to mean - rf/periods. Just check that AUC is bounded.
    assert 0.0 <= auc <= 1.0


def test_build_report_assembles_metrics():
    # Use mixed returns so std > 0 and Sharpe is non-zero.
    rows = [
        _row(date(2024, 1, d), score=2.0, fwd=0.005 + (d % 3) * 0.002)
        for d in range(1, 21)
    ]
    config = HindcastConfig(
        expert="E_TEST", universe_size=1, n_signals_attempted=25,
        n_signals_skipped=5, horizon_days=30,
    )
    rep = build_report(rows, config)
    assert rep.expert == "E_TEST"
    assert rep.n_signals == 20
    assert rep.n_skipped == 5
    assert rep.expert_skip_pct == pytest.approx(5 / 25)
    assert rep.cost_passed_pct == 1.0
    assert rep.is_sharpe > 0
    assert rep.timestamp is not None


def test_trading_days_between_excludes_weekends():
    days = trading_days_between(date(2024, 1, 1), date(2024, 1, 7))
    # 2024-01-01 Mon, 2 Tue, 3 Wed, 4 Thu, 5 Fri ; 6 Sat, 7 Sun
    assert len(days) == 5
    assert all(d.weekday() < 5 for d in days)
