from __future__ import annotations

import argparse
import asyncio
import json
import os
import warnings
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml

from glostat.cli_mocks import MockSecEdgarClient, MockYFinanceClient
from glostat.cli_predict_print import print_verdict
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

# Legacy verdict surface — preserved under `glostat verdict` subcommand.
# v1.0 deprecated the BUY/HOLD/SELL action format in favor of probability +
# evidence Predictions; this module is kept for backward compatibility only.

_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"
_MARKETS_YAML: Final[Path] = (
    Path(__file__).resolve().parents[2] / "configs" / "markets.yaml"
)
_BUDGET_YAML: Final[Path] = (
    Path(__file__).resolve().parents[2] / "configs" / "budget.yaml"
)
_FIXTURES_DIR: Final[Path] = (
    Path(__file__).resolve().parents[2] / "tests" / "fixtures"
)


def add_verdict_subparser(sub: Any) -> None:
    verdict = sub.add_parser(
        "verdict",
        help="[deprecated] Issue a Verdict (BUY/HOLD/SELL). Use `predict` for v1.0 Prediction.",
    )
    verdict.add_argument("ticker")
    verdict.add_argument("--mock", action="store_true",
                         help="Use bundled fixtures instead of network calls.")
    verdict.add_argument("--horizon", type=int, default=30,
                         help="Horizon days (1-30 swing window). Default 30.")
    verdict.add_argument("--jurisdiction", default="US",
                         choices=["KR", "US", "EU", "JP", "TW", "HK", "DEFAULT"],
                         help="Compliance disclaimer jurisdiction. Default US.")
    verdict.add_argument("--json", action="store_true",
                         help="Emit verdict as canonical JSON (machine-readable).")
    verdict.add_argument("--expert", default="all",
                         choices=["fundamental", "time", "fund_flow", "all"],
                         help="Expert(s) to run. 'all' runs every wired Expert.")


def cmd_verdict(args: argparse.Namespace) -> int:
    warnings.warn(
        "glostat verdict is deprecated; use `glostat predict` for v1.0 Prediction "
        "(probability + evidence) instead.",
        DeprecationWarning,
        stacklevel=2,
    )
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
                _verdict_mock(
                    args.ticker, ts, market_meta, broker, args.horizon, args.expert
                )
            )
        else:
            verdict = asyncio.run(
                _verdict_live(
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


async def _verdict_live(
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
        ticker=ticker, signals=signals, market_meta=market_meta, ts=ts,
        prompt_versions={}, horizon_days=horizon,
    )


async def _verdict_mock(
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
        ticker=ticker, signals=signals, market_meta=market_meta, ts=ts,
        prompt_versions={},
        current_price=fixture.get("current_price"),
        next_trigger=next_trigger,
        horizon_days=horizon,
    )


async def _gather_signals(experts: list[Any], ticker: str, ts: datetime) -> list[Any]:
    out: list[Any] = []
    for e in experts:
        try:
            out.append(await e.compute(ticker, ts))
        except ExpertSkipError:
            continue
    return out


def _select_experts(choice: str, router: DataRouter) -> list[Any]:
    if choice == "fundamental":
        return [EFundamentalExpert(router=router)]
    if choice == "time":
        return [ETimeExpert(router=router)]
    if choice == "fund_flow":
        return [EFundFlowExpert(router=router)]
    return [
        EFundamentalExpert(router=router),
        ETimeExpert(router=router),
        EFundFlowExpert(router=router),
    ]


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
            f"no mock fixture for {ticker} at {path}"
        )
    return json.loads(path.read_text("utf-8"))


__all__ = ["add_verdict_subparser", "cmd_verdict"]
