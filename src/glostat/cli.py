from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

from glostat import __version__
from glostat.cli_hindcast import (
    add_gate_status_subparser,
    add_hindcast_subparser,
    cmd_gate_status,
    cmd_hindcast,
)
from glostat.cli_mocks import MockSecEdgarClient, MockYFinanceClient
from glostat.cli_predict_print import print_verdict
from glostat.cli_universe import (
    add_screen_subparser,
    add_universe_subparser,
    cmd_screen,
    cmd_universe,
)
from glostat.core.errors import ExpertSkipError
from glostat.core.types import (
    MarketMeta,
    SessionWindow,
    Verdict,
    verdict_to_canonical_json,
)
from glostat.data.data_router import DataRouter
from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import YFinanceClient
from glostat.experts import EFundamentalExpert, EFundFlowExpert, ETimeExpert
from glostat.risk.compliance_gate import (
    ComplianceContext,
    assert_personal_use,
    disclaimer_for,
)
from glostat.verdict_builder import build_verdict

# CLI surface — Sprint 1 PR #1 minimal subset:
#   glostat predict <ticker> [--mock] [--horizon DAYS]
#   glostat replay <evidence_hash>
#   glostat audit  <date>
#   glostat status

_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"
_MARKETS_YAML: Final[Path] = Path(__file__).resolve().parents[2] / "configs" / "markets.yaml"
_BUDGET_YAML: Final[Path] = Path(__file__).resolve().parents[2] / "configs" / "budget.yaml"
_FIXTURES_DIR: Final[Path] = Path(__file__).resolve().parents[2] / "tests" / "fixtures"


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_help()
        return 2
    handler = {
        "predict":     _cmd_predict,
        "replay":      _cmd_replay,
        "audit":       _cmd_audit,
        "status":      _cmd_status,
        "universe":    cmd_universe,
        "screen":      cmd_screen,
        "hindcast":    cmd_hindcast,
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
        description="GLOSTAT — Global Cascade Intelligence Engine (personal use only)",
    )
    p.add_argument("--version", action="version", version=f"glostat {__version__}")
    sub = p.add_subparsers(dest="command")

    predict = sub.add_parser("predict", help="Issue a Verdict for a ticker.")
    predict.add_argument("ticker")
    predict.add_argument("--mock", action="store_true",
                         help="Use bundled fixtures instead of network calls.")
    predict.add_argument("--horizon", type=int, default=30,
                         help="Horizon days (1-30 swing window). Default 30.")
    predict.add_argument("--jurisdiction", default="US",
                         choices=["KR", "US", "EU", "JP", "TW", "HK", "DEFAULT"],
                         help="Compliance disclaimer jurisdiction. Default US.")
    predict.add_argument("--json", action="store_true",
                         help="Emit verdict as canonical JSON (machine-readable).")
    predict.add_argument("--expert", default="all",
                         choices=["fundamental", "time", "fund_flow", "all"],
                         help="Expert(s) to run. 'all' runs every wired Expert.")

    replay = sub.add_parser("replay", help="Re-derive verdict from snapshot store.")
    replay.add_argument("verdict_hash")

    audit = sub.add_parser("audit", help="Compute Merkle root over snapshots for a date.")
    audit.add_argument("date", help="ISO date YYYY-MM-DD")

    sub.add_parser("status", help="Print version, phase, snapshot count.")

    add_universe_subparser(sub)
    add_screen_subparser(sub)
    add_hindcast_subparser(sub)
    add_gate_status_subparser(sub)

    return p


# ── command: predict ───────────────────────────────────────────────────────


def _cmd_predict(args: argparse.Namespace) -> int:
    ts = datetime.now(tz=UTC)
    market_meta = _load_market_meta("XNAS")
    ctx = ComplianceContext(
        user_profile_hash="0" * 64,
        jurisdiction=args.jurisdiction,
    )
    assert_personal_use(ctx)

    broker = SnapshotBroker(root=_DEFAULT_SNAPSHOT_ROOT)
    try:
        if args.mock:
            verdict = asyncio.run(
                _predict_mock(
                    args.ticker, ts, market_meta, broker, args.horizon, args.expert
                )
            )
        else:
            verdict = asyncio.run(
                _predict_live(
                    args.ticker, ts, market_meta, broker, args.horizon, args.expert
                )
            )
        broker.record_verdict(
            verdict_hash=verdict.evidence_hash,
            ticker=verdict.ticker,
            issued_at=verdict.issued_at,
            leaves=tuple(s for sig in verdict.contributing_signals for s in sig.sources),
            git_commit=verdict.git_commit,
            payload=json.loads(verdict_to_canonical_json(verdict)),
        )
    finally:
        broker.close()

    disclaimer = disclaimer_for(args.jurisdiction).render(
        ticker=verdict.ticker,
        action=verdict.action,
        issued_at=verdict.issued_at.isoformat(),
    )
    if args.json:
        print(verdict_to_canonical_json(verdict))
    else:
        print_verdict(verdict, disclaimer=disclaimer)
    return 0


async def _predict_live(
    ticker: str,
    ts: datetime,
    market_meta: MarketMeta,
    broker: SnapshotBroker,
    horizon: int,
    expert_choice: str,
) -> Verdict:
    yf_client = YFinanceClient(snapshot_broker=broker)
    sec_user_agent = os.environ.get("GLOSTAT_SEC_USER_AGENT")
    sec_client = SecEdgarClient(user_agent=sec_user_agent, snapshot_broker=broker)
    router = DataRouter(budget_yaml=_BUDGET_YAML)
    router.register_client("yfinance", yf_client)
    router.register_client("sec_edgar", sec_client)
    experts = _select_experts(expert_choice, router)
    try:
        signals = await _gather_signals(experts, ticker, ts)
    finally:
        await sec_client.aclose()
    return build_verdict(
        ticker=ticker,
        signals=signals,
        market_meta=market_meta,
        ts=ts,
        prompt_versions={},
        horizon_days=horizon,
    )


async def _predict_mock(
    ticker: str,
    ts: datetime,
    market_meta: MarketMeta,
    broker: SnapshotBroker,
    horizon: int,
    expert_choice: str,
) -> Verdict:
    fixture = _load_fixture(ticker)
    yf_client = MockYFinanceClient(broker=broker, fixture=fixture)
    sec_client = MockSecEdgarClient(broker=broker, fixture=fixture)
    router = DataRouter(budget_yaml=_BUDGET_YAML)
    router.register_client("yfinance", yf_client)
    router.register_client("sec_edgar", sec_client)
    experts = _select_experts(expert_choice, router)
    signals = await _gather_signals(experts, ticker, ts)
    next_earnings = fixture.get("next_earnings_date")
    next_trigger = (
        f"Next earnings: {next_earnings}" if next_earnings else None
    )
    return build_verdict(
        ticker=ticker,
        signals=signals,
        market_meta=market_meta,
        ts=ts,
        prompt_versions={},
        current_price=fixture.get("current_price"),
        next_trigger=next_trigger,
        horizon_days=horizon,
    )


async def _gather_signals(experts: list[Any], ticker: str, ts: datetime) -> list[Any]:
    # Sprint 5 PR #1: ExpertSkipError → drop the signal honestly. The verdict
    # builder still requires ≥ 1 signal so a fully skipped run surfaces as a
    # ValueError rather than a silent fake-neutral verdict.
    out: list[Any] = []
    for e in experts:
        try:
            out.append(await e.compute(ticker, ts))
        except ExpertSkipError:
            continue
    return out


def _select_experts(choice: str, router: DataRouter) -> list[Any]:
    # WHY: 'all' runs every wired Expert; specific names map to exactly one.
    if choice == "fundamental":
        return [EFundamentalExpert(router=router)]
    if choice == "time":
        return [ETimeExpert(router=router)]
    if choice == "fund_flow":
        return [EFundFlowExpert(router=router)]
    # 'all'
    return [
        EFundamentalExpert(router=router),
        ETimeExpert(router=router),
        EFundFlowExpert(router=router),
    ]


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


def _load_market_meta(mic: str) -> MarketMeta:
    data = yaml.safe_load(_MARKETS_YAML.read_text("utf-8")) or {}
    raw = (data.get("markets", {}) or {}).get(mic)
    if raw is None:
        raise ValueError(f"market {mic} not in markets.yaml")
    sessions = tuple(
        SessionWindow(
            name=str(s["name"]),
            open_local=str(s["open_local"]),
            close_local=str(s["close_local"]),
            open_utc=str(s["open_utc"]),
            close_utc=str(s["close_utc"]),
        )
        for s in raw.get("sessions", [])
    )
    return MarketMeta(
        mic=str(raw["mic"]),
        name=str(raw["name"]),
        country=str(raw["country"]),
        currency=str(raw["currency"]),
        tz=str(raw["tz"]),
        sessions=sessions,
        settlement_days=int(raw.get("settlement_days", 1)),
        fee_bps=float(raw.get("fee_bps", 0.0)),
        tax_bps_buy=float(raw.get("tax_bps_buy", 0.0)),
        tax_bps_sell=float(raw.get("tax_bps_sell", 0.0)),
        tick_size=str(raw.get("tick_size", "1c")),
        holidays_calendar=str(raw.get("holidays_calendar", "")),
        bigdata_mcp_coverage=str(raw.get("bigdata_mcp_coverage", "NONE")),  # type: ignore[arg-type]
        foreign_access=str(raw.get("foreign_access", "open")),  # type: ignore[arg-type]
        daily_limit_pct=raw.get("daily_limit_pct"),
    )


def _load_fixture(ticker: str) -> dict[str, Any]:
    path = _FIXTURES_DIR / f"{ticker.lower()}_mock.json"
    if not path.exists():
        raise FileNotFoundError(
            f"no mock fixture for {ticker} at {path}. Sprint 1 PR #1 ships AAPL only."
        )
    return json.loads(path.read_text("utf-8"))


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


if __name__ == "__main__":
    sys.exit(main())
