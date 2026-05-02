from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final

import structlog

from glostat.replay.phase_kr_hindcast import (
    KrThesisReport,
    PhaseKrHindcastResult,
)

# v1.2 L1 — Phase KR rendering layer (kept separate from phase_kr_hindcast.py
# so the orchestrator stays under the 400-line cap).

log: Final = structlog.get_logger(__name__)

_DEFAULT_OUTPUT_DIR: Final[Path] = Path("cache") / "hindcast" / "phase_kr"


def persist_phase_kr_reports(
    *,
    result: PhaseKrHindcastResult,
    output_dir: Path | None = None,
) -> Mapping[str, Path]:
    out = output_dir or _DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for report in (
        result.fundamental_kr, result.time_kr,
        result.foreign_reversal, result.pead_kr,
        result.fundamental_kr_cyclical, result.commodity_index_kr,
    ):
        slug = report.thesis.lower()
        path = out / f"{slug}_report.json"
        path.write_text(
            json.dumps(report.to_phase1b_payload(), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        paths[report.thesis] = path
    cmp_path = out / "phase_kr_comparison.md"
    cmp_path.write_text(render_phase_kr_comparison(result), encoding="utf-8")
    paths["comparison"] = cmp_path
    return paths


def render_phase_kr_comparison(result: PhaseKrHindcastResult) -> str:
    lines: list[str] = [
        "# Phase KR — KOSPI 200 hindcast comparison",
        "",
        "Honest measurement of KR-active theses on a real KR universe + window.",
        "",
        "| metric | E_FUNDAMENTAL_KR | E_TIME_KR | E_FOREIGN_REVERSAL | "
        "E_PEAD_KR | E_FUNDAMENTAL_KR_CYCLICAL | E_COMMODITY_INDEX_KR |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    reports = (
        result.fundamental_kr, result.time_kr,
        result.foreign_reversal, result.pead_kr,
        result.fundamental_kr_cyclical, result.commodity_index_kr,
    )
    lines.extend(_render_metric_rows(reports))
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    for thesis_name, report in (
        ("E_FUNDAMENTAL_KR", result.fundamental_kr),
        ("E_TIME_KR", result.time_kr),
        ("E_FOREIGN_REVERSAL", result.foreign_reversal),
        ("E_PEAD_KR", result.pead_kr),
        ("E_FUNDAMENTAL_KR_CYCLICAL", result.fundamental_kr_cyclical),
        ("E_COMMODITY_INDEX_KR", result.commodity_index_kr),
    ):
        lines.append(f"- {thesis_name}: {', '.join(report.notes)}")
        if report.skip_breakdown:
            top = sorted(
                report.skip_breakdown.items(), key=lambda kv: -kv[1]
            )[:5]
            top_fmt = "; ".join(f"{k}={v}" for k, v in top)
            lines.append(f"  - top skips: {top_fmt}")
    lines.append("")
    if result.skipped_tickers:
        preview = ", ".join(result.skipped_tickers[:10])
        skipped_n = len(result.skipped_tickers)
        more = "" if skipped_n <= 10 else f" (+{skipped_n - 10} more)"
        lines.append(
            f"## Tickers fully skipped (Naver fetch failed): {skipped_n}"
        )
        lines.append("")
        lines.append(f"`{preview}{more}`")
        lines.append("")
    return "\n".join(lines)


def _render_metric_rows(
    reports: tuple[KrThesisReport, ...],
) -> list[str]:
    # Build N-column rows (one column per report) for any thesis count.
    def cells_int(attr: str) -> str:
        return " | ".join(str(getattr(r, attr)) for r in reports)

    def cells_float4(attr: str) -> str:
        return " | ".join(f"{getattr(r, attr):.4f}" for r in reports)

    def cells_pct(attr: str) -> str:
        return " | ".join(f"{getattr(r, attr):.2%}" for r in reports)

    return [
        f"| universe size | {cells_int('n_universe')} |",
        f"| evaluated | {cells_int('n_evaluated')} |",
        f"| skipped | {cells_int('n_skipped')} |",
        f"| actionable | {cells_int('n_actionable')} |",
        f"| traded (n) | {cells_int('n_traded')} |",
        f"| **AUC (overall)** | {cells_float4('overall_auc')} |",
        f"| AUC IS | {cells_float4('is_auc')} |",
        f"| AUC OOS | {cells_float4('oos_auc')} |",
        f"| **Sharpe (overall)** | {cells_float4('overall_sharpe')} |",
        f"| Sharpe IS | {cells_float4('is_sharpe')} |",
        f"| Sharpe OOS | {cells_float4('oos_sharpe')} |",
        f"| OOS degradation | {cells_pct('oos_degradation')} |",
    ]


__all__ = [
    "persist_phase_kr_reports",
    "render_phase_kr_comparison",
]
