from __future__ import annotations

from glostat.cli_gating_print import print_gating_breakdown
from glostat.core.types import Verdict
from glostat.predictor.types import Prediction

# Render a Verdict (legacy v0.7) or Prediction (v1.0) in human-readable form.
# Split out of cli.py to keep cli.py under the 400-line house rule.


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


def print_prediction(p: Prediction) -> None:
    print(f"=== GLOSTAT Prediction — {p.ticker} ({p.market}) ===")
    print(f"  horizon       : {p.horizon}")
    print(
        f"  up / down / sideways: "
        f"{p.up_probability * 100:.1f}% / "
        f"{p.down_probability * 100:.1f}% / "
        f"{p.sideways_probability * 100:.1f}%"
    )
    low, high = p.confidence_interval_bps
    print(
        f"  expected return: {p.expected_return_bps:+.0f}bps  "
        f"(CI: {low:+.0f}bps .. {high:+.0f}bps)"
    )
    print(f"  base rate up  : {p.base_rate_up * 100:.1f}%")
    print(f"  edge over baseline: {p.edge_over_baseline_pp:+.1f}pp")
    print()
    print(
        f"Contributing signals (active {p.active_signal_count} / "
        f"total {p.total_signal_count}):"
    )
    for s in p.contributing_signals:
        if s.direction == "skip":
            reason = s.skip_reason or "skipped"
            short = reason if len(reason) <= 50 else reason[:47] + "..."
            print(f"  {s.name:<22} . skip   ({short})")
            continue
        arrow = {"up": "^", "down": "v", "neutral": "-"}[s.direction]
        val = f"{s.value:+.2f}" if s.value is not None else "n/a"
        print(
            f"  {s.name:<22} {arrow} {val:>7}  "
            f"(AUC {s.calibration_auc:.3f}, n={s.n_samples})"
        )
    print()
    print("Next triggers:")
    for t in p.next_triggers:
        print(f"  - {t}")
    print()
    cal_start, cal_end = p.calibration_period
    print(p.disclaimer)
    print(
        f"Calibration period: {cal_start.isoformat()} → {cal_end.isoformat()}"
    )
    print(f"evidence_hash: sha256:{p.evidence_hash[:16]}...")
    print(f"issued_at:     {p.issued_at.isoformat()}")
    print(f"git_commit:    {p.git_commit[:12]}")


__all__ = ["print_prediction", "print_verdict"]
