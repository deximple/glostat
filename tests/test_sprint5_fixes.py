from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glostat.cli_mocks import MockYFinanceClient
from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal, MarketMeta, SessionWindow
from glostat.data.data_router import DataRouter
from glostat.data.sector_stats import SectorStats, SectorStatsBundle, empty_bundle
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey
from glostat.data.yfinance_types import HoldersSnapshot
from glostat.experts import EFundamentalExpert, EFundFlowExpert, ETimeExpert
from glostat.experts.e_fund_flow import _classify_pattern_v2, _holder_deltas
from glostat.experts.ichimoku import WINDOW_TRADING_DAYS
from glostat.verdict_builder import (
    _COST_GATE_RATIO,
    _NET_SCORE_TO_BPS,
    build_verdict,
)
from tests.fixtures import load_fixture

_NOW: datetime = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


# ── Fix 1: E_FUND_FLOW holders-based ───────────────────────────────────────


def test_fix1_holders_based_no_skip_when_prior_present(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    # Seed prior snapshot via broker so the live path detects deltas.
    prior_payload = {
        "ticker": "AAPL",
        "kind": "institutional",
        "fetched_at": (_NOW - timedelta(days=14)).isoformat(),
        "holders": [
            {"name": "Vanguard Group Inc", "pct_held": 0.082, "shares": 1_200_000_000},
            {"name": "BlackRock Inc.", "pct_held": 0.070, "shares": 1_000_000_000},
            {"name": "Berkshire Hathaway Inc", "pct_held": 0.056, "shares": 850_000_000},
            {"name": "State Street Corporation", "pct_held": 0.039, "shares": 580_000_000},
            {"name": "FMR LLC", "pct_held": 0.020, "shares": 300_000_000},
        ],
    }
    key = SnapshotKey(
        uaid="XNAS.AAPL", edge_type="holders.institutional",
        ts_utc=_NOW - timedelta(days=14), tool="yfinance.holders.test",
        params_canon='{"ticker":"AAPL","kind":"institutional"}',
    )
    broker.save_snapshot(key, prior_payload)
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    router = DataRouter()
    router.register_client("yfinance", yf)
    expert = EFundFlowExpert(router=router)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    assert sig.expert_name == "E_FUND_FLOW"
    md = dict(sig.metadata)
    # Five holders increased → NET_BUY pattern → +1.5 → LONG.
    assert md["pattern"] == "NET_BUY"
    assert sig.net_score == pytest.approx(1.5)
    broker.close()


def test_fix1_skip_when_no_prior_snapshot(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    router = DataRouter()
    router.register_client("yfinance", yf)
    expert = EFundFlowExpert(router=router)
    with pytest.raises(ExpertSkipError, match="INSUFFICIENT prior snapshot"):
        asyncio.run(expert.compute("AAPL", _NOW))
    broker.close()


def test_fix1_holder_deltas_classifies_mixed_under_threshold() -> None:
    # Only 4 holders increasing → not enough to trigger NET_BUY (≥5 required).
    current = HoldersSnapshot(
        ticker="X", kind="institutional", holders=(),
        fetched_at=_NOW,
        rows=tuple(
            (n, 0.0, s, "") for (n, s) in [
                ("A", 110), ("B", 120), ("C", 130), ("D", 105), ("E", 95),
            ]
        ),
    )
    prior = HoldersSnapshot(
        ticker="X", kind="institutional", holders=(),
        fetched_at=_NOW - timedelta(days=14),
        rows=tuple(
            (n, 0.0, s, "") for (n, s) in [
                ("A", 100), ("B", 100), ("C", 100), ("D", 100), ("E", 100),
            ]
        ),
    )
    _, agg, inc, dec = _holder_deltas(current, prior)
    assert inc == 4
    assert dec == 1
    assert agg > 0
    pattern = _classify_pattern_v2(prior, inc, dec, agg)
    assert pattern == "MIXED"


# ── Fix 2: E_TIME relaxed window ───────────────────────────────────────────


def test_fix2_window_constant_is_seven() -> None:
    assert WINDOW_TRADING_DAYS == 7


def test_fix2_e_time_returns_neutral_when_no_anchors_no_earnings(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    fixture = dict(fixture)
    # Flatten OHLCV to defeat anchor detection (all closes equal).
    fixture["ohlcv"] = [
        {**b, "low": 100.0, "close": 100.0, "high": 100.0, "open": 100.0}
        for b in fixture["ohlcv"]
    ]
    fixture["earnings_calendar"] = {"ticker": "AAPL", "upcoming": []}
    broker = SnapshotBroker(root=tmp_path / "snap")
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    router = DataRouter()
    router.register_client("yfinance", yf)
    expert = ETimeExpert(router=router)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    # Sprint 5: no-signal is recorded as neutral instead of skipping.
    assert sig.direction == "NEUTRAL"
    assert sig.net_score == 0.0
    broker.close()


# ── Fix 3: sector_stats live wiring ────────────────────────────────────────


def test_fix3_sector_stats_injected_into_expert(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    router = DataRouter()
    router.register_client("yfinance", yf)
    # Inject a tight sector stats bundle (median PER 22, stddev 4) so AAPL's
    # PE 28.0 reads as +1.5 z (signal). With the empty/fallback bundle the
    # stddev is 8 which would yield z=0.75 — observable difference.
    bundle = SectorStatsBundle(
        fetched_at=_NOW, universe="test",
        by_sector={
            "Technology": SectorStats(
                sector="Technology", sample_size=12,
                per_median=22.0, per_stddev=4.0,
                roe_median=0.30, roe_stddev=0.15,
                is_fallback=False,
            ),
            "UNKNOWN": SectorStats(
                sector="UNKNOWN", sample_size=0,
                per_median=22.0, per_stddev=8.0,
                roe_median=0.18, roe_stddev=0.12,
                is_fallback=True,
            ),
        },
    )

    async def resolver(_t: str) -> str:
        return "Technology"

    expert = EFundamentalExpert(
        router=router, sector_stats=bundle, sector_resolver=resolver,
    )
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    md = dict(sig.metadata)
    assert md["sector"] == "Technology"
    assert md["sector_fallback"] == "False"
    assert int(md["sector_sample_size"]) == 12
    broker.close()


def test_fix3_fallback_used_when_sector_unknown(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    router = DataRouter()
    router.register_client("yfinance", yf)
    bundle = empty_bundle("test")
    expert = EFundamentalExpert(router=router, sector_stats=bundle)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    md = dict(sig.metadata)
    assert md["sector"] == "UNKNOWN"
    assert md["sector_fallback"] == "True"
    broker.close()


# ── Fix 4: cost_gate retune ────────────────────────────────────────────────


def _xnas() -> MarketMeta:
    return MarketMeta(
        mic="XNAS", name="NASDAQ", country="US", currency="USD",
        tz="America/New_York",
        sessions=(SessionWindow("regular", "09:30", "16:00", "14:30", "21:00"),),
        settlement_days=1, fee_bps=0.6, tax_bps_buy=0.0, tax_bps_sell=0.24,
        tick_size="1c", holidays_calendar="us_2026.yaml",
        bigdata_mcp_coverage="HIGH", foreign_access="open",
    )


def test_fix4_net_score_to_bps_halved() -> None:
    assert _NET_SCORE_TO_BPS == 50.0
    # Cost ratio kept at 1.5 (clean physical interpretation lives in the bps
    # constant). _COST_GATE_RATIO acts as the safety multiplier on top.
    assert _COST_GATE_RATIO == 1.5


def test_fix4_marginal_signal_now_demoted() -> None:
    # PR #3 baseline: net_score=0.05 with NET_SCORE_TO_BPS=100 yields edge=5bps,
    # which beats 1.5 * 1.44 = 2.16. The retune (50 bps/unit) makes the same
    # net_score yield only 2.5bps — still passes — but a smaller score does not.
    weak = ExpertSignal(
        expert_name="E_FUNDAMENTAL", ticker="AAPL",
        direction="LONG", net_score=0.02, confidence=0.10,
        archetype="continuation", basis="weak",
        sources=("yfinance.info#xx",),
        expires_at=_NOW + timedelta(days=30),
    )
    v = build_verdict(
        ticker="AAPL", signals=[weak], market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    assert v.cost_passed is False
    assert v.action == "HOLD"


def test_fix4_strong_signal_still_passes() -> None:
    strong = ExpertSignal(
        expert_name="E_FUNDAMENTAL", ticker="AAPL",
        direction="LONG", net_score=2.0, confidence=0.85,
        archetype="continuation", basis="strong",
        sources=("yfinance.info#xx",),
        expires_at=_NOW + timedelta(days=30),
    )
    v = build_verdict(
        ticker="AAPL", signals=[strong], market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    assert v.cost_passed is True
    assert v.edge_bps == pytest.approx(2.0 * 50.0)
