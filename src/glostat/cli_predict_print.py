from __future__ import annotations

from glostat.cli_gating_print import print_gating_breakdown
from glostat.core.types import Verdict

# Render a Verdict in human-readable form. Split out of cli.py to keep cli.py
# under the 400-line house rule (PLAN_v0.6 §house rules).


def print_verdict(v: Verdict, *, disclaimer: str) -> None:
    print(f"=== GLOSTAT Verdict — {v.ticker} ({v.market}) ===")
    print(f"  action            : {v.action}")
    print(f"  conviction_w      : {v.conviction_w:.2f}")
    print(f"  suggested_size_pct: {v.suggested_size_pct:.2f}")
    print(f"  horizon_days      : {v.horizon_days}")
    print(f"  edge_bps          : {v.edge_bps:.2f}")
    print(f"  all_in_bps        : {v.all_in_bps:.2f}")
    print(f"  cost_passed       : {v.cost_passed}")
    print(f"  expected_pnl_bps  : {v.expected_pnl_bps:.2f}")
    if v.target_price is not None and v.stop_price is not None:
        print(f"  target / stop     : ${v.target_price:.2f} / ${v.stop_price:.2f}")
    print(f"  next_trigger      : {v.next_trigger}")
    print(f"  evidence_hash     : {v.evidence_hash}")
    print(f"  git_commit        : {v.git_commit[:12]}")
    print(f"  issued_at         : {v.issued_at.isoformat()}")
    print()
    print_gating_breakdown(v)
    print("Contributing signals:")
    for sig in v.contributing_signals:
        print(
            f"  - {sig.expert_name}: {sig.direction} (net={sig.net_score:+.2f}, "
            f"conf={sig.confidence:.2f})"
        )
        print(f"    basis: {sig.basis}")
        print(f"    sources: {', '.join(sig.sources)}")
    print()
    print(disclaimer)


__all__ = ["print_verdict"]
