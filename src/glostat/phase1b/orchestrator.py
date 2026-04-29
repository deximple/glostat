from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

import structlog

from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.yfinance_client import YFinanceClient
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.runner_fomc_drift import run_fomc_drift_hindcast
from glostat.phase1b.runner_insider_cluster import run_insider_cluster_hindcast
from glostat.phase1b.runner_pead import run_pead_hindcast
from glostat.phase1b.runner_sector_rotation import run_sector_rotation_hindcast
from glostat.phase1b.types import PhaseHindcastReport
from glostat.replay.sprint4_gate import (
    Sprint4Gate,
    evaluate_sprint4_gate,
    render_gate_table,
)

log: Final = structlog.get_logger(__name__)

_DEFAULT_OUT_DIR: Final[Path] = Path("cache") / "phase1b"


async def run_all_theses(
    *,
    start: date,
    end: date,
    sp500_universe: Sequence[str],
    russell_universe: Sequence[tuple[str, str]],
    yf_client: YFinanceClient,
    sec_client: SecEdgarClient,
    out_dir: Path = _DEFAULT_OUT_DIR,
) -> dict[str, tuple[PhaseHindcastReport, Sprint4Gate]]:
    out_dir.mkdir(parents=True, exist_ok=True)

    cache = PriceCache(client=yf_client, start=start, end=end)

    # Run sequentially to keep yfinance throttle in check; each runner
    # internally parallelises within its own concurrency budget.
    results: dict[str, tuple[PhaseHindcastReport, Sprint4Gate]] = {}

    log.info("phase1b.start_sector_rotation")
    sector = await run_sector_rotation_hindcast(cache=cache, start=start, end=end)
    results["E_SECTOR_ROTATION"] = (sector, _gate(sector))
    _persist(out_dir, sector, results["E_SECTOR_ROTATION"][1])

    log.info("phase1b.start_fomc_drift")
    fomc = await run_fomc_drift_hindcast(cache=cache, start=start, end=end)
    results["E_FOMC_DRIFT"] = (fomc, _gate(fomc))
    _persist(out_dir, fomc, results["E_FOMC_DRIFT"][1])

    log.info("phase1b.start_pead", n_universe=len(sp500_universe))
    pead = await run_pead_hindcast(
        universe=sp500_universe, yf_client=yf_client, cache=cache,
        start=start, end=end,
    )
    results["E_PEAD"] = (pead, _gate(pead))
    _persist(out_dir, pead, results["E_PEAD"][1])

    log.info("phase1b.start_insider_cluster", n_universe=len(russell_universe))
    insider = await run_insider_cluster_hindcast(
        universe_with_cik=russell_universe, sec_client=sec_client, cache=cache,
        start=start, end=end,
    )
    results["E_INSIDER_CLUSTER"] = (insider, _gate(insider))
    _persist(out_dir, insider, results["E_INSIDER_CLUSTER"][1])

    return results


def _gate(report: PhaseHindcastReport) -> Sprint4Gate:
    return evaluate_sprint4_gate(
        sharpe=report.overall_sharpe,
        oos_degradation=report.oos_degradation,
        auc=report.overall_auc,
        cost_passed_pct=report.cost_passed_pct,
        maxdd=max(report.is_maxdd, report.oos_maxdd),
        reproducibility=1.0 if report.determinism_verified else 0.0,
        n_verdicts=report.n_signals,
        compliance_clean_days=90,
        profile="cautious",
    )


def _persist(
    out_dir: Path, report: PhaseHindcastReport, gate: Sprint4Gate
) -> None:
    payload = {
        "report": _report_to_dict(report),
        "gate": gate.to_dict(),
    }
    path = out_dir / f"{report.expert.lower()}_report.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    log.info("phase1b.persisted", expert=report.expert, path=str(path))


def _report_to_dict(report: PhaseHindcastReport) -> dict:
    d = asdict(report)
    d["rows"] = [
        {**asdict(r), "day": r.day.isoformat()} for r in report.rows[:200]
    ]
    d["sample_dates"] = [dt.isoformat() for dt in report.sample_dates]
    if report.timestamp is not None:
        d["timestamp"] = report.timestamp.isoformat()
    d["oos_degradation"] = report.oos_degradation
    return d


def _phase2_recommendation(
    results: dict[str, tuple[PhaseHindcastReport, Sprint4Gate]],
) -> list[str]:
    out: list[str] = []
    out.append("## Phase 2 promotion recommendation")
    out.append("")
    out.append(
        "Promotion criteria (informal): overall Sharpe > 0.5 AND overall AUC >= 0.55 "
        "AND sample size >= 50 AND OOS degradation <= 0.50."
    )
    out.append("")
    promoted: list[str] = []
    rejected: list[tuple[str, list[str]]] = []
    for expert, (rep, _gate) in results.items():
        reasons: list[str] = []
        if rep.overall_sharpe <= 0.5:
            reasons.append(f"Sharpe {rep.overall_sharpe:.3f} <= 0.5")
        if rep.overall_auc < 0.55:
            reasons.append(f"AUC {rep.overall_auc:.3f} < 0.55")
        if rep.n_signals < 50:
            reasons.append(f"n_signals {rep.n_signals} < 50")
        if rep.oos_degradation > 0.50:
            reasons.append(f"OOS deg {rep.oos_degradation * 100:.1f}% > 50%")
        if reasons:
            rejected.append((expert, reasons))
        else:
            promoted.append(expert)
    if promoted:
        out.append("**Promote to Phase 2 study:**")
        for e in promoted:
            out.append(f"- {e}")
    else:
        out.append("**No thesis meets the promotion bar.** All 4 fail at least one criterion.")
    if rejected:
        out.append("")
        out.append("**Rejected (why):**")
        for e, reasons in rejected:
            out.append(f"- {e}: {'; '.join(reasons)}")
    out.append("")
    return out


def render_comparison_md(
    results: dict[str, tuple[PhaseHindcastReport, Sprint4Gate]],
    *,
    start: date,
    end: date,
) -> str:
    now = datetime.now(tz=UTC).isoformat(timespec="seconds")
    lines: list[str] = []
    lines.append("# Phase 1B — 4-Thesis Hindcast Comparative Report")
    lines.append("")
    lines.append(f"> Generated: {now}")
    lines.append(f"> Window: {start.isoformat()} → {end.isoformat()}")
    lines.append(
        "> Empirical hindcast of 4 free-stack equity alpha theses on US equities."
    )
    lines.append("")
    lines.append("## Comparative gate table")
    lines.append("")
    lines.append(
        "| Thesis | Sharpe | AUC | OOS deg | cost_passed | skip | n_signals | Gate |"
    )
    lines.append(
        "|--------|-------:|----:|--------:|------------:|-----:|----------:|------|"
    )
    for expert in (
        "E_SECTOR_ROTATION", "E_PEAD", "E_FOMC_DRIFT", "E_INSIDER_CLUSTER",
    ):
        if expert not in results:
            continue
        rep, gate = results[expert]
        lines.append(
            f"| {expert} "
            f"| {rep.overall_sharpe:.3f} "
            f"| {rep.overall_auc:.3f} "
            f"| {rep.oos_degradation * 100:.1f}% "
            f"| {rep.cost_passed_pct * 100:.1f}% "
            f"| {rep.expert_skip_pct * 100:.1f}% "
            f"| {rep.n_signals} "
            f"| **{gate.pass_status}** |"
        )
    lines.append("")
    lines.append("## Per-thesis detail")
    for expert, (rep, gate) in results.items():
        lines.append("")
        lines.append(f"### {expert}")
        lines.append("")
        lines.append(f"- universe_size: **{rep.universe_size}**")
        lines.append(f"- n_signals: **{rep.n_signals}** (n_trades={rep.n_trades})")
        lines.append(f"- n_skipped: **{rep.n_skipped}** (skip_pct={rep.expert_skip_pct * 100:.2f}%)")
        lines.append(
            f"- IS Sharpe: {rep.is_sharpe:.4f}  |  OOS Sharpe: {rep.oos_sharpe:.4f}  "
            f"|  Overall: {rep.overall_sharpe:.4f}"
        )
        lines.append(
            f"- IS AUC: {rep.is_auc:.4f}  |  OOS AUC: {rep.oos_auc:.4f}  "
            f"|  Overall: {rep.overall_auc:.4f}"
        )
        lines.append(
            f"- OOS degradation: {rep.oos_degradation * 100:.2f}%  "
            f"|  IS maxdd: {rep.is_maxdd * 100:.2f}%  "
            f"|  OOS maxdd: {rep.oos_maxdd * 100:.2f}%"
        )
        lines.append(f"- cost_passed_pct: {rep.cost_passed_pct * 100:.2f}%")
        lines.append("")
        lines.append("```")
        lines.append(render_gate_table(gate))
        lines.append("```")
        if rep.notes:
            lines.append("")
            lines.append("Notes:")
            for n in rep.notes:
                lines.append(f"- {n}")
    lines.append("")
    lines.extend(_phase2_recommendation(results))
    return "\n".join(lines) + "\n"


def write_comparison_md(
    results: dict[str, tuple[PhaseHindcastReport, Sprint4Gate]],
    *,
    start: date,
    end: date,
    path: Path | None = None,
) -> Path:
    out = path or (_DEFAULT_OUT_DIR / "phase1b_comparison.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_comparison_md(results, start=start, end=end))
    return out


__all__ = ["render_comparison_md", "run_all_theses", "write_comparison_md"]
