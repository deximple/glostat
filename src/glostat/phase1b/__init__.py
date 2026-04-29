from __future__ import annotations

# Phase 1B — empirical hindcast of 4 free-stack equity alpha theses.
# Isolated package (does not touch archived production verdict pipeline).
# Each thesis: 1 Expert + 1 hindcast loop + Sprint 4 gate evaluation.
from glostat.phase1b.types import (
    PhaseHindcastReport,
    PhaseSignal,
    PhaseTradeRow,
)

__all__ = [
    "PhaseHindcastReport",
    "PhaseSignal",
    "PhaseTradeRow",
]
