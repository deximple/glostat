from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from glostat.replay.kill_criteria import KillDecisionResult
from glostat.replay.sprint4_gate import Sprint4Gate
from glostat.replay.validation_harness import HindcastReport

# Render + persist Sprint 4 hindcast reports. Split out of cli_hindcast.py to
# keep both files under the 400-line house rule (PLAN_v0.6 §house rules).


def render_metrics_table(report: HindcastReport) -> str:
    lines: list[str] = []
    lines.append("=== GLOSTAT Hindcast Metrics ===")
    lines.append(
        f"  window           : "
        f"{report.split.in_sample_start.isoformat()}..{report.split.out_sample_end.isoformat()}"
    )
    lines.append(
        f"  IS days / OOS days: {report.split.in_sample_days} / {report.split.out_sample_days}"
    )
    lines.append(f"  trading days     : {report.days_evaluated}")
    lines.append(f"  total verdicts   : {report.n_verdicts}")
    lines.append(f"  cost_passed pct  : {report.cost_passed_pct * 100:6.2f}%")
    lines.append(f"  reproducibility  : {report.reproducibility * 100:6.2f}%")
    lines.append("")
    lines.append(f"  {'METRIC':<14} {'IS':>10} {'OOS':>10} {'OVERALL':>10}")
    lines.append("  " + "-" * 50)
    lines.append(
        f"  {'sharpe':<14} {report.is_sharpe:>10.4f} {report.oos_sharpe:>10.4f} "
        f"{report.overall_sharpe:>10.4f}"
    )
    lines.append(
        f"  {'auc':<14} {report.is_auc:>10.4f} {report.oos_auc:>10.4f} "
        f"{report.overall_auc:>10.4f}"
    )
    lines.append(
        f"  {'maxdd':<14} {report.is_max_drawdown:>10.4f} "
        f"{report.oos_max_drawdown:>10.4f} {report.overall_maxdd:>10.4f}"
    )
    lines.append(
        f"  {'oos_degradation':<14} {'-':>10} {report.degradation() * 100:>9.2f}% "
        f"{'-':>10}"
    )
    return "\n".join(lines)


def render_kill_decision(kill: KillDecisionResult) -> str:
    lines: list[str] = []
    badge = {
        "CONTINUE":   "[CONTINUE]",
        "SUSPEND_7D": "[SUSPEND_7D]",
        "SHUTDOWN":   "[SHUTDOWN]",
    }[kill.decision.value]
    lines.append(f"=== Kill Criteria Decision {badge} (profile={kill.profile}) ===")
    lines.append(f"  reason             : {kill.reason}")
    if kill.violated_metrics:
        lines.append(f"  violated_metrics   : {', '.join(kill.violated_metrics)}")
    if kill.borderline_metrics:
        lines.append(f"  borderline_metrics : {', '.join(kill.borderline_metrics)}")
    lines.append(f"  v031_pivot_eligible: {kill.eligible_for_v031_pivot}")
    lines.append("  recommendations    :")
    for r in kill.recommendations:
        lines.append(f"    - {r}")
    return "\n".join(lines)


def persist_reports(
    report: HindcastReport,
    gate: Sprint4Gate,
    kill: KillDecisionResult,
    report_dir: Path,
    *,
    profile: str,
) -> dict[str, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S")
    json_path = report_dir / f"sprint4_{today}_report.json"
    md_path = report_dir / f"sprint4_{today}_report.md"
    payload = json_payload(report, gate, kill, profile)
    json_path.write_text(payload, encoding="utf-8")
    md_path.write_text(render_md(report, gate, kill, profile), encoding="utf-8")
    return {"json": json_path, "md": md_path}


def json_payload(
    report: HindcastReport,
    gate: Sprint4Gate,
    kill: KillDecisionResult,
    profile: str,
    *,
    compact: bool = False,
) -> str:
    body: dict[str, Any] = {
        "schema_version": 1,
        "profile": profile,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "report": {
            "split": {
                "in_sample_start": report.split.in_sample_start.isoformat(),
                "in_sample_end":   report.split.in_sample_end.isoformat(),
                "out_sample_start": report.split.out_sample_start.isoformat(),
                "out_sample_end":   report.split.out_sample_end.isoformat(),
                "in_sample_days":   report.split.in_sample_days,
                "out_sample_days":  report.split.out_sample_days,
            },
            "is_sharpe":          report.is_sharpe,
            "oos_sharpe":         report.oos_sharpe,
            "is_auc":             report.is_auc,
            "oos_auc":            report.oos_auc,
            "is_max_drawdown":    report.is_max_drawdown,
            "oos_max_drawdown":   report.oos_max_drawdown,
            "overall_sharpe":     report.overall_sharpe,
            "overall_auc":        report.overall_auc,
            "overall_maxdd":      report.overall_maxdd,
            "cost_passed_pct":    report.cost_passed_pct,
            "reproducibility":    report.reproducibility,
            "determinism_verified": report.determinism_verified,
            "n_verdicts":         report.n_verdicts,
            "days_evaluated":     report.days_evaluated,
            "seed":               report.seed,
            "oos_degradation":    report.degradation(),
            "notes":              list(report.notes),
        },
        "gate": gate.to_dict(),
        "kill": kill.to_dict(),
    }
    if compact:
        return json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return json.dumps(body, indent=2, sort_keys=True, default=str)


def render_md(
    report: HindcastReport,
    gate: Sprint4Gate,
    kill: KillDecisionResult,
    profile: str,
) -> str:
    lines: list[str] = []
    lines.append(f"# Sprint 4 Hindcast Report — {profile}")
    lines.append("")
    lines.append(f"- generated: `{datetime.now(tz=UTC).isoformat()}`")
    lines.append(
        f"- window: `{report.split.in_sample_start.isoformat()}`"
        f" .. `{report.split.out_sample_end.isoformat()}`"
    )
    lines.append(
        f"- IS / OOS days: {report.split.in_sample_days} / {report.split.out_sample_days}"
    )
    lines.append(f"- verdicts: {report.n_verdicts}")
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
    lines.append(
        f"| maxdd | {report.is_max_drawdown:.4f} | {report.oos_max_drawdown:.4f} | "
        f"{report.overall_maxdd:.4f} |"
    )
    lines.append(
        f"| oos_degradation | – | {report.degradation() * 100:.2f}% | – |"
    )
    lines.append(
        f"| cost_passed_pct | – | – | {report.cost_passed_pct * 100:.2f}% |"
    )
    lines.append("")
    lines.append("## Sprint 4 Gate")
    lines.append("")
    lines.append(f"- status: **{gate.pass_status}**")
    lines.append(f"- v0.3.1 pivot eligible: {gate.v031_pivot_eligible}")
    lines.append(f"- reasoning: {gate.reasoning}")
    lines.append("")
    lines.append("| metric | actual | op | threshold | passed |")
    lines.append("|---|---:|---|---:|---|")
    for c in gate.per_metric_breakdown:
        flag = "ok" if c.passed else "FAIL"
        if c.borderline:
            flag += " (borderline)"
        lines.append(
            f"| {c.name} | {c.actual:.4f} | {c.operator} | {c.threshold:.4f} | {flag} |"
        )
    lines.append("")
    lines.append("## Kill Decision")
    lines.append("")
    lines.append(f"- decision: **{kill.decision.value}**")
    lines.append(f"- reason: {kill.reason}")
    lines.append(f"- v031 pivot eligible: {kill.eligible_for_v031_pivot}")
    if kill.violated_metrics:
        lines.append(f"- violated: {', '.join(kill.violated_metrics)}")
    if kill.borderline_metrics:
        lines.append(f"- borderline: {', '.join(kill.borderline_metrics)}")
    lines.append("")
    lines.append("### Recommendations")
    for r in kill.recommendations:
        lines.append(f"- {r}")
    lines.append("")
    lines.append("> Generated by `glostat hindcast` (Sprint 4 PR #1).")
    return "\n".join(lines)


__all__ = [
    "json_payload",
    "persist_reports",
    "render_kill_decision",
    "render_md",
    "render_metrics_table",
]
