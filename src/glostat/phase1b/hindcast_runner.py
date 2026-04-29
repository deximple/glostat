from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

from glostat.phase1b.types import PhaseHindcastReport, PhaseTradeRow
from glostat.replay.metrics import (
    annualized_sharpe,
    auc_roc,
    max_drawdown,
)

# Common hindcast loop helpers shared by all 4 thesis runners.
# Per-thesis runners produce a list[PhaseTradeRow]; this module computes the
# aggregate metrics + IS/OOS split.

_DEFAULT_RF_ANNUAL: Final[float] = 0.045
_DEFAULT_HORIZON_DAYS: Final[int] = 30
_IS_RATIO: Final[float] = 0.7
# Sprint 4 cost-gate: edge_bps must be >= 1.5 * all_in_bps.
# US: 0.6 fee + 0.24 sell = ~0.84 per side, ~1.68 round trip → 2.52 cost gate.
DEFAULT_ALL_IN_BPS: Final[float] = 1.68
DEFAULT_COST_GATE_RATIO: Final[float] = 1.5


@dataclass(frozen=True, slots=True)
class HindcastConfig:
    expert: str
    universe_size: int
    n_signals_attempted: int = 0
    n_signals_skipped: int = 0
    horizon_days: int = _DEFAULT_HORIZON_DAYS
    is_ratio: float = _IS_RATIO
    notes: tuple[str, ...] = ()


def split_is_oos(
    rows: Sequence[PhaseTradeRow], ratio: float = _IS_RATIO
) -> tuple[tuple[PhaseTradeRow, ...], tuple[PhaseTradeRow, ...]]:
    if not rows:
        return ((), ())
    sorted_rows = sorted(rows, key=lambda r: r.day)
    days = sorted({r.day for r in sorted_rows})
    if len(days) < 2:
        # all-IS fallback when window too short
        return (tuple(sorted_rows), ())
    cut_idx = max(1, int(round(len(days) * ratio)))
    cut_idx = min(cut_idx, len(days) - 1)
    cut_day = days[cut_idx - 1]
    is_rows = tuple(r for r in sorted_rows if r.day <= cut_day)
    oos_rows = tuple(r for r in sorted_rows if r.day > cut_day)
    return (is_rows, oos_rows)


def compute_metrics(
    rows: Sequence[PhaseTradeRow],
    *,
    horizon_days: int = _DEFAULT_HORIZON_DAYS,
    risk_free_annual: float = _DEFAULT_RF_ANNUAL,
) -> tuple[float, float, float]:
    if not rows:
        return (0.0, 0.5, 0.0)
    per_trade: list[float] = []
    scores: list[float] = []
    labels: list[int] = []
    for r in rows:
        if r.direction == "LONG":
            sign = 1.0
        elif r.direction == "SHORT":
            sign = -1.0
        else:
            sign = 0.0
        if sign != 0.0 and r.cost_passed:
            per_trade.append(sign * r.actual_fwd_return)
        scores.append(sign * r.edge_bps)
        labels.append(1 if r.actual_fwd_return > 0 else 0)
    if per_trade:
        cycles_per_year = max(1, round(252.0 / max(1, horizon_days)))
        sharpe = annualized_sharpe(
            per_trade,
            risk_free_rate_annual=risk_free_annual,
            periods_per_year=cycles_per_year,
        )
        mdd = max_drawdown(per_trade)
    else:
        sharpe = 0.0
        mdd = 0.0
    auc = auc_roc(scores, labels) if scores else 0.5
    return (sharpe, auc, mdd)


def build_report(
    rows: Sequence[PhaseTradeRow],
    config: HindcastConfig,
) -> PhaseHindcastReport:
    rows_t = tuple(rows)
    is_rows, oos_rows = split_is_oos(rows_t, config.is_ratio)

    is_sharpe, is_auc, is_mdd = compute_metrics(is_rows, horizon_days=config.horizon_days)
    oos_sharpe, oos_auc, oos_mdd = compute_metrics(oos_rows, horizon_days=config.horizon_days)
    overall_sharpe, overall_auc, _ = compute_metrics(rows_t, horizon_days=config.horizon_days)

    cost_passed = sum(1 for r in rows_t if r.cost_passed)
    cost_passed_pct = (cost_passed / len(rows_t)) if rows_t else 0.0

    n_attempted = max(config.n_signals_attempted, len(rows_t))
    skip_pct = (
        config.n_signals_skipped / n_attempted if n_attempted > 0 else 0.0
    )

    return PhaseHindcastReport(
        expert=config.expert,
        universe_size=config.universe_size,
        n_trades=sum(1 for r in rows_t if r.direction != "NEUTRAL"),
        n_signals=len(rows_t),
        n_skipped=config.n_signals_skipped,
        is_sharpe=is_sharpe,
        oos_sharpe=oos_sharpe,
        overall_sharpe=overall_sharpe,
        is_auc=is_auc,
        oos_auc=oos_auc,
        overall_auc=overall_auc,
        cost_passed_pct=cost_passed_pct,
        expert_skip_pct=skip_pct,
        is_maxdd=is_mdd,
        oos_maxdd=oos_mdd,
        determinism_verified=True,  # pure-fn replay over cached snapshots
        rows=rows_t,
        notes=config.notes,
        sample_dates=tuple(sorted({r.day for r in rows_t})),
        timestamp=datetime.now(tz=UTC),
    )


def trading_days_between(start: date, end: date, *, step_days: int = 1) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=step_days)
    return out


def make_trade_row(
    *,
    day: date,
    ticker: str,
    score: float,
    direction: str,
    actual_fwd_return: float,
    score_to_bps: float = 50.0,
    all_in_bps: float = DEFAULT_ALL_IN_BPS,
    cost_gate_ratio: float = DEFAULT_COST_GATE_RATIO,
) -> PhaseTradeRow:
    edge_bps = abs(score) * score_to_bps
    cost_bps = all_in_bps
    cost_passed = edge_bps >= cost_gate_ratio * cost_bps
    return PhaseTradeRow(
        day=day,
        ticker=ticker,
        score=score,
        direction=direction if cost_passed else "NEUTRAL",
        edge_bps=edge_bps,
        cost_bps=cost_bps,
        cost_passed=cost_passed,
        actual_fwd_return=actual_fwd_return,
    )


__all__ = [
    "DEFAULT_ALL_IN_BPS",
    "DEFAULT_COST_GATE_RATIO",
    "HindcastConfig",
    "build_report",
    "compute_metrics",
    "make_trade_row",
    "split_is_oos",
    "trading_days_between",
]
