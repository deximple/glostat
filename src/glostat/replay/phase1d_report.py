from __future__ import annotations

from pathlib import Path
from typing import Final

import structlog

from glostat.replay.sprint4_gate import evaluate_sprint4_gate

# Phase 1D rendering layer — keeps phase1d_hindcast.py under the 400-line cap.

log: Final = structlog.get_logger(__name__)

_DEFAULT_OUTPUT_DIR: Final[Path] = Path("cache") / "hindcast" / "phase1d"


def render_report_md(report) -> str:
    lines: list[str] = []
    lines.append(f"# Phase 1D Thesis Hindcast — {report.thesis}")
    lines.append("")
    lines.append(f"- universe: `{', '.join(report.universe)}`")
    lines.append(f"- bars evaluated: **{report.n_bars_evaluated}**")
    lines.append(f"- INSUFFICIENT skipped: {report.n_skip_insufficient}")
    lines.append(f"- NEUTRAL bars: {report.n_neutral}")
    lines.append(f"- actionable (pre-cost): {report.n_actionable}")
    lines.append(f"- cost_passed: {report.n_cost_passed} ({report.cost_passed_pct:.1%})")
    lines.append(f"- traded (final): {report.n_traded}")
    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| metric | IS | OOS | overall |")
    lines.append("|---|---:|---:|---:|")
    lines.append(
        f"| sharpe | {report.is_sharpe:.4f} | {report.oos_sharpe:.4f} | "
        f"{report.overall_sharpe:.4f} |"
    )
    lines.append(
        f"| auc | {report.is_auc:.4f} | {report.oos_auc:.4f} | "
        f"{report.overall_auc:.4f} |"
    )
    lines.append(f"| maxdd | – | – | {report.overall_maxdd:.4f} |")
    lines.append(f"| oos_degradation | – | {report.oos_degradation:.2%} | – |")
    lines.append(
        f"| hit_rate_actionable | – | – | {report.hit_rate_actionable:.2%} |"
    )
    lines.append(
        f"| avg_actionable_return | – | – | {report.avg_actionable_return:+.4%} |"
    )
    lines.append("")
    if report.pattern_breakdown:
        lines.append("## Pattern breakdown")
        lines.append("")
        lines.append("| pattern | count | hit_rate (post-cost) |")
        lines.append("|---|---:|---:|")
        for p, c in sorted(report.pattern_breakdown.items(), key=lambda kv: -kv[1]):
            hr = report.pattern_hit_rates.get(p)
            hr_s = f"{hr:.2%}" if hr is not None else "–"
            lines.append(f"| {p} | {c} | {hr_s} |")
        lines.append("")
    return "\n".join(lines)


def render_gate_summary(report) -> str:
    gate = evaluate_sprint4_gate(
        sharpe=report.overall_sharpe,
        oos_degradation=report.oos_degradation,
        auc=report.overall_auc,
        cost_passed_pct=report.cost_passed_pct,
        maxdd=report.overall_maxdd,
        n_verdicts=report.n_traded,
        profile="cautious",
    )
    out: list[str] = []
    out.append(f"## Sprint 4 Gate ({report.thesis})")
    out.append(f"- status: **{gate.pass_status}**")
    out.append(f"- reasoning: {gate.reasoning}")
    out.append("")
    out.append("| metric | actual | op | threshold | passed |")
    out.append("|---|---:|---|---:|---|")
    for c in gate.per_metric_breakdown:
        out.append(
            f"| {c.name} | {c.actual:.4f} | {c.operator} | {c.threshold:.4f} | "
            f"{'OK' if c.passed else 'FAIL'} |"
        )
    return "\n".join(out)


def persist_phase1d_report(report, *, output_dir: Path | None = None) -> Path:
    out = output_dir or _DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    body = render_report_md(report) + "\n\n" + render_gate_summary(report) + "\n"
    path = out / f"{report.thesis.lower()}_report.md"
    path.write_text(body, encoding="utf-8")
    log.info("phase1d.report_persisted", thesis=report.thesis, path=str(path))
    return path


__all__ = [
    "persist_phase1d_report",
    "render_gate_summary",
    "render_report_md",
]
