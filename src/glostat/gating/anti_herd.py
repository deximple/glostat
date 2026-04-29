from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Final

from glostat.core.types import ExpertSignal

# MOET A2 — anti-herd discount (INV-GS-005).
# When ≥ N experts agree on the same direction, we apply a multiplicative
# discount to *every* signal's weight: over-consensus is itself a signal of
# noise (correlation collapse), not conviction.

DEFAULT_THRESHOLD: Final[int] = 4
DEFAULT_DISCOUNT: Final[float] = 0.80


def apply_anti_herd_discount(
    signals: Iterable[ExpertSignal],
    threshold: int = DEFAULT_THRESHOLD,
    discount: float = DEFAULT_DISCOUNT,
) -> dict[str, float]:
    sig_list = list(signals)
    if not sig_list:
        return {}
    counts = Counter(s.direction for s in sig_list)
    max_dir_count = max(counts.values())
    triggered = max_dir_count >= threshold
    multiplier = discount if triggered else 1.0
    return {s.expert_name: multiplier for s in sig_list}


def anti_herd_triggered(
    signals: Iterable[ExpertSignal], threshold: int = DEFAULT_THRESHOLD
) -> bool:
    sig_list = list(signals)
    if not sig_list:
        return False
    counts = Counter(s.direction for s in sig_list)
    return max(counts.values()) >= threshold


def majority_direction_count(
    signals: Iterable[ExpertSignal],
) -> tuple[str, int]:
    sig_list = list(signals)
    if not sig_list:
        return ("NEUTRAL", 0)
    counts = Counter(s.direction for s in sig_list)
    direction, count = max(counts.items(), key=lambda kv: kv[1])
    return (str(direction), int(count))


__all__ = [
    "DEFAULT_DISCOUNT",
    "DEFAULT_THRESHOLD",
    "anti_herd_triggered",
    "apply_anti_herd_discount",
    "majority_direction_count",
]
