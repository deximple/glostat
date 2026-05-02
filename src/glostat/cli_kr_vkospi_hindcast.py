from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.universe import load_universe
from glostat.data.vkospi_client import VkospiClient
from glostat.data.vkospi_csv_provider import attach_csv_provider
from glostat.replay.phase_kr_vkospi_mood_hindcast import (
    PhaseKrVkospiConfig,
    PhaseKrVkospiResult,
    VkospiMoodReport,
    persist_phase_kr_vkospi_reports,
    run_phase_kr_vkospi_mood_hindcast,
)

# v1.10.8 — `glostat kr-vkospi-hindcast` subcommand. Lifts E_VKOSPI_MOOD_KR
# from bootstrap (n=0, weight=0) to a measured calibration entry.

_DEFAULT_UNIVERSE: Final[str] = "KR_KOSPI200_TOP30"
_DEFAULT_VKOSPI_CSV: Final[Path] = Path("cache") / "vkospi_history.csv"
_DEFAULT_OUTPUT_DIR: Final[Path] = (
    Path("cache") / "hindcast" / "phase_kr_vkospi_mood"
)
_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"


def add_kr_vkospi_hindcast_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "kr-vkospi-hindcast",
        help=(
            "Run E_VKOSPI_MOOD_KR hindcast (Lee/Son/Lee 2024) against "
            "KOSPI 200 basket. Requires VKOSPI history CSV (see "
            "docs/VKOSPI_SETUP.md)."
        ),
    )
    p.add_argument(
        "--universe", default=_DEFAULT_UNIVERSE,
        help=f"KR universe name (default {_DEFAULT_UNIVERSE}).",
    )
    p.add_argument(
        "--start", default="2024-01-02",
        help="ISO date YYYY-MM-DD (default 2024-01-02).",
    )
    p.add_argument(
        "--end", default="2026-03-29",
        help="ISO date YYYY-MM-DD (default 2026-03-29).",
    )
    p.add_argument(
        "--stride", type=int, default=1,
        help="Day-stride. Paper triggers on every event day. Default 1.",
    )
    p.add_argument(
        "--horizon", type=int, default=20,
        help="Forward-return horizon in days (paper headline). Default 20.",
    )
    p.add_argument(
        "--split", type=float, default=0.7,
        help="IS/OOS split ratio in [0.5, 0.9]. Default 0.7.",
    )
    p.add_argument(
        "--vkospi-csv", type=Path, default=None,
        help=(
            f"Path to VKOSPI history CSV. Default {_DEFAULT_VKOSPI_CSV}. "
            "See docs/VKOSPI_SETUP.md for export instructions."
        ),
    )
    p.add_argument(
        "--output-dir", type=Path, default=None,
        help=f"Output dir for reports. Default {_DEFAULT_OUTPUT_DIR}.",
    )
    p.add_argument(
        "--snapshot-root", type=Path, default=None,
        help=f"Snapshot broker root. Default {_DEFAULT_SNAPSHOT_ROOT}.",
    )


def cmd_kr_vkospi_hindcast(args: argparse.Namespace) -> int:
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
    kr_markets = {"XKRX", "XKOS"}
    if not (set(universe.markets) & kr_markets):
        print(
            f"universe {args.universe!r} is not a KR universe "
            f"(markets={universe.markets})",
            file=sys.stderr,
        )
        return 2

    csv_path = Path(args.vkospi_csv or _DEFAULT_VKOSPI_CSV)
    if not csv_path.exists():
        print(
            f"VKOSPI history CSV not found: {csv_path}\n"
            f"See docs/VKOSPI_SETUP.md for KRX export instructions.",
            file=sys.stderr,
        )
        return 3

    config = PhaseKrVkospiConfig(
        universe_tickers=tuple(universe.tickers),
        start=start,
        end=end,
        sample_stride_days=max(1, args.stride),
        split_ratio=max(0.5, min(0.9, args.split)),
        horizon_days=max(1, args.horizon),
    )

    snap_root = Path(args.snapshot_root or _DEFAULT_SNAPSHOT_ROOT)
    broker = SnapshotBroker(root=snap_root)
    vkospi_client = VkospiClient(snapshot_broker=broker)
    attach_csv_provider(vkospi_client, csv_path)
    try:
        result = asyncio.run(run_phase_kr_vkospi_mood_hindcast(
            config=config, vkospi_client=vkospi_client,
            snapshot_broker=broker,
        ))
    finally:
        broker.close()

    output_dir = Path(args.output_dir or _DEFAULT_OUTPUT_DIR)
    paths = persist_phase_kr_vkospi_reports(
        result=result, output_dir=output_dir,
    )

    print(_render_summary(result=result, paths=paths))
    return 0


def _render_summary(
    *, result: PhaseKrVkospiResult, paths: dict,
) -> str:
    r = result.vkospi_mood_kr
    decision = _calibration_decision(r)
    lines: list[str] = [
        "=== glostat kr-vkospi-hindcast complete ===",
        "",
        (
            f"  {r.thesis:<22} n={r.n_traded:>4} "
            f"AUC={r.overall_auc:.3f} Sharpe={r.overall_sharpe:+.3f} "
            f"OOS_deg={r.oos_degradation:.1%} [{decision}]"
        ),
        "",
        "Artifacts:",
    ]
    for k, v in paths.items():
        lines.append(f"  {k}: {v}")
    return "\n".join(lines)


def _calibration_decision(report: VkospiMoodReport) -> str:
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
    "add_kr_vkospi_hindcast_subparser",
    "cmd_kr_vkospi_hindcast",
]
