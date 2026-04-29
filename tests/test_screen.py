from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from glostat.cli_mock_universe import (
    mock_sector_stats_for,
    synthetic_screen_fixture,
    synthetic_sector_for,
)
from glostat.cli_mocks import MockSecEdgarClient, MockYFinanceClient
from glostat.core.errors import ExpertSkipError
from glostat.core.types import (
    ExpertSignal,
    MarketMeta,
    SessionWindow,
    Verdict,
)
from glostat.data.data_router import DataRouter
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.universe import Universe, load_universe
from glostat.experts import EFundamentalExpert, EFundFlowExpert, ETimeExpert
from glostat.screen import (
    SCREEN_SEMAPHORE,
    ScreenResult,
    ScreenRow,
    render_screen_table,
    screen_to_json,
    screen_universe,
)
from glostat.verdict_builder import build_verdict

# Sprint 1 PR #4 — universe screening tests.

_NOW = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _market_meta() -> MarketMeta:
    return MarketMeta(
        mic="XNAS",
        name="NASDAQ",
        country="US",
        currency="USD",
        tz="America/New_York",
        sessions=(
            SessionWindow(
                name="regular",
                open_local="09:30",
                close_local="16:00",
                open_utc="14:30",
                close_utc="21:00",
            ),
        ),
        settlement_days=1,
        fee_bps=0.6,
        tax_bps_buy=0.0,
        tax_bps_sell=0.24,
        tick_size="1c",
        holidays_calendar="us_2026.yaml",
        bigdata_mcp_coverage="HIGH",
        foreign_access="open",
    )


def _make_verdict(
    *,
    ticker: str,
    action: str,
    edge_bps: float,
    cost_passed: bool = True,
    confidence: float = 0.6,
    agreement: float = 1.0,
) -> Verdict:
    sig = ExpertSignal(
        expert_name="E_FUNDAMENTAL",
        ticker=ticker,
        direction="LONG" if action == "BUY" else "NEUTRAL",
        net_score=edge_bps / 100.0,
        confidence=confidence,
        archetype="continuation",
        basis=f"synthetic test basis for {ticker}",
        sources=(f"synthetic#{ticker}",),
        expires_at=_NOW + timedelta(days=30),
    )
    return build_verdict(
        ticker=ticker,
        signals=[sig],
        market_meta=_market_meta(),
        ts=_NOW,
        prompt_versions={},
        horizon_days=30,
    )


def _small_universe(n: int = 5) -> Universe:
    tickers = tuple(f"T{i:02d}" for i in range(n))
    return Universe(
        name="MICRO",
        description="micro test",
        markets=("XNAS", "XNYS"),
        tickers=tickers,
        size=n,
    )


# ── core screen behavior ───────────────────────────────────────────────────


def test_screen_returns_top_n() -> None:
    universe = _small_universe(5)

    async def builder(ticker: str) -> tuple[Verdict, str]:
        edge = 50.0 + int(ticker[1:]) * 30.0
        return _make_verdict(ticker=ticker, action="BUY", edge_bps=edge), "Technology"

    async def sector_of(_t: str) -> str:
        return "Technology"

    result = asyncio.run(screen_universe(
        universe,
        market_meta=_market_meta(),
        build_verdict=builder,
        sector_of=sector_of,
        top_n=3,
    ))
    assert isinstance(result, ScreenResult)
    assert len(result.rows) == 3
    # Sorted descending by edge × agreement
    assert result.rows[0].edge_bps > result.rows[1].edge_bps


def test_screen_filters_cost_passed_when_requested() -> None:
    universe = _small_universe(4)

    async def builder(ticker: str) -> tuple[Verdict, str]:
        # T00, T01 fail cost gate (low edge); T02, T03 pass.
        edge = 0.5 if ticker in {"T00", "T01"} else 200.0
        return _make_verdict(ticker=ticker, action="BUY", edge_bps=edge), "Technology"

    async def sector_of(_t: str) -> str:
        return "Technology"

    result = asyncio.run(screen_universe(
        universe,
        market_meta=_market_meta(),
        build_verdict=builder,
        sector_of=sector_of,
        only_cost_passed=True,
    ))
    assert all(r.cost_passed for r in result.rows)
    assert result.total_filtered_out == 2


def test_screen_includes_cost_failed_when_disabled() -> None:
    universe = _small_universe(2)

    async def builder(ticker: str) -> tuple[Verdict, str]:
        return _make_verdict(ticker=ticker, action="BUY", edge_bps=0.5), "Technology"

    async def sector_of(_t: str) -> str:
        return "Technology"

    result = asyncio.run(screen_universe(
        universe,
        market_meta=_market_meta(),
        build_verdict=builder,
        sector_of=sector_of,
        only_cost_passed=False,
    ))
    assert len(result.rows) == 2
    assert result.total_filtered_out == 0


def test_screen_sort_by_edge_bps() -> None:
    universe = _small_universe(3)

    async def builder(ticker: str) -> tuple[Verdict, str]:
        edges = {"T00": 100.0, "T01": 300.0, "T02": 200.0}
        return _make_verdict(ticker=ticker, action="BUY", edge_bps=edges[ticker]), "Technology"

    async def sector_of(_t: str) -> str:
        return "Technology"

    result = asyncio.run(screen_universe(
        universe,
        market_meta=_market_meta(),
        build_verdict=builder,
        sector_of=sector_of,
        sort_by="edge_bps",
        top_n=3,
    ))
    assert [r.ticker for r in result.rows] == ["T01", "T02", "T00"]


def test_screen_handles_failures_gracefully() -> None:
    universe = _small_universe(4)

    async def builder(ticker: str) -> tuple[Verdict, str]:
        if ticker == "T01":
            raise RuntimeError("simulated failure")
        return _make_verdict(ticker=ticker, action="BUY", edge_bps=200.0), "Technology"

    async def sector_of(_t: str) -> str:
        return "Technology"

    result = asyncio.run(screen_universe(
        universe,
        market_meta=_market_meta(),
        build_verdict=builder,
        sector_of=sector_of,
    ))
    assert result.total_failed == 1
    assert len(result.rows) == 3


# ── concurrency: semaphore enforces max parallel ──────────────────────────


def test_screen_parallel_execution_semaphore() -> None:
    # WHY: verifies bounded concurrency. Without the semaphore, all 50 builder
    # coroutines would run simultaneously; with semaphore=10, max in-flight ≤ 10.
    in_flight = 0
    max_concurrent = 0
    universe = Universe(
        name="LARGE_TEST",
        description="50 ticker concurrency test",
        markets=("XNAS", "XNYS"),
        tickers=tuple(f"T{i:02d}" for i in range(50)),
        size=50,
    )

    async def builder(ticker: str) -> tuple[Verdict, str]:
        nonlocal in_flight, max_concurrent
        in_flight += 1
        max_concurrent = max(max_concurrent, in_flight)
        await asyncio.sleep(0.01)
        in_flight -= 1
        return _make_verdict(ticker=ticker, action="BUY", edge_bps=200.0), "Technology"

    async def sector_of(_t: str) -> str:
        return "Technology"

    asyncio.run(screen_universe(
        universe,
        market_meta=_market_meta(),
        build_verdict=builder,
        sector_of=sector_of,
        semaphore=10,
        top_n=5,
    ))
    assert max_concurrent <= 10
    assert max_concurrent > 1  # at least some parallelism happened


def test_screen_default_semaphore_constant() -> None:
    assert SCREEN_SEMAPHORE == 10


# ── full universe mock end-to-end ──────────────────────────────────────────


def test_screen_mock_full_universe(tmp_path: Path) -> None:
    universe = load_universe("US_LARGE_SAMPLE")
    bundle = mock_sector_stats_for(universe)
    broker = SnapshotBroker(root=tmp_path / "snap")

    async def builder(ticker: str) -> tuple[Verdict, str]:
        sector = synthetic_sector_for(ticker)
        fixture = synthetic_screen_fixture(ticker)
        yf = MockYFinanceClient(broker=broker, fixture=fixture)
        sec = MockSecEdgarClient(broker=broker, fixture=fixture)
        router = DataRouter()
        router.register_client("yfinance", yf)
        router.register_client("sec_edgar", sec)

        async def resolver(_t: str) -> str:
            return sector

        experts = [
            EFundamentalExpert(router=router, sector_stats=bundle, sector_resolver=resolver),
            ETimeExpert(router=router),
            EFundFlowExpert(router=router),
        ]
        # Sprint 4 PR #3: experts may raise ExpertSkipError on missing data;
        # collect surviving signals and let verdict_builder do its ≥1 check.
        signals = []
        for e in experts:
            try:
                signals.append(await e.compute(ticker, _NOW))
            except ExpertSkipError:
                continue
        verdict = build_verdict(
            ticker=ticker,
            signals=signals,
            market_meta=_market_meta(),
            ts=_NOW,
            prompt_versions={},
            current_price=fixture["current_price"],
            horizon_days=30,
        )
        return verdict, sector

    async def sector_of(t: str) -> str:
        return synthetic_sector_for(t)

    result = asyncio.run(screen_universe(
        universe,
        market_meta=_market_meta(),
        build_verdict=builder,
        sector_of=sector_of,
        top_n=10,
        only_cost_passed=False,
    ))
    broker.close()
    assert result.total_processed == 50
    # Synthetic 13F fixture is empty so E_FUND_FLOW always skips — surviving
    # E_FUNDAMENTAL + E_TIME signals build verdicts, so total_failed must be 0.
    assert result.total_failed == 0
    assert len(result.rows) <= 10
    # Each row should have a non-empty sector and a valid action
    for row in result.rows:
        assert row.sector
        assert row.action in {"BUY", "HOLD", "SELL"}


# ── output rendering ───────────────────────────────────────────────────────


def test_screen_table_includes_disclaimer() -> None:
    universe = _small_universe(2)

    async def builder(ticker: str) -> tuple[Verdict, str]:
        return _make_verdict(ticker=ticker, action="BUY", edge_bps=200.0), "Technology"

    async def sector_of(_t: str) -> str:
        return "Technology"

    result = asyncio.run(screen_universe(
        universe,
        market_meta=_market_meta(),
        build_verdict=builder,
        sector_of=sector_of,
    ))
    rendered = render_screen_table(result, disclaimer="[DISCLAIMER] PERSONAL USE")
    assert "PERSONAL USE" in rendered
    assert "MICRO" in rendered or "T00" in rendered  # universe label or row


def test_screen_to_json_emits_machine_readable() -> None:
    universe = _small_universe(2)

    async def builder(ticker: str) -> tuple[Verdict, str]:
        return _make_verdict(ticker=ticker, action="BUY", edge_bps=200.0), "Technology"

    async def sector_of(_t: str) -> str:
        return "Technology"

    result = asyncio.run(screen_universe(
        universe,
        market_meta=_market_meta(),
        build_verdict=builder,
        sector_of=sector_of,
    ))
    payload = screen_to_json(result)
    assert payload["universe"] == "MICRO"
    assert "rows" in payload
    assert len(payload["rows"]) >= 1
    assert all("ticker" in r for r in payload["rows"])


# ── ScreenRow composite rank ───────────────────────────────────────────────


def test_screen_row_composite_rank() -> None:
    high_edge_high_agree = ScreenRow(
        ticker="X", sector="Tech", action="BUY",
        conviction_w=2.0, edge_bps=300.0, cost_passed=True,
        disagreement_weight=1.0, contributing_basis=("",),
        verdict_evidence_hash="0" * 64,
    )
    low_edge_low_agree = ScreenRow(
        ticker="Y", sector="Tech", action="BUY",
        conviction_w=2.0, edge_bps=100.0, cost_passed=True,
        disagreement_weight=0.4, contributing_basis=("",),
        verdict_evidence_hash="1" * 64,
    )
    assert high_edge_high_agree.composite_rank > low_edge_low_agree.composite_rank
