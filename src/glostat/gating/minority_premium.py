from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable
from typing import Final

from glostat.core.types import ExpertSignal

# MOET A3 — minority premium + Meta-adjudicator (PLAN_v0.4 §0.3).
# When a single dissenting Expert disagrees with the herd, its signal gets a
# 1.15× boost IFF the Meta-adjudicator approves. MVP: approve_callback defaults
# to always-True (no LLM cost). Phase 2 will plug an LLM here that vetoes
# flimsy contrarians.

DEFAULT_BOOST: Final[float] = 1.15

ApproveFn = Callable[[ExpertSignal], bool]


def _always_approve(_signal: ExpertSignal) -> bool:
    return True


def apply_minority_premium(
    signals: Iterable[ExpertSignal],
    boost: float = DEFAULT_BOOST,
    approve_callback: ApproveFn | None = None,
) -> dict[str, float]:
    sig_list = list(signals)
    if not sig_list:
        return {}
    approve = approve_callback or _always_approve

    counts = Counter(s.direction for s in sig_list)
    # WHY: minority = the *least*-represented direction with at least one signal.
    # Tie among directions → no single minority, no boost (avoid double-promotion).
    min_count = min(counts.values())
    min_dirs = [d for d, c in counts.items() if c == min_count]
    if len(min_dirs) != 1 or len(counts) <= 1:
        # Either everyone agrees (no minority) or multiple-way tie (ambiguous).
        return {s.expert_name: 1.0 for s in sig_list}
    minority_dir = min_dirs[0]

    out: dict[str, float] = {}
    for s in sig_list:
        if s.direction == minority_dir and approve(s):
            out[s.expert_name] = boost
        else:
            out[s.expert_name] = 1.0
    return out


def boosted_experts(multipliers: dict[str, float], boost: float = DEFAULT_BOOST) -> tuple[str, ...]:
    return tuple(sorted(name for name, m in multipliers.items() if m >= boost))


__all__ = [
    "DEFAULT_BOOST",
    "ApproveFn",
    "apply_minority_premium",
    "boosted_experts",
]
