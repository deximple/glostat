from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from glostat.cli_mocks import MockSecEdgarClient, MockYFinanceClient
from glostat.core.errors import ExpertSkipError
from glostat.data.data_router import DataRouter
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.experts import EFundamentalExpert, EFundFlowExpert, ETimeExpert

# Sprint 4 PR #3 — Expert fail-fast guards. PR #2 produced 200 verdicts where
# every E_TIME emitted t=0.0 and every E_FUND_FLOW emitted INSUFFICIENT — those
# are now ExpertSkipError so the harness can record honest skips and the
# Sharpe / AUC denominators stop being polluted by fake-neutral signals.

_NOW: datetime = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _build_router(broker: SnapshotBroker, fixture: dict[str, Any]) -> DataRouter:
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    sec = MockSecEdgarClient(broker=broker, fixture=fixture)
    router = DataRouter()
    router.register_client("yfinance", yf)
    router.register_client("sec_edgar", sec)
    return router


def _blank_fixture(ticker: str = "BLANK") -> dict[str, Any]:
    return {
        "ticker": ticker,
        "fundamentals": {
            "ticker": ticker,
            "pe_ratio": None, "forward_pe": None,
            "eps": None, "forward_eps": None,
            "roe": None, "market_cap": None,
            "dividend_yield": None, "beta": None,
            "fifty_two_week_high": None, "fifty_two_week_low": None,
        },
        "company_facts": {"cik": "0000000000", "entity_name": "Blank Co", "facts": []},
        "ohlcv": [],
        "earnings_calendar": {"ticker": ticker, "upcoming": []},
        "dividends": {"ticker": ticker, "events": []},
        "institutional_holders": {"ticker": ticker, "holders": []},
        "13f_filings": [],
        "13f_holdings": {},
    }


# ── E_FUNDAMENTAL ──────────────────────────────────────────────────────────


def test_e_fundamental_missing_per_raises_skip(tmp_path: Path) -> None:
    fixture = _blank_fixture()
    broker = SnapshotBroker(root=tmp_path / "snap")
    expert = EFundamentalExpert(router=_build_router(broker, fixture))
    with pytest.raises(ExpertSkipError, match=r"E_FUNDAMENTAL.*missing PER.*BLANK"):
        asyncio.run(expert.compute("BLANK", _NOW))
    broker.close()


def test_e_fundamental_with_only_forward_pe_does_not_skip(tmp_path: Path) -> None:
    fixture = _blank_fixture()
    fixture["fundamentals"]["forward_pe"] = 22.0
    broker = SnapshotBroker(root=tmp_path / "snap")
    expert = EFundamentalExpert(router=_build_router(broker, fixture))
    sig = asyncio.run(expert.compute("BLANK", _NOW))
    assert sig.expert_name == "E_FUNDAMENTAL"
    broker.close()


# ── E_TIME ─────────────────────────────────────────────────────────────────


def test_e_time_short_ohlcv_raises_skip(tmp_path: Path) -> None:
    fixture = _blank_fixture()
    fixture["ohlcv"] = [
        {"ts": f"2026-04-{(i % 28) + 1:02d}", "open": 100.0, "high": 101.0,
         "low": 99.0, "close": 100.0, "volume": 1_000_000}
        for i in range(50)
    ]
    broker = SnapshotBroker(root=tmp_path / "snap")
    expert = ETimeExpert(router=_build_router(broker, fixture))
    with pytest.raises(ExpertSkipError, match=r"E_TIME.*insufficient OHLCV"):
        asyncio.run(expert.compute("BLANK", _NOW))
    broker.close()


def test_e_time_no_convergence_returns_neutral_signal(tmp_path: Path) -> None:
    # Sprint 5 PR #1: when anchors don't converge on the verdict day, E_TIME now
    # emits a neutral signal rather than skipping. We engineer a strictly
    # decreasing series ending the day before _NOW so the anchor low equals
    # the last bar — anchor + 65 bdays lands well past today.
    fixture = _blank_fixture()
    bars: list[dict[str, Any]] = []
    end = _NOW.date() - timedelta(days=1)
    n = 320
    for i in range(n):
        d = end - timedelta(days=(n - 1) - i)
        if d.weekday() >= 5:
            continue
        # Strictly decreasing toward the present → anchor = end (most recent).
        # end + 65 bdays = far future, all bases miss today.
        price = 200.0 - (i * 0.1)
        bars.append({
            "ts": d.isoformat(),
            "open": price, "high": price, "low": price, "close": price,
            "volume": 1_000_000,
        })
    fixture["ohlcv"] = bars
    fixture["earnings_calendar"] = {"ticker": "BLANK", "upcoming": []}
    broker = SnapshotBroker(root=tmp_path / "snap")
    expert = ETimeExpert(router=_build_router(broker, fixture))
    sig = asyncio.run(expert.compute("BLANK", _NOW))
    assert sig.expert_name == "E_TIME"
    assert sig.direction == "NEUTRAL"
    assert sig.net_score == 0.0
    broker.close()


# ── E_FUND_FLOW ────────────────────────────────────────────────────────────


def test_e_fund_flow_insufficient_quarters_raises_skip(tmp_path: Path) -> None:
    fixture = _blank_fixture()
    broker = SnapshotBroker(root=tmp_path / "snap")
    expert = EFundFlowExpert(router=_build_router(broker, fixture))
    with pytest.raises(ExpertSkipError, match=r"E_FUND_FLOW.*INSUFFICIENT.*BLANK"):
        asyncio.run(expert.compute("BLANK", _NOW))
    broker.close()


def test_expert_skip_error_is_glostat_error() -> None:
    from glostat.core.errors import GlostatError  # noqa: PLC0415

    err = ExpertSkipError("test")
    assert isinstance(err, GlostatError)
    assert isinstance(err, RuntimeError)
