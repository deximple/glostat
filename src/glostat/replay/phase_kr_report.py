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
        "| metric | E_FUNDAMENTAL_KR | E_TIME_KR | E_FOREIGN_REVERSAL | E_PEAD_KR |",
        "|---|---:|---:|---:|---:|",
    ]
    f = result.fundamental_kr
    t = result.time_kr
    r = result.foreign_reversal
    p = result.pead_kr
    lines.extend(_render_metric_rows(f=f, t=t, r=r, p=p))
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    for thesis_name, report in (
        ("E_FUNDAMENTAL_KR", f), ("E_TIME_KR", t),
        ("E_FOREIGN_REVERSAL", r), ("E_PEAD_KR", p),
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
    *,
    f: KrThesisReport,
    t: KrThesisReport,
    r: KrThesisReport,
    p: KrThesisReport,
) -> list[str]:
    return [
        f"| universe size | {f.n_universe} | {t.n_universe} | "
        f"{r.n_universe} | {p.n_universe} |",
        f"| evaluated | {f.n_evaluated} | {t.n_evaluated} | "
        f"{r.n_evaluated} | {p.n_evaluated} |",
        f"| skipped | {f.n_skipped} | {t.n_skipped} | "
        f"{r.n_skipped} | {p.n_skipped} |",
        f"| actionable | {f.n_actionable} | {t.n_actionable} | "
        f"{r.n_actionable} | {p.n_actionable} |",
        f"| traded (n) | {f.n_traded} | {t.n_traded} | "
        f"{r.n_traded} | {p.n_traded} |",
        f"| **AUC (overall)** | {f.overall_auc:.4f} | {t.overall_auc:.4f} | "
        f"{r.overall_auc:.4f} | {p.overall_auc:.4f} |",
        f"| AUC IS | {f.is_auc:.4f} | {t.is_auc:.4f} | "
        f"{r.is_auc:.4f} | {p.is_auc:.4f} |",
        f"| AUC OOS | {f.oos_auc:.4f} | {t.oos_auc:.4f} | "
        f"{r.oos_auc:.4f} | {p.oos_auc:.4f} |",
        f"| **Sharpe (overall)** | {f.overall_sharpe:.4f} | "
        f"{t.overall_sharpe:.4f} | "
        f"{r.overall_sharpe:.4f} | {p.overall_sharpe:.4f} |",
        f"| Sharpe IS | {f.is_sharpe:.4f} | {t.is_sharpe:.4f} | "
        f"{r.is_sharpe:.4f} | {p.is_sharpe:.4f} |",
        f"| Sharpe OOS | {f.oos_sharpe:.4f} | {t.oos_sharpe:.4f} | "
        f"{r.oos_sharpe:.4f} | {p.oos_sharpe:.4f} |",
        f"| OOS degradation | {f.oos_degradation:.2%} | "
        f"{t.oos_degradation:.2%} | "
        f"{r.oos_degradation:.2%} | {p.oos_degradation:.2%} |",
    ]


__all__ = [
    "persist_phase_kr_reports",
    "render_phase_kr_comparison",
]
