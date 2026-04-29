from __future__ import annotations

from typing import Final

from glostat.core.types import ComposedSignal, Verdict
from glostat.verdict_builder import compose_signals

# CLI helper — render the Gating breakdown line for `glostat predict`. Lives
# alongside cli.py so the main CLI module stays under the 400-line cap.

_LABEL_MAP: Final[dict[str, str]] = {
    "E_FUNDAMENTAL": "E_FUND",
    "E_FUND_FLOW": "E_FF",
    "E_TIME": "E_TIME",
    "E_NARRATIVE": "E_NARR",
    "E_MACRO": "E_MACRO",
}


def render_gating_breakdown(v: Verdict) -> list[str]:
    composed: ComposedSignal = compose_signals(list(v.contributing_signals))
    parts = [
        f"{_LABEL_MAP.get(name, name)} {weight * 100:.0f}%"
        for name, weight in composed.per_signal_weights
    ]
    direction_counts: dict[str, int] = {}
    for sig in v.contributing_signals:
        direction_counts[sig.direction] = direction_counts.get(sig.direction, 0) + 1
    top_dir, top_n = max(direction_counts.items(), key=lambda kv: kv[1])
    ah = "ON" if composed.applied_anti_herd else "OFF"
    mp = composed.applied_minority_premium
    minority_line = f"  minority_premium : {len(mp)}" + (
        f" ({', '.join(mp)})" if mp else ""
    )
    return [
        "Gating: " + " × ".join(parts),
        f"  anti_herd        : {ah} ({top_n} {top_dir})",
        minority_line,
    ]


def print_gating_breakdown(v: Verdict) -> None:
    for line in render_gating_breakdown(v):
        print(line)
    print()


__all__ = ["print_gating_breakdown", "render_gating_breakdown"]
