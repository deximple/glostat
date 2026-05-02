from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Final

import yaml

from glostat import __version__
from glostat.cli_hindcast import (
    add_gate_status_subparser,
    add_hindcast_subparser,
    cmd_gate_status,
    cmd_hindcast,
)
from glostat.cli_kr_hindcast import (
    add_kr_hindcast_subparser,
    cmd_kr_hindcast,
)
from glostat.cli_predictor import (
    add_calibrate_subparser,
    add_predict_subparser,
    cmd_calibrate,
    cmd_predict,
)
from glostat.cli_scan import add_scan_subparser, cmd_scan
from glostat.cli_universe import (
    add_screen_subparser,
    add_universe_subparser,
    cmd_screen,
    cmd_universe,
)
from glostat.cli_us_regime_hindcast import (
    add_us_regime_hindcast_subparser,
    cmd_us_regime_hindcast,
)
from glostat.cli_verdict import (
    _load_fixture,
    _load_market_meta,
    add_verdict_subparser,
    cmd_verdict,
)
from glostat.data.snapshot_broker import SnapshotBroker

# v1.0 — predict subcommand outputs Prediction (probability + evidence). The
# legacy Verdict (BUY/HOLD/SELL) surface is preserved under `glostat verdict`
# with a deprecation notice.

_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"
_BUDGET_YAML: Final[Path] = (
    Path(__file__).resolve().parents[2] / "configs" / "budget.yaml"
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    handler = {
        "predict":     cmd_predict,
        "calibrate":   cmd_calibrate,
        "scan":        cmd_scan,
        "verdict":     cmd_verdict,
        "replay":      _cmd_replay,
        "audit":       _cmd_audit,
        "status":      _cmd_status,
        "universe":    cmd_universe,
        "screen":      cmd_screen,
        "hindcast":    cmd_hindcast,
        "kr-hindcast": cmd_kr_hindcast,
        "us-regime-hindcast": cmd_us_regime_hindcast,
        "gate-status": cmd_gate_status,
    }[args.command]
    try:
        return handler(args)
    except KeyboardInterrupt:
        print("[glostat] interrupted", file=sys.stderr)
        return 130


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="glostat",
        description=(
            "GLOSTAT — Prediction tool framework "
            "(personal use only, not investment advice)."
        ),
    )
    p.add_argument("--version", action="version", version=f"glostat {__version__}")
    sub = p.add_subparsers(dest="command")

    # v1.0 commands
    add_predict_subparser(sub)
    add_calibrate_subparser(sub)
    # v1.9.0 — universe scan command
    add_scan_subparser(sub)

    # Legacy v0.7 verdict surface — deprecated, kept for backward compat.
    add_verdict_subparser(sub)

    replay = sub.add_parser("replay", help="Re-derive verdict from snapshot store.")
    replay.add_argument("verdict_hash")

    audit = sub.add_parser("audit", help="Compute Merkle root over snapshots for a date.")
    audit.add_argument("date", help="ISO date YYYY-MM-DD")

    sub.add_parser("status", help="Print version, phase, snapshot count.")

    add_universe_subparser(sub)
    add_screen_subparser(sub)
    add_hindcast_subparser(sub)
    add_kr_hindcast_subparser(sub)
    add_us_regime_hindcast_subparser(sub)
    add_gate_status_subparser(sub)

    return p


# ── command: replay ────────────────────────────────────────────────────────


def _cmd_replay(args: argparse.Namespace) -> int:
    broker = SnapshotBroker(root=_DEFAULT_SNAPSHOT_ROOT)
    try:
        try:
            payload = broker.replay_verdict(args.verdict_hash)
        except KeyError:
            print(f"verdict not found: {args.verdict_hash}", file=sys.stderr)
            return 1
    finally:
        broker.close()
    print("True")
    print(json.dumps({"ticker": payload.get("ticker"),
                      "action": payload.get("action"),
                      "issued_at": payload.get("issued_at")}, indent=2))
    return 0


# ── command: audit ─────────────────────────────────────────────────────────


def _cmd_audit(args: argparse.Namespace) -> int:
    broker = SnapshotBroker(root=_DEFAULT_SNAPSHOT_ROOT)
    try:
        leaves = [
            row["leaf_hash"]
            for row in broker._db.execute(
                "SELECT leaf_hash FROM snapshots WHERE ts_utc LIKE ? ORDER BY leaf_hash",
                (f"{args.date}%",),
            ).fetchall()
        ]
        root = broker.audit_root(leaves)
    finally:
        broker.close()
    print(root)
    return 0


# ── command: status ────────────────────────────────────────────────────────


def _cmd_status(_args: argparse.Namespace) -> int:
    phase = os.environ.get("GLOSTAT_PHASE") or _phase_from_yaml(_BUDGET_YAML)
    snapshot_count = _snapshot_count(_DEFAULT_SNAPSHOT_ROOT)
    budget = _budget_cap(_BUDGET_YAML, phase)
    print(f"version  = {__version__}")
    print(f"phase    = {phase}")
    print(f"budget   = ${budget}/mo")
    print(f"snapshots= {snapshot_count}")
    return 0


def _phase_from_yaml(path: Path) -> str:
    if not path.exists():
        return "mvp"
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    return str(data.get("phase", "mvp")).strip().lower()


def _budget_cap(path: Path, phase: str) -> int:
    if not path.exists():
        return 0
    data = yaml.safe_load(path.read_text("utf-8")) or {}
    table = (data.get("budget", {}) or {})
    key = {"mvp": "mvp_phase",
           "phase_2": "phase_2_optional",
           "phase_3": "phase_3_cascade"}.get(phase, "mvp_phase")
    return int((table.get(key, {}) or {}).get("cap_usd_per_month", 0))


def _snapshot_count(root: Path) -> int:
    db = root / "index.sqlite"
    if not db.exists():
        return 0
    import sqlite3  # noqa: PLC0415 — keeps cold path off the hot import.
    conn = sqlite3.connect(db)
    try:
        return int(conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
    finally:
        conn.close()


# Compatibility shim — historic test suite imports `_load_market_meta` and
# `_load_fixture` from this module.
__all__ = [
    "_load_fixture",
    "_load_market_meta",
    "main",
]

if __name__ == "__main__":
    sys.exit(main())
