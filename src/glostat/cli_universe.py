from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from glostat.cli_mock_universe import (
    mock_sector_stats_for,
    synthetic_fundamentals_for,
    synthetic_screen_fixture,
    synthetic_sector_for,
)
from glostat.cli_mocks import MockSecEdgarClient, MockYFinanceClient
from glostat.core.types import MarketMeta, Verdict
from glostat.data.data_router import DataRouter
from glostat.data.entity_map import EntityMap, record_for_us_ticker
from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.sector_stats import (
    SectorStatsBundle,
    compute_universe_stats,
    save_sector_stats,
    summarize,
)
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.universe import Universe, list_universes, load_universe
from glostat.data.yfinance_client import YFinanceClient
from glostat.risk.compliance_gate import disclaimer_for
from glostat.screen import (
    DEFAULT_TOP_N,
    ScreenResult,
    render_screen_table,
    screen_to_json,
    screen_universe,
    select_experts_for_screen,
)
from glostat.verdict_builder import build_verdict

# CLI handlers for `glostat universe build` and `glostat screen` (PR #4).
# Kept in a separate module so cli.py stays under the 400-line house rule.

_DEFAULT_SNAPSHOT_ROOT: Final[Path] = Path("cache") / "snapshots"
_DEFAULT_ENTITY_MAP: Final[Path] = Path("cache") / "entity_map.parquet"
_DEFAULT_SECTOR_STATS: Final[Path] = Path("cache") / "sector_stats.parquet"
_DEFAULT_UNIVERSE: Final[str] = "US_LARGE_SAMPLE"


# ── universe build ─────────────────────────────────────────────────────────


def cmd_universe(args: argparse.Namespace) -> int:
    if args.universe_action == "list":
        for name in list_universes():
            print(name)
        return 0
    if args.universe_action == "build":
        return _cmd_universe_build(args)
    print(f"unknown universe action: {args.universe_action}", file=sys.stderr)
    return 2


def _cmd_universe_build(args: argparse.Namespace) -> int:
    universe = load_universe(args.name)
    print(f"[glostat] building universe {universe.name} ({universe.size} tickers)")
    bundle, sector_table = asyncio.run(_build_universe_async(universe, mock=args.mock))
    save_sector_stats(bundle, cache_path=Path(args.cache or _DEFAULT_SECTOR_STATS))
    _persist_entity_map(universe, sector_table, args)
    print(f"  tickers indexed: {len(sector_table)}")
    sector_counts: dict[str, int] = {}
    for sector in sector_table.values():
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
    print(f"  sectors discovered: {len(sector_counts)}")
    for s in sorted(sector_counts):
        print(f"    {s:<22} {sector_counts[s]:>3}")
    print()
    print("  sector medians (PER / ROE):")
    print(summarize(bundle))
    return 0


async def _build_universe_async(
    universe: Universe, *, mock: bool
) -> tuple[SectorStatsBundle, Mapping[str, str]]:
    sector_table: dict[str, str] = {}
    if mock:
        for ticker in universe.tickers:
            sector_table[ticker] = synthetic_sector_for(ticker)

        async def resolve_mock(
            ticker: str,
        ) -> tuple[str, tuple[float | None, float | None, float | None]]:
            f = synthetic_fundamentals_for(ticker)
            return (
                synthetic_sector_for(ticker),
                (f["pe_ratio"], f["roe"], f["market_cap"]),
            )

        bundle = await compute_universe_stats(universe, resolve_ticker=resolve_mock)
        return bundle, sector_table

    # Live path — wire real clients. NOTE: live runs are gated by the SEC User-Agent
    # check; without a valid env var the SEC client raises ConfigError immediately.
    broker = SnapshotBroker(root=_DEFAULT_SNAPSHOT_ROOT)
    yf = YFinanceClient(snapshot_broker=broker)
    sec = SecEdgarClient(
        user_agent=os.environ.get("GLOSTAT_SEC_USER_AGENT"),
        snapshot_broker=broker,
    )
    try:
        for ticker in universe.tickers:
            sector_table[ticker] = "UNKNOWN"  # WHY: live SIC fetch lands Sprint 1 PR #5

        async def resolve_live(
            ticker: str,
        ) -> tuple[str, tuple[float | None, float | None, float | None]]:
            try:
                f = await yf.get_fundamentals(ticker)
            except Exception:
                return ("UNKNOWN", (None, None, None))
            return ("UNKNOWN", (f.pe_ratio, f.roe, f.market_cap))

        bundle = await compute_universe_stats(universe, resolve_ticker=resolve_live)
    finally:
        await sec.aclose()
        broker.close()
    return bundle, sector_table


def _persist_entity_map(
    universe: Universe,
    sector_table: Mapping[str, str],
    args: argparse.Namespace,
) -> None:
    cache = Path(args.entity_map_cache or _DEFAULT_ENTITY_MAP)
    em = EntityMap.load(cache)
    for ticker in universe.tickers:
        sector = sector_table.get(ticker, "UNKNOWN")
        market_cap = (
            float(synthetic_fundamentals_for(ticker)["market_cap"]) if args.mock else 0.0
        )
        rec = record_for_us_ticker(
            ticker=ticker,
            name=ticker,
            sector=sector,
            market_cap_usd=market_cap,
            market="XNAS",
        )
        em.upsert(rec)
    em.flush()


# ── screen ─────────────────────────────────────────────────────────────────


def cmd_screen(args: argparse.Namespace) -> int:
    universe = load_universe(args.universe)
    market_meta = _load_market_meta_from_yaml("XNAS", args)
    result = asyncio.run(_screen_async(universe, market_meta, args))
    if args.json:
        print(json.dumps(screen_to_json(result), sort_keys=True, separators=(",", ":")))
    else:
        disclaimer = disclaimer_for(args.jurisdiction).render(
            ticker="*", action="*", issued_at=result.fetched_at.isoformat()
        )
        print(render_screen_table(result, disclaimer=disclaimer, universe_label=args.universe))
    return 0


async def _screen_async(
    universe: Universe, market_meta: MarketMeta, args: argparse.Namespace
) -> ScreenResult:
    bundle = mock_sector_stats_for(universe) if args.mock else _empty_bundle(universe)
    broker = SnapshotBroker(root=_DEFAULT_SNAPSHOT_ROOT)
    try:
        async def builder(ticker: str) -> tuple[Verdict, str]:
            sector = synthetic_sector_for(ticker) if args.mock else "UNKNOWN"
            fixture = synthetic_screen_fixture(ticker) if args.mock else None
            verdict = await _verdict_for_ticker(
                ticker=ticker,
                fixture=fixture,
                sector=sector,
                bundle=bundle,
                broker=broker,
                market_meta=market_meta,
                expert_choice=args.expert,
                horizon=args.horizon,
                mock=args.mock,
            )
            return verdict, sector

        async def sector_of(ticker: str) -> str:
            return synthetic_sector_for(ticker) if args.mock else "UNKNOWN"

        result = await screen_universe(
            universe,
            market_meta=market_meta,
            build_verdict=builder,
            sector_of=sector_of,
            top_n=args.top,
            sort_by=args.sort,
            only_cost_passed=not args.include_cost_failed,
        )
    finally:
        broker.close()
    return result


async def _verdict_for_ticker(
    *,
    ticker: str,
    fixture: dict[str, Any] | None,
    sector: str,
    bundle: SectorStatsBundle,
    broker: SnapshotBroker,
    market_meta: MarketMeta,
    expert_choice: str,
    horizon: int,
    mock: bool,
) -> Verdict:
    if mock and fixture is not None:
        yf = MockYFinanceClient(broker=broker, fixture=fixture)
        sec = MockSecEdgarClient(broker=broker, fixture=fixture)
    else:
        yf = YFinanceClient(snapshot_broker=broker)  # type: ignore[assignment]
        sec = SecEdgarClient(  # type: ignore[assignment]
            user_agent=os.environ.get("GLOSTAT_SEC_USER_AGENT"),
            snapshot_broker=broker,
        )
    router = DataRouter()
    router.register_client("yfinance", yf)
    router.register_client("sec_edgar", sec)

    async def resolver(_t: str) -> str:
        return sector

    experts = select_experts_for_screen(
        expert_choice,  # type: ignore[arg-type]
        router=router,
        sector_stats=bundle,
        sector_resolver=resolver,
    )
    ts = datetime.now(tz=UTC)
    signals = [await e.compute(ticker, ts) for e in experts]
    fixture_price = fixture.get("current_price") if fixture else None
    return build_verdict(
        ticker=ticker,
        signals=signals,
        market_meta=market_meta,
        ts=ts,
        prompt_versions={},
        current_price=fixture_price,
        horizon_days=horizon,
    )


def _empty_bundle(universe: Universe) -> SectorStatsBundle:
    from glostat.data.sector_stats import empty_bundle  # noqa: PLC0415 — local import

    return empty_bundle(universe.name)


def _load_market_meta_from_yaml(
    mic: str, _args: argparse.Namespace
) -> MarketMeta:
    # WHY: import the existing parser from cli to avoid duplicating yaml plumbing.
    from glostat.cli import _load_market_meta  # noqa: PLC0415 — local import

    return _load_market_meta(mic)


# ── argparse wiring ────────────────────────────────────────────────────────


def add_universe_subparser(sub: Any) -> None:
    p = sub.add_parser("universe", help="Universe management (list / build).")
    sub_u = p.add_subparsers(dest="universe_action")
    sub_u.add_parser("list", help="List configured universes.")
    build = sub_u.add_parser("build", help="Build universe → sector_stats cache.")
    build.add_argument("--name", default=_DEFAULT_UNIVERSE,
                       help=f"Universe name (default {_DEFAULT_UNIVERSE}).")
    build.add_argument("--mock", action="store_true",
                       help="Use synthetic fundamentals; no network calls.")
    build.add_argument("--cache", default=None,
                       help="Override sector_stats cache path.")
    build.add_argument("--entity-map-cache", default=None,
                       help="Override entity_map parquet path.")


def add_screen_subparser(sub: Any) -> None:
    p = sub.add_parser("screen", help="Screen a universe and rank top candidates.")
    p.add_argument("universe", help="Universe name (e.g., US_LARGE_SAMPLE).")
    p.add_argument("--top", type=int, default=DEFAULT_TOP_N,
                   help=f"Top N to return (default {DEFAULT_TOP_N}).")
    p.add_argument("--mock", action="store_true",
                   help="Use synthetic per-ticker data.")
    p.add_argument("--expert", default="all",
                   choices=["fundamental", "time", "fund_flow", "all"])
    p.add_argument("--sort", default="edge_x_disagreement",
                   choices=["edge_bps", "confidence", "edge_x_disagreement"])
    p.add_argument("--horizon", type=int, default=30, help="Horizon days [1, 30].")
    p.add_argument("--include-cost-failed", action="store_true",
                   help="Include verdicts that fail INV-GS-001 cost gate.")
    p.add_argument("--jurisdiction", default="US",
                   choices=["KR", "US", "EU", "JP", "TW", "HK", "DEFAULT"])
    p.add_argument("--json", action="store_true",
                   help="Emit machine-readable JSON instead of table.")


__all__ = [
    "add_screen_subparser",
    "add_universe_subparser",
    "cmd_screen",
    "cmd_universe",
]
