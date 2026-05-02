from __future__ import annotations

from glostat.cli_gating_print import print_gating_breakdown
from glostat.core.types import Verdict
from glostat.predictor.honesty import (
    all_active_signals_are_noise,
    ci_includes_zero,
    format_significance,
    kr_megacap_honesty_note,
    round_trip_bps,
)
from glostat.predictor.types import Prediction, SignalContribution

# Render a Verdict (legacy v0.7) or Prediction (v1.0) in human-readable form.
# Split out of cli.py to keep cli.py under the 400-line house rule.
# v1.4.1 (X+W patch): print_prediction adds statistical-honesty annotations
# (INV-GS-113) and KR megacap universe-specific honesty footer (INV-GS-114).


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
    # X4: cost-net expected return so the user reads the figure that survives
    # round-trip transaction costs (KR megacap = ~23 bps, US = ~1.4 bps).
    cost_bps = round_trip_bps(p.market)
    net_bps = p.expected_return_bps - cost_bps
    if cost_bps > 0.5:
        print(
            f"  expected return: {p.expected_return_bps:+.0f}bps gross / "
            f"{net_bps:+.0f}bps net (after {cost_bps:.0f}bps round-trip cost)"
        )
    else:
        print(f"  expected return: {p.expected_return_bps:+.0f}bps")
    # X1: CI label clarifies the 1-sigma (~68%) interval; flag CI-includes-0.
    low, high = p.confidence_interval_bps
    ci_flag = "  *** includes 0 -> no clear direction" if ci_includes_zero(low, high) else ""
    print(
        f"  CI 1-sigma (68%): {low:+.0f}bps .. {high:+.0f}bps"
        f"{ci_flag}"
    )
    print(f"  base rate up  : {p.base_rate_up * 100:.1f}%")
    print(f"  edge over baseline: {p.edge_over_baseline_pp:+.1f}pp")
    print()
    print(
        f"Contributing signals (active {p.active_signal_count} / "
        f"total {p.total_signal_count}):"
    )
    for s in p.contributing_signals:
        _print_signal_line(s)
    print()
    if p.dca_sizing is not None:
        s = p.dca_sizing
        r, t, v, sscore = s.w_components
        print(
            f"Sizing tier: {s.tier.upper()} (W={s.w_value:.2f}, "
            f"suggested {s.suggested_entry_pct:.1f}% if user enters)"
        )
        print(
            f"  W components: R={r:.2f} T={t:.2f} V={v:.2f} S={sscore:.2f}"
        )
        print(f"  {s.disclaimer}")
        print()
    print("Next triggers:")
    for t in p.next_triggers:
        print(f"  - {t}")
    print()
    # X6: composite-level statistical-significance disclaimer when every
    # active signal fails the AUC z-test at α=0.05 (P8 Statistician).
    _maybe_print_significance_disclaimer(p)
    # W1: KR megacap universe-specific honesty footer (P10 Contrarian).
    _maybe_print_universe_note(p)
    cal_start, cal_end = p.calibration_period
    print(p.disclaimer)
    print(
        f"Calibration period: {cal_start.isoformat()} -> {cal_end.isoformat()}"
    )
    print(f"evidence_hash: sha256:{p.evidence_hash[:16]}...")
    print(f"issued_at:     {p.issued_at.isoformat()}")
    print(f"git_commit:    {p.git_commit[:12]}")


def _print_signal_line(s: SignalContribution) -> None:
    if s.direction == "skip":
        reason = s.skip_reason or "skipped"
        short = reason if len(reason) <= 50 else reason[:47] + "..."
        print(f"  {s.name:<22} . skip   ({short})")
        return
    # X5: explicit "no data" rendering for n=0 thesis (vs the silent "+0.00").
    if s.n_samples == 0:
        print(f"  {s.name:<22} no data (n=0, weight=0)")
        return
    arrow = {"up": "^", "down": "v", "neutral": "-"}[s.direction]
    val = f"{s.value:+.2f}" if s.value is not None else "n/a"
    # X3: AUC z-score / p-value annotation. "n.s." when |z| < 1.96.
    sig = format_significance(s.calibration_auc, s.n_samples)
    line = (
        f"  {s.name:<22} {arrow} {val:>7}  "
        f"(AUC {s.calibration_auc:.3f}, n={s.n_samples}, {sig}"
    )
    if s.confidence_v2 is not None:
        line += f", conf_v2={s.confidence_v2.composite_confidence:.3f}"
    line += ")"
    print(line)
    if s.confidence_v2 is not None:
        c = s.confidence_v2
        print(
            f"    conf_v2 breakdown: sample={c.sample_quality:.2f} "
            f"eff={c.effective_size_factor:.2f} stab={c.score_stability:.2f} "
            f"cons={c.return_consistency:.2f} recency={c.recency_quality:.2f}"
        )


def _maybe_print_significance_disclaimer(p: Prediction) -> None:
    active_aucs = tuple(
        (s.calibration_auc, s.n_samples)
        for s in p.contributing_signals
        if s.direction != "skip" and s.n_samples > 0
    )
    if not active_aucs:
        # No active calibrated signals: the prediction is the prior. Make it loud.
        print(
            "*** Statistical note: no active signal carried weight in this "
            "prediction; the output is essentially the base rate prior."
        )
        print()
        return
    if all_active_signals_are_noise(active_aucs):
        print(
            "*** Statistical note: every active signal's AUC is "
            "statistically indistinguishable from random (p > 0.05). "
            "The +/- edge over baseline has no significance basis at "
            "alpha=0.05 — treat as exploratory, not as a forecasting edge."
        )
        print()


def _maybe_print_universe_note(p: Prediction) -> None:
    note = kr_megacap_honesty_note(p.market)
    if note is not None:
        print(f"*** {note}")
        print()


__all__ = ["print_prediction", "print_verdict"]
