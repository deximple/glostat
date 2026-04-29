from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True, slots=True)
class PhaseSignal:
    expert: str
    ticker: str
    day: date
    score: float                       # signed [-3, +3] convention
    direction: str                     # LONG / SHORT / NEUTRAL
    confidence: float                  # [0, 1]
    metadata: tuple[tuple[str, str], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class PhaseTradeRow:
    day: date
    ticker: str
    score: float
    direction: str                     # LONG / SHORT / NEUTRAL
    edge_bps: float
    cost_bps: float
    cost_passed: bool
    actual_fwd_return: float           # 30-day forward total return, fraction


@dataclass(frozen=True, slots=True)
class PhaseHindcastReport:
    expert: str
    universe_size: int
    n_trades: int
    n_signals: int
    n_skipped: int
    is_sharpe: float
    oos_sharpe: float
    overall_sharpe: float
    is_auc: float
    oos_auc: float
    overall_auc: float
    cost_passed_pct: float
    expert_skip_pct: float
    is_maxdd: float
    oos_maxdd: float
    determinism_verified: bool
    rows: tuple[PhaseTradeRow, ...] = field(default_factory=tuple)
    notes: tuple[str, ...] = field(default_factory=tuple)
    sample_dates: tuple[date, ...] = field(default_factory=tuple)
    timestamp: datetime | None = None

    @property
    def oos_degradation(self) -> float:
        if self.is_sharpe <= 0.0:
            return 1.0
        return max(0.0, 1.0 - (self.oos_sharpe / self.is_sharpe))


__all__ = ["PhaseHindcastReport", "PhaseSignal", "PhaseTradeRow"]
