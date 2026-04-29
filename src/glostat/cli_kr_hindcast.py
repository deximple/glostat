from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.universe import load_universe
from glostat.replay.phase_kr_hindcast import (
    KrThesisReport,
    PhaseKrHindcastConfig,
    PhaseKrHindcastResult,
    persist_phase_kr_reports,
    run_phase_kr_hindcast,
)

# v1.2 L1 — `glostat kr-hindcast` subcommand. Runs the KR-active theses against
# a configurable KR universe + window and writes per-thesis JSON reports plus a
# side-by-side comparison markdown so calibration.py can ingest the result.

_DEFAULT_UNIVERSE: Final[str] = "KR_KOSPI200_TOP30"
_DEFAULT_OUTPUT_DIR: Final[Path] = Path("cache") / "hindcast" / "phase_kr"
_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"
_DEFAULT_MAX_CONCURRENT: Final[int] = 5


def add_kr_hindcast_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "kr-hindcast",
        help="Run KR (KOSPI 200) hindcast over a date range; produce calibration JSON.",
    )
    p.add_argument("--universe", default=_DEFAULT_UNIVERSE,
                   help=f"Universe name (default {_DEFAULT_UNIVERSE}).")
    p.add_argument("--start", default="2024-01-02",
                   help="ISO date YYYY-MM-DD (default 2024-01-02).")
    p.add_argument("--end", default="2026-03-29",
                   help="ISO date YYYY-MM-DD (default 2026-03-29).")
    p.add_argument("--max-concurrent", type=int, default=_DEFAULT_MAX_CONCURRENT,
                   help=f"Parallel ticker semaphore. Default {_DEFAULT_MAX_CONCURRENT}.")
    p.add_argument("--stride", type=int, default=7,
                   help="Day-stride per ticker (smaller = more samples, slower). Default 7.")
    p.add_argument("--split", type=float, default=0.7,
                   help="IS/OOS split ratio in [0.5, 0.9]. Default 0.7.")
    p.add_argument("--output-dir", type=Path, default=None,
                   help=f"Output dir for reports. Default {_DEFAULT_OUTPUT_DIR}.")
    p.add_argument("--snapshot-root", type=Path, default=None,
                   help=f"Snapshot broker root. Default {_DEFAULT_SNAPSHOT_ROOT}.")


def cmd_kr_hindcast(args: argparse.Namespace) -> int:
    try:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    except ValueError as exc:
        print(f"invalid date: {exc}", file=sys.stderr)
        return 2
    if end <= start:
        print("--end must be after --start", file=sys.stderr)
        return 2

    universe = load_universe(args.universe)
    if "XKRX" not in universe.markets and "XKOS" not in universe.markets:
        print(
            f"universe {args.universe!r} is not a KR universe (markets={universe.markets})",
            file=sys.stderr,
        )
        return 2

    config = PhaseKrHindcastConfig(
        universe_tickers=tuple(universe.tickers),
        start=start,
        end=end,
        sample_stride_days=max(1, args.stride),
        split_ratio=max(0.5, min(0.9, args.split)),
        max_concurrent=max(1, args.max_concurrent),
    )

    snap_root = Path(args.snapshot_root or _DEFAULT_SNAPSHOT_ROOT)
    broker = SnapshotBroker(root=snap_root)
    try:
        result = asyncio.run(run_phase_kr_hindcast(
            config=config, snapshot_broker=broker,
        ))
    finally:
        broker.close()

    output_dir = Path(args.output_dir or _DEFAULT_OUTPUT_DIR)
    paths = persist_phase_kr_reports(result=result, output_dir=output_dir)

    print(_render_summary(result=result, paths=paths))
    return 0


def _render_summary(*, result: PhaseKrHindcastResult, paths: dict) -> str:
    lines: list[str] = [
        "=== glostat kr-hindcast complete ===",
        "",
    ]
    for r in (result.fundamental_kr, result.time_kr, result.foreign_reversal):
        decision = _calibration_decision(r)
        lines.append(
            f"  {r.thesis:<22} n={r.n_traded:>4} AUC={r.overall_auc:.3f} "
            f"Sharpe={r.overall_sharpe:+.3f} OOS_deg={r.oos_degradation:.1%} "
            f"[{decision}]"
        )
    lines.append("")
    lines.append("Artifacts:")
    for k, v in paths.items():
        lines.append(f"  {k}: {v}")
    if result.skipped_tickers:
        lines.append("")
        lines.append(
            f"Tickers with Naver fetch failure: {len(result.skipped_tickers)} "
            "(reported in comparison.md)"
        )
    return "\n".join(lines)


def _calibration_decision(report: KrThesisReport) -> str:
    # WHY: v1.0 framing — calibration data, not pass/fail. Surface a one-word
    # label so the operator sees what the composite predictor will do with this.
    if report.n_traded < 10:
        return "INSUFFICIENT_N"
    edge = abs(report.overall_auc - 0.5)
    if edge < 0.02:
        return "NEAR_RANDOM"
    if report.overall_sharpe > 0.5:
        return "WEAKLY_PREDICTIVE"
    if report.overall_sharpe < -0.5:
        return "ANTI_PREDICTIVE"
    return "AMBIGUOUS"


__all__ = [
    "add_kr_hindcast_subparser",
    "cmd_kr_hindcast",
]
