from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glostat.cli_mocks import MockSecEdgarClient, MockYFinanceClient
from glostat.core.errors import ExpertSkipError
from glostat.data.data_router import DataRouter
from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey
from glostat.data.yfinance_client import YFinanceClient
from glostat.data.yfinance_types import HoldersSnapshot
from glostat.experts import EFundFlowExpert, FundFlowScore
from glostat.experts.e_fund_flow import (
    _PATTERN_SCORE,
    _classify_pattern_v2,
    _holder_deltas,
    _top_holder,
)
from tests.fixtures import load_fixture

# Sprint 5 PR #1 — E_FUND_FLOW now uses yfinance institutional_holders snapshot
# delta against the prior broker snapshot. 13F issuer-CIK path retired.

_NOW = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


# ── Helpers ────────────────────────────────────────────────────────────────


def _build_router(broker: SnapshotBroker, fixture: dict) -> DataRouter:
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    sec = MockSecEdgarClient(broker=broker, fixture=fixture)
    r = DataRouter()
    r.register_client("yfinance", yf)
    r.register_client("sec_edgar", sec)
    return r


def _holders_payload(rows: list[tuple[str, float, int]]) -> dict:
    return {
        "ticker": "AAPL",
        "kind": "institutional",
        "fetched_at": (_NOW - timedelta(days=14)).isoformat(),
        "holders": [
            {"name": n, "pct_held": p, "shares": s, "date_reported": "2026-03-31"}
            for (n, p, s) in rows
        ],
    }


def _seed_prior(
    broker: SnapshotBroker,
    ticker: str,
    rows: list[tuple[str, float, int]],
    *,
    days_back: int = 14,
) -> None:
    prior_ts = _NOW - timedelta(days=days_back)
    key = SnapshotKey(
        uaid=f"XNAS.{ticker.upper()}",
        edge_type="holders.institutional",
        ts_utc=prior_ts,
        tool="yfinance.holders.test",
        params_canon='{"ticker":"' + ticker.upper() + '","kind":"institutional"}',
    )
    broker.save_snapshot(key, _holders_payload(rows))


# ── compute() returns a well-formed ExpertSignal ───────────────────────────


def test_compute_returns_expert_signal_structure(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    # Seed prior snapshot so the live path detects a delta and does not skip.
    _seed_prior(
        broker, "AAPL",
        [
            ("Vanguard Group Inc", 0.0840, 1_270_000_000),
            ("BlackRock Inc.", 0.0710, 1_073_000_000),
            ("Berkshire Hathaway Inc", 0.0580, 880_000_000),
            ("State Street Corporation", 0.0400, 600_000_000),
            ("FMR LLC", 0.0210, 320_000_000),
        ],
    )
    router = _build_router(broker, fixture)
    expert = EFundFlowExpert(router=router)

    sig = asyncio.run(expert.compute("AAPL", _NOW))

    assert sig.expert_name == "E_FUND_FLOW"
    assert sig.ticker == "AAPL"
    assert sig.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert -3.0 <= sig.net_score <= 3.0
    assert 0.0 <= sig.confidence <= 1.0
    assert sig.archetype in {"continuation", "mixed"}
    assert (sig.expires_at - _NOW).days == 30
    broker.close()


def test_pattern_net_buy_when_five_or_more_holders_increase() -> None:
    current = HoldersSnapshot(
        ticker="AAPL", kind="institutional",
        holders=(),
        fetched_at=_NOW,
        rows=tuple(
            (n, 0.0, s, "2026-03-31") for (n, s) in [
                ("Vanguard", 1_300_000_000),
                ("BlackRock", 1_100_000_000),
                ("Berkshire", 900_000_000),
                ("State Street", 620_000_000),
                ("FMR", 340_000_000),
            ]
        ),
    )
    prior = HoldersSnapshot(
        ticker="AAPL", kind="institutional",
        holders=(),
        fetched_at=_NOW - timedelta(days=14),
        rows=tuple(
            (n, 0.0, s, "2026-03-31") for (n, s) in [
                ("Vanguard", 1_270_000_000),
                ("BlackRock", 1_073_000_000),
                ("Berkshire", 880_000_000),
                ("State Street", 600_000_000),
                ("FMR", 320_000_000),
            ]
        ),
    )
    _, agg, inc, dec = _holder_deltas(current, prior)
    assert inc == 5
    assert dec == 0
    assert agg > 0
    pattern = _classify_pattern_v2(prior, inc, dec, agg)
    assert pattern == "NET_BUY"
    assert _PATTERN_SCORE[pattern] == 1.5


def test_pattern_net_sell_when_five_or_more_decrease() -> None:
    current_rows = [
        ("Vanguard", 1_240_000_000),
        ("BlackRock", 1_050_000_000),
        ("Berkshire", 860_000_000),
        ("State Street", 580_000_000),
        ("FMR", 300_000_000),
    ]
    prior_rows = [
        ("Vanguard", 1_270_000_000),
        ("BlackRock", 1_073_000_000),
        ("Berkshire", 880_000_000),
        ("State Street", 600_000_000),
        ("FMR", 320_000_000),
    ]
    current = HoldersSnapshot(
        ticker="AAPL", kind="institutional", holders=(),
        fetched_at=_NOW,
        rows=tuple((n, 0.0, s, "2026-03-31") for (n, s) in current_rows),
    )
    prior = HoldersSnapshot(
        ticker="AAPL", kind="institutional", holders=(),
        fetched_at=_NOW - timedelta(days=14),
        rows=tuple((n, 0.0, s, "2026-03-31") for (n, s) in prior_rows),
    )
    _, agg, inc, dec = _holder_deltas(current, prior)
    assert dec == 5
    assert agg < 0
    pattern = _classify_pattern_v2(prior, inc, dec, agg)
    assert pattern == "NET_SELL"
    assert _PATTERN_SCORE[pattern] == -1.5


def test_pattern_mixed_when_split() -> None:
    current_rows = [
        ("A", 110), ("B", 120), ("C", 95), ("D", 85), ("E", 100),
    ]
    prior_rows = [
        ("A", 100), ("B", 110), ("C", 100), ("D", 100), ("E", 100),
    ]
    current = HoldersSnapshot(
        ticker="AAPL", kind="institutional", holders=(),
        fetched_at=_NOW,
        rows=tuple((n, 0.0, s, "") for (n, s) in current_rows),
    )
    prior = HoldersSnapshot(
        ticker="AAPL", kind="institutional", holders=(),
        fetched_at=_NOW - timedelta(days=14),
        rows=tuple((n, 0.0, s, "") for (n, s) in prior_rows),
    )
    _, _agg, inc, dec = _holder_deltas(current, prior)
    pattern = _classify_pattern_v2(prior, inc, dec, _agg)
    assert pattern == "MIXED"
    assert _PATTERN_SCORE[pattern] == 0.0


def test_pattern_insufficient_when_no_prior() -> None:
    pattern = _classify_pattern_v2(None, 5, 0, 100)
    assert pattern == "INSUFFICIENT"


def test_compute_skips_when_no_prior_snapshot(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, fixture)
    expert = EFundFlowExpert(router=router)
    with pytest.raises(ExpertSkipError, match=r"E_FUND_FLOW.*INSUFFICIENT prior snapshot"):
        asyncio.run(expert.compute("AAPL", _NOW))
    broker.close()


def test_compute_skips_when_holders_below_min(tmp_path: Path) -> None:
    blank = {
        "fundamentals": {
            "ticker": "BLANK",
            "pe_ratio": None, "forward_pe": None,
            "eps": None, "forward_eps": None,
            "roe": None, "market_cap": None,
            "dividend_yield": None, "beta": None,
            "fifty_two_week_high": None, "fifty_two_week_low": None,
        },
        "company_facts": {"cik": "0000000000", "entity_name": "Blank Co", "facts": []},
        "institutional_holders": {"ticker": "BLANK", "kind": "institutional", "holders": []},
        "13f_filings": [],
        "13f_holdings": {},
    }
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, blank)
    expert = EFundFlowExpert(router=router)
    with pytest.raises(ExpertSkipError, match=r"E_FUND_FLOW.*INSUFFICIENT holders"):
        asyncio.run(expert.compute("BLANK", _NOW))
    broker.close()


def test_compute_returns_net_buy_signal_with_seeded_prior(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    # Seed a prior with materially smaller share counts so the delta classifier
    # observes 5 holders increasing → NET_BUY → score +1.5 → LONG.
    _seed_prior(
        broker, "AAPL",
        [
            ("Vanguard Group Inc", 0.0820, 1_200_000_000),
            ("BlackRock Inc.", 0.0700, 1_000_000_000),
            ("Berkshire Hathaway Inc", 0.0560, 850_000_000),
            ("State Street Corporation", 0.0390, 580_000_000),
            ("FMR LLC", 0.0200, 300_000_000),
        ],
    )
    router = _build_router(broker, fixture)
    expert = EFundFlowExpert(router=router)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    md = dict(sig.metadata)
    assert md["pattern"] == "NET_BUY"
    assert sig.net_score == pytest.approx(1.5)
    assert sig.direction == "LONG"
    assert sig.archetype == "continuation"
    broker.close()


# ── Direction thresholds (FundFlowScore behavioural) ───────────────────────


def test_direction_long_when_score_above_threshold() -> None:
    s = FundFlowScore(
        pattern="NET_BUY", quarter_directions=("in",) * 5,
        pattern_score=1.5, option_proxy=0.0,
        net_score=1.5, top_holder="Vanguard", top_holder_pct=0.085,
    )
    assert s.direction == "LONG"


def test_direction_short_when_score_below_neg_threshold() -> None:
    s = FundFlowScore(
        pattern="NET_SELL", quarter_directions=("out",) * 5,
        pattern_score=-1.5, option_proxy=0.0,
        net_score=-1.5, top_holder="X", top_holder_pct=0.05,
    )
    assert s.direction == "SHORT"


def test_direction_neutral_in_dead_zone() -> None:
    for v in (-1.0, -0.5, 0.0, 0.5, 1.0):
        s = FundFlowScore(
            pattern="MIXED", quarter_directions=(),
            pattern_score=0.0, option_proxy=0.0,
            net_score=v, top_holder="X", top_holder_pct=0.0,
        )
        assert s.direction == "NEUTRAL", f"score={v}"


def test_archetype_continuation_for_net_flows() -> None:
    s_buy = FundFlowScore(
        pattern="NET_BUY", quarter_directions=("in",) * 5,
        pattern_score=1.5, option_proxy=0.0, net_score=1.5,
        top_holder="X", top_holder_pct=0.0,
    )
    assert s_buy.archetype == "continuation"
    s_sell = FundFlowScore(
        pattern="NET_SELL", quarter_directions=("out",) * 5,
        pattern_score=-1.5, option_proxy=0.0, net_score=-1.5,
        top_holder="X", top_holder_pct=0.0,
    )
    assert s_sell.archetype == "continuation"


def test_archetype_mixed_for_balanced() -> None:
    s = FundFlowScore(
        pattern="MIXED", quarter_directions=("in", "out"),
        pattern_score=0.0, option_proxy=0.0,
        net_score=0.0, top_holder="X", top_holder_pct=0.0,
    )
    assert s.archetype == "mixed"


# ── Top holder helper ──────────────────────────────────────────────────────


def test_top_holder_returns_name_and_pct() -> None:
    snap = HoldersSnapshot(
        ticker="AAPL", kind="institutional",
        holders=(("Vanguard", 0.085), ("BlackRock", 0.072)),
        fetched_at=_NOW,
    )
    name, pct = _top_holder(snap)
    assert name == "Vanguard"
    assert pct == 0.085


def test_top_holder_empty_when_none() -> None:
    name, pct = _top_holder(None)
    assert name == "n/a"
    assert pct == 0.0


# ── Network test — opt-in real AAPL run ────────────────────────────────────


@pytest.mark.network
def test_network_real_aapl_fund_flow(tmp_path: Path) -> None:
    if not os.environ.get("GLOSTAT_SEC_USER_AGENT"):
        pytest.skip("GLOSTAT_SEC_USER_AGENT not set")
    broker = SnapshotBroker(root=tmp_path / "snap")
    yf = YFinanceClient(snapshot_broker=broker)
    sec = SecEdgarClient(snapshot_broker=broker)
    router = DataRouter()
    router.register_client("yfinance", yf)
    router.register_client("sec_edgar", sec)
    expert = EFundFlowExpert(router=router)

    async def run() -> None:
        # First call seeds the snapshot; second call sees a prior and emits a
        # MIXED/NET signal rather than INSUFFICIENT skip.
        import contextlib  # noqa: PLC0415
        with contextlib.suppress(ExpertSkipError):
            await expert.compute("AAPL", datetime.now(tz=UTC))
        sig = await expert.compute("AAPL", datetime.now(tz=UTC) + timedelta(seconds=1))
        assert sig.ticker == "AAPL"
        await sec.aclose()

    asyncio.run(run())
    broker.close()
