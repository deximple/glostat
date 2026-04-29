from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from glostat.cli_mocks import MockYFinanceClient
from glostat.data.data_router import DataRouter
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import YFinanceClient
from glostat.data.yfinance_types import (
    EarningsCalendar,
    EarningsEvent,
    OhlcvBar,
    OhlcvSeries,
)
from glostat.experts import ETimeExpert, TimeScore
from glostat.experts.e_time import (
    _compute_earnings_proximity,
    _compute_t_with_anchor,
)
from tests.fixtures import load_fixture

# ── Helpers ────────────────────────────────────────────────────────────────


_NOW = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _build_router(broker: SnapshotBroker, fixture: dict) -> DataRouter:
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    r = DataRouter()
    r.register_client("yfinance", yf)
    return r


# ── compute() returns a well-formed ExpertSignal ───────────────────────────


def test_compute_returns_expert_signal_structure(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, fixture)
    expert = ETimeExpert(router=router)

    sig = asyncio.run(expert.compute("AAPL", _NOW))

    assert sig.expert_name == "E_TIME"
    assert sig.ticker == "AAPL"
    assert sig.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert -3.0 <= sig.net_score <= 3.0
    assert 0.0 <= sig.confidence <= 1.0
    assert sig.archetype in {"continuation", "impulse"}
    assert sig.basis.startswith("T=")
    assert (sig.expires_at - _NOW).days == 30
    broker.close()


# ── T-score calculation via _compute_T_with_anchor ─────────────────────────


def test_t_score_calculation_high_t(tmp_path: Path) -> None:
    # The fixture is engineered for T=2.0 (3 anchors hit 3 bases).
    fixture = load_fixture("aapl_mock.json")
    bars = tuple(
        OhlcvBar(
            ts=datetime.fromisoformat(b["ts"] + "T00:00:00+00:00"),
            open=b["open"], high=b["high"], low=b["low"],
            close=b["close"], volume=b["volume"], adj_close=b["close"],
        )
        for b in fixture["ohlcv"]
    )
    series = OhlcvSeries(ticker="AAPL", bars=bars)
    t, matched, anchors = _compute_t_with_anchor(series, _NOW.date())
    assert t == 2.0
    assert len(matched) >= 3
    assert len(anchors) >= 1


def test_t_score_zero_when_no_ohlcv() -> None:
    t, matched, anchors = _compute_t_with_anchor(None, _NOW.date())
    assert t == 0.0
    assert matched == []
    assert anchors == []


# ── earnings proximity ─────────────────────────────────────────────────────


def test_earnings_proximity_pre_earnings_bonus() -> None:
    today = date(2026, 4, 28)
    # Sprint 5 PR #1: earnings 7 days out → in [0, 30] → +0.3 (was 14d / +0.5).
    cal = EarningsCalendar(
        ticker="AAPL",
        upcoming=(EarningsEvent(
            ticker="AAPL",
            earnings_date=datetime(2026, 5, 5, tzinfo=UTC),
            eps_estimate=1.5, eps_actual=None, revenue_estimate=80e9,
        ),),
    )
    days_to, p = _compute_earnings_proximity(cal, today)
    assert days_to == 7
    assert p == pytest.approx(0.3)


def test_earnings_proximity_window_extends_to_30_days() -> None:
    today = date(2026, 4, 28)
    # Sprint 5 PR #1 — 21d still inside the 30d window → +0.3 bonus.
    cal = EarningsCalendar(
        ticker="AAPL",
        upcoming=(EarningsEvent(
            ticker="AAPL",
            earnings_date=datetime(2026, 5, 19, tzinfo=UTC),
            eps_estimate=None, eps_actual=None, revenue_estimate=None,
        ),),
    )
    days_to, p = _compute_earnings_proximity(cal, today)
    assert days_to == 21
    assert p == pytest.approx(0.3)


def test_earnings_proximity_far_future_no_bonus() -> None:
    today = date(2026, 4, 28)
    # Sprint 5 PR #1: 90 days out → outside relaxed 30d window → 0.0.
    cal = EarningsCalendar(
        ticker="AAPL",
        upcoming=(EarningsEvent(
            ticker="AAPL",
            earnings_date=datetime(2026, 7, 27, tzinfo=UTC),
            eps_estimate=None, eps_actual=None, revenue_estimate=None,
        ),),
    )
    days_to, p = _compute_earnings_proximity(cal, today)
    assert days_to == 90
    assert p == 0.0


def test_earnings_proximity_no_calendar() -> None:
    days_to, p = _compute_earnings_proximity(None, date(2026, 4, 28))
    assert days_to is None
    assert p == 0.0


def test_earnings_proximity_skips_past_events() -> None:
    today = date(2026, 4, 28)
    cal = EarningsCalendar(
        ticker="AAPL",
        upcoming=(EarningsEvent(
            ticker="AAPL",
            earnings_date=datetime(2026, 4, 1, tzinfo=UTC),
            eps_estimate=None, eps_actual=None, revenue_estimate=None,
        ),),
    )
    days_to, p = _compute_earnings_proximity(cal, today)
    assert days_to is None  # no future event
    assert p == 0.0


# ── direction thresholds ───────────────────────────────────────────────────


def test_direction_long_when_score_positive() -> None:
    s = TimeScore(
        t_value=2.0, matched_bases=(65, 129, 172),
        earnings_proximity=0.5, days_to_earnings=7,
        net_score=2.0,
    )
    assert s.direction == "LONG"


def test_direction_neutral_in_dead_zone() -> None:
    for v in (-1.0, -0.5, 0.0, 0.5, 1.0):
        s = TimeScore(
            t_value=1.0, matched_bases=(65,),
            earnings_proximity=0.0, days_to_earnings=None,
            net_score=v,
        )
        assert s.direction == "NEUTRAL", f"score={v}"


def test_direction_short_when_score_very_negative() -> None:
    s = TimeScore(
        t_value=0.0, matched_bases=(),
        earnings_proximity=0.0, days_to_earnings=None,
        net_score=-1.5,
    )
    assert s.direction == "SHORT"


# ── INV-GS-008: bonus_eligible_T metadata ──────────────────────────────────


def test_inv_gs_008_bonus_eligible_when_t_high() -> None:
    s = TimeScore(
        t_value=1.5, matched_bases=(65, 129),
        earnings_proximity=0.0, days_to_earnings=None,
        net_score=1.125,
    )
    assert s.bonus_eligible_t is True


def test_inv_gs_008_bonus_not_eligible_when_t_low() -> None:
    s = TimeScore(
        t_value=1.0, matched_bases=(65,),
        earnings_proximity=0.0, days_to_earnings=None,
        net_score=0.75,
    )
    assert s.bonus_eligible_t is False


def test_inv_gs_008_metadata_flag_in_signal(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, fixture)
    expert = ETimeExpert(router=router)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    md = dict(sig.metadata)
    assert "bonus_eligible_T" in md
    # The fixture is engineered so T=2.0 → flag must be True.
    assert md["bonus_eligible_T"] == "True"
    broker.close()


# ── sources populated ──────────────────────────────────────────────────────


def test_sources_populated_with_two_snapshots(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, fixture)
    expert = ETimeExpert(router=router)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    assert len(sig.sources) >= 2
    assert any("yfinance.history" in s for s in sig.sources)
    assert any("yfinance.calendar" in s for s in sig.sources)
    broker.close()


# ── confidence caps at 1.0 ─────────────────────────────────────────────────


def test_confidence_caps_at_1() -> None:
    s = TimeScore(
        t_value=2.0, matched_bases=(65, 129, 172),
        earnings_proximity=0.5, days_to_earnings=7,
        net_score=2.0,
    )
    assert s.confidence == 1.0


def test_confidence_zero_when_no_t() -> None:
    s = TimeScore(
        t_value=0.0, matched_bases=(),
        earnings_proximity=0.0, days_to_earnings=None,
        net_score=0.0,
    )
    assert s.confidence == 0.0


# ── snapshot integration ───────────────────────────────────────────────────


def test_each_fetch_records_a_snapshot(tmp_path: Path) -> None:
    fixture = load_fixture("aapl_mock.json")
    broker = SnapshotBroker(root=tmp_path / "snap")
    router = _build_router(broker, fixture)
    expert = ETimeExpert(router=router)
    asyncio.run(expert.compute("AAPL", _NOW))
    rows = list(broker.list_snapshots())
    edges = sorted(r.leaf.key.edge_type for r in rows)
    # 2 expected: ohlcv + earnings_calendar
    assert "ohlcv" in edges
    assert "earnings_calendar" in edges
    broker.close()


# ── network test ───────────────────────────────────────────────────────────


@pytest.mark.network
def test_real_aapl_e_time(tmp_path: Path) -> None:
    if not os.environ.get("NETWORK_TESTS"):
        pytest.skip("network gate")
    broker = SnapshotBroker(root=tmp_path / "snap")
    yf = YFinanceClient(snapshot_broker=broker)
    router = DataRouter()
    router.register_client("yfinance", yf)
    expert = ETimeExpert(router=router)
    sig = asyncio.run(expert.compute("AAPL", datetime.now(tz=UTC)))
    assert sig.ticker == "AAPL"
    assert (sig.expires_at - datetime.now(tz=UTC)) > timedelta(days=29)
    broker.close()
