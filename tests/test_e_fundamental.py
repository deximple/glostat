from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from glostat.cli_mocks import MockSecEdgarClient, MockYFinanceClient
from glostat.core.errors import ExpertSkipError
from glostat.data.data_router import DataRouter
from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import Fundamentals, YFinanceClient
from glostat.experts import EFundamentalExpert, FundamentalScore
from glostat.experts.e_fundamental import (
    _WEIGHT_EPS_SUR,
    _WEIGHT_PER,
    _WEIGHT_ROE,
    _eps_surprise,
    _per_z_score,
    _roe_z_score,
)
from tests.fixtures import load_fixture

# ── Helpers ────────────────────────────────────────────────────────────────


_NOW = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _build_router(broker: SnapshotBroker, fixture: dict) -> DataRouter:
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    sec = MockSecEdgarClient(broker=broker, fixture=fixture)
    r = DataRouter()
    r.register_client("yfinance", yf)
    r.register_client("sec_edgar", sec)
    return r


# ── compute() returns a well-formed ExpertSignal ───────────────────────────


def test_compute_returns_expert_signal(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, fixture)
    expert = EFundamentalExpert(router=router)

    sig = asyncio.run(expert.compute("AAPL", _NOW))

    assert sig.expert_name == "E_FUNDAMENTAL"
    assert sig.ticker == "AAPL"
    assert sig.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert -3.0 <= sig.net_score <= 3.0
    assert 0.0 <= sig.confidence <= 1.0
    assert sig.archetype == "continuation"
    assert sig.basis.startswith("PER")
    assert sig.expires_at > _NOW
    assert (sig.expires_at - _NOW).days == 30
    broker.close()


# ── Score formula building blocks ──────────────────────────────────────────


def test_per_zscore_calculation() -> None:
    # Sector median 22, stddev 8 → PER 30 = +1 stddev (raw); inverted later by expert.
    assert _per_z_score(30.0) == pytest.approx(1.0)
    assert _per_z_score(14.0) == pytest.approx(-1.0)
    assert _per_z_score(None) == 0.0
    assert _per_z_score(0.0) == 0.0   # negative/zero PER = treat as no signal


def test_roe_zscore_calculation() -> None:
    # Sector median 0.18, stddev 0.12 → ROE 0.30 = +1 stddev.
    assert _roe_z_score(0.30) == pytest.approx(1.0)
    assert _roe_z_score(0.06) == pytest.approx(-1.0)
    assert _roe_z_score(None) == 0.0


def test_eps_surprise_calculation() -> None:
    # +20% surprise (1.2 actual vs 1.0 estimate) → +0.20.
    assert _eps_surprise(1.2, 1.0) == pytest.approx(0.20)
    # Negative surprise.
    assert _eps_surprise(0.8, 1.0) == pytest.approx(-0.20)
    # Missing fields → 0.
    assert _eps_surprise(None, 1.0) == 0.0
    assert _eps_surprise(1.0, None) == 0.0
    assert _eps_surprise(1.0, 0.0) == 0.0


# ── Direction thresholds ───────────────────────────────────────────────────


def test_direction_thresholds() -> None:
    # > 1.5 → LONG
    s = FundamentalScore(per_z=0.0, roe_z=0.0, eps_surprise=0.0, net_score=1.6)
    assert s.direction == "LONG"
    # < -1.5 → SHORT
    s = FundamentalScore(per_z=0.0, roe_z=0.0, eps_surprise=0.0, net_score=-1.6)
    assert s.direction == "SHORT"
    # Between → NEUTRAL
    for v in (-1.5, -0.5, 0.0, 0.5, 1.5):
        s = FundamentalScore(per_z=0.0, roe_z=0.0, eps_surprise=0.0, net_score=v)
        assert s.direction == "NEUTRAL", f"score={v}"


def test_confidence_normalized() -> None:
    s = FundamentalScore(per_z=0.0, roe_z=0.0, eps_surprise=0.0, net_score=3.0)
    assert s.confidence == 1.0
    s = FundamentalScore(per_z=0.0, roe_z=0.0, eps_surprise=0.0, net_score=0.0)
    assert s.confidence == 0.0


# ── Sources populated ──────────────────────────────────────────────────────


def test_sources_populated_from_both_data_sources(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, fixture)
    expert = EFundamentalExpert(router=router)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    # Expect ≥ 2 sources: yfinance + (sec_edgar.company_tickers OR companyfacts).
    assert len(sig.sources) >= 2
    assert any("yfinance" in s for s in sig.sources)
    assert any("sec_edgar" in s for s in sig.sources)
    broker.close()


# ── Score clipped to [-3, +3] ──────────────────────────────────────────────


def test_score_clipped_to_range(tmp_path: Path) -> None:
    extreme = {
        "fundamentals": {
            "ticker": "EXTREME",
            "pe_ratio": 1000.0,        # absurdly expensive
            "forward_pe": None,
            "eps": -10.0,              # huge negative surprise
            "forward_eps": 5.0,
            "roe": -2.0,               # awful ROE
            "market_cap": None,
            "dividend_yield": None,
            "beta": None,
            "fifty_two_week_high": None,
            "fifty_two_week_low": None,
        },
        "company_facts": {
            "cik": "0000000000",
            "entity_name": "Extreme Co",
            "facts": [],
        },
    }
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, extreme)
    expert = EFundamentalExpert(router=router)
    sig = asyncio.run(expert.compute("EXTREME", _NOW))
    assert -3.0 <= sig.net_score <= 3.0
    broker.close()


def test_score_skips_when_per_missing(tmp_path: Path) -> None:
    # Sprint 4 PR #3: missing PER + forward PE → ExpertSkipError instead of
    # silent net_score=0. Hindcast caller decides whether to drop or partial.
    blank = {
        "fundamentals": {
            "ticker": "BLANK",
            "pe_ratio": None, "forward_pe": None,
            "eps": None, "forward_eps": None,
            "roe": None, "market_cap": None,
            "dividend_yield": None, "beta": None,
            "fifty_two_week_high": None, "fifty_two_week_low": None,
        },
        "company_facts": {"cik": "0", "entity_name": "Blank", "facts": []},
    }
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, blank)
    expert = EFundamentalExpert(router=router)
    with pytest.raises(ExpertSkipError, match="E_FUNDAMENTAL"):
        asyncio.run(expert.compute("BLANK", _NOW))
    broker.close()


# ── Snapshot integration: every fetch records a snapshot ───────────────────


def test_each_fetch_records_a_snapshot(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, fixture)
    expert = EFundamentalExpert(router=router)
    asyncio.run(expert.compute("AAPL", _NOW))
    rows = list(broker.list_snapshots())
    # 3 expected: fundamentals + ticker_to_cik + company_facts
    assert len(rows) == 3
    edges = sorted(r.leaf.key.edge_type for r in rows)
    assert edges == ["company_facts", "fundamentals", "ticker_cik"]
    broker.close()


# ── Network test — opt-in real AAPL run ────────────────────────────────────


@pytest.mark.network
def test_network_real_aapl_compute() -> None:
    if not os.environ.get("GLOSTAT_SEC_USER_AGENT"):
        pytest.skip("GLOSTAT_SEC_USER_AGENT not set")
    broker = SnapshotBroker(root=Path("cache/snapshots_network"))
    yf = YFinanceClient(snapshot_broker=broker)
    sec = SecEdgarClient(snapshot_broker=broker)
    router = DataRouter()
    router.register_client("yfinance", yf)
    router.register_client("sec_edgar", sec)
    expert = EFundamentalExpert(router=router)

    async def run() -> None:
        sig = await expert.compute("AAPL", datetime.now(tz=UTC))
        assert sig.ticker == "AAPL"
        assert len(sig.sources) >= 2
        await sec.aclose()

    asyncio.run(run())
    broker.close()


# ── Score weight assertions ────────────────────────────────────────────────


def test_score_weights_sum_to_one() -> None:
    assert pytest.approx(_WEIGHT_PER + _WEIGHT_ROE + _WEIGHT_EPS_SUR) == 1.0


def test_neutral_fundamentals_round_trips_through_signal(tmp_path: Path) -> None:
    # Constructing Fundamentals directly to confirm dataclass surface stable.
    f = Fundamentals(
        ticker="X", pe_ratio=22.0, forward_pe=22.0, eps=2.0, forward_eps=2.0,
        roe=0.18, market_cap=1e9, dividend_yield=0.01, beta=1.0,
        fifty_two_week_high=100.0, fifty_two_week_low=80.0, raw=(),
    )
    # Median PER & ROE → both z = 0; no surprise → score 0.
    assert _per_z_score(f.pe_ratio) == 0.0
    assert _roe_z_score(f.roe) == 0.0
