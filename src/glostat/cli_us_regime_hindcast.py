from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path
from typing import Any, Final

from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.universe import load_universe
from glostat.replay.phase_us_regime_hindcast import (
    PhaseUsRegimeConfig,
    PhaseUsRegimeResult,
    UsRegimeReport,
    persist_phase_us_regime_reports,
    run_phase_us_regime_hindcast,
)

# v1.10 — `glostat us-regime-hindcast` subcommand. Produces real (not
# bootstrapped) calibration for E_REGIME_US so the next `glostat predict`
# can lift the n=0 weight=0 entry to measured AUC/Sharpe.

_DEFAULT_UNIVERSE: Final[str] = "US_LARGE_SAMPLE"
_DEFAULT_OUTPUT_DIR: Final[Path] = (
    Path("cache") / "hindcast" / "phase_us_regime"
)
_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"


def add_us_regime_hindcast_subparser(sub: Any) -> None:
    p = sub.add_parser(
        "us-regime-hindcast",
        help=(
            "Run E_REGIME_US hindcast (VIX term + UST curve) against US "
            "basket; produce calibration JSON."
        ),
    )
    p.add_argument(
        "--universe", default=_DEFAULT_UNIVERSE,
        help=f"US universe name (default {_DEFAULT_UNIVERSE}).",
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
        "--stride", type=int, default=7,
        help="Day-stride (smaller = more samples, slower). Default 7.",
    )
    p.add_argument(
        "--horizon", type=int, default=30,
        help="Forward-return horizon in days. Default 30.",
    )
    p.add_argument(
        "--split", type=float, default=0.7,
        help="IS/OOS split ratio in [0.5, 0.9]. Default 0.7.",
    )
    p.add_argument(
        "--output-dir", type=Path, default=None,
        help=f"Output dir for reports. Default {_DEFAULT_OUTPUT_DIR}.",
    )
    p.add_argument(
        "--snapshot-root", type=Path, default=None,
        help=f"Snapshot broker root. Default {_DEFAULT_SNAPSHOT_ROOT}.",
    )


def cmd_us_regime_hindcast(args: argparse.Namespace) -> int:
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
    us_markets = {"XNAS", "XNYS"}
    if not (set(universe.markets) & us_markets):
        print(
            f"universe {args.universe!r} is not a US universe "
            f"(markets={universe.markets})",
            file=sys.stderr,
        )
        return 2

    config = PhaseUsRegimeConfig(
        universe_tickers=tuple(universe.tickers),
        start=start,
        end=end,
        sample_stride_days=max(1, args.stride),
        split_ratio=max(0.5, min(0.9, args.split)),
        horizon_days=max(1, args.horizon),
    )

    snap_root = Path(args.snapshot_root or _DEFAULT_SNAPSHOT_ROOT)
    broker = SnapshotBroker(root=snap_root)
    try:
        result = asyncio.run(run_phase_us_regime_hindcast(
            config=config, snapshot_broker=broker,
        ))
    finally:
        broker.close()

    output_dir = Path(args.output_dir or _DEFAULT_OUTPUT_DIR)
    paths = persist_phase_us_regime_reports(
        result=result, output_dir=output_dir,
    )

    print(_render_summary(result=result, paths=paths))
    return 0


def _render_summary(
    *, result: PhaseUsRegimeResult, paths: dict,
) -> str:
    r = result.regime_us
    decision = _calibration_decision(r)
    lines: list[str] = [
        "=== glostat us-regime-hindcast complete ===",
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


def _calibration_decision(report: UsRegimeReport) -> str:
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
    "add_us_regime_hindcast_subparser",
    "cmd_us_regime_hindcast",
]
