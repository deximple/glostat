from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Final

# Pure metric helpers used by Hindcast.run() and unit tests. Kept separate from
# validation_harness.py so the math is testable without pipeline plumbing.
# Source formulas: docs/research/kill_criteria_design.md §1.1 ~ §1.5

_TRADING_DAYS_PER_YEAR: Final[int] = 252


def annualized_sharpe(
    daily_returns: Sequence[float],
    *,
    risk_free_rate_annual: float = 0.045,
    periods_per_year: int = _TRADING_DAYS_PER_YEAR,
) -> float:
    n = len(daily_returns)
    if n < 2:
        return 0.0
    rf_daily = risk_free_rate_annual / periods_per_year
    excess = [r - rf_daily for r in daily_returns]
    mean = sum(excess) / n
    var = sum((e - mean) ** 2 for e in excess) / (n - 1)
    std = math.sqrt(var)
    if std == 0.0:
        return 0.0
    return (mean / std) * math.sqrt(periods_per_year)


def cumulative_returns(daily_returns: Sequence[float]) -> list[float]:
    # WHY: compounded multiplicative cumulative return curve, starting at 1.0.
    out: list[float] = []
    cum = 1.0
    for r in daily_returns:
        cum *= 1.0 + r
        out.append(cum)
    return out


def max_drawdown(daily_returns: Sequence[float]) -> float:
    # Peak-to-trough on cumulative compounded curve. Returns positive magnitude.
    cum = cumulative_returns(daily_returns)
    if not cum:
        return 0.0
    peak = cum[0]
    mdd = 0.0
    for v in cum:
        peak = max(peak, v)
        if peak > 0:
            dd = (peak - v) / peak
            mdd = max(mdd, dd)
    return mdd


def auc_roc(scores: Sequence[float], labels: Sequence[int]) -> float:
    # ROC AUC via the rank-sum formula (Mann-Whitney U).
    # Tied scores get average rank; pure-Python so no scipy dependency.
    if len(scores) != len(labels):
        raise ValueError(
            f"auc_roc: scores and labels length mismatch ({len(scores)} vs {len(labels)})"
        )
    if not scores:
        return 0.5
    pos = [i for i, lbl in enumerate(labels) if lbl == 1]
    neg = [i for i, lbl in enumerate(labels) if lbl == 0]
    if not pos or not neg:
        return 0.5
    n_pos = len(pos)
    n_neg = len(neg)
    ranks = _average_ranks(scores)
    rank_sum_pos = sum(ranks[i] for i in pos)
    auc = (rank_sum_pos - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg)
    return max(0.0, min(1.0, auc))


def _average_ranks(values: Sequence[float]) -> list[float]:
    # Average ties; ranks are 1-indexed.
    indexed = sorted(enumerate(values), key=lambda x: x[1])
    ranks: list[float] = [0.0] * len(values)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def degradation(is_sharpe: float, oos_sharpe: float) -> float:
    if is_sharpe <= 0.0:
        return 1.0
    return max(0.0, 1.0 - (oos_sharpe / is_sharpe))


__all__ = [
    "annualized_sharpe",
    "auc_roc",
    "cumulative_returns",
    "degradation",
    "max_drawdown",
]
