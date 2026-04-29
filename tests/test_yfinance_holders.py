from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import YFinanceClient
from glostat.data.yfinance_types import HoldersSnapshot


def _fake_yf_with_holders(
    institutional: list[tuple[str, float, int]] | None = None,
    major: list[tuple[str, float, int]] | None = None,
    mutualfund: list[tuple[str, float, int]] | None = None,
) -> SimpleNamespace:
    def _df(rows: list[tuple[str, float, int]] | None) -> MagicMock | None:
        if rows is None:
            return None
        df = MagicMock()
        df.iterrows.return_value = iter(
            (
                i,
                {
                    "Holder": name,
                    "% Out": pct,
                    "Shares": shares,
                    "Date Reported": "2026-03-31",
                },
            )
            for i, (name, pct, shares) in enumerate(rows)
        )
        return df

    fake_ticker = MagicMock()
    fake_ticker.institutional_holders = _df(institutional)
    fake_ticker.major_holders = _df(major)
    fake_ticker.mutualfund_holders = _df(mutualfund)
    return SimpleNamespace(Ticker=lambda symbol: fake_ticker)


# ── institutional ──────────────────────────────────────────────────────────


def test_get_holders_institutional(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_yf_with_holders(
        institutional=[
            ("Vanguard Group Inc", 0.085, 1_280_000_000),
            ("BlackRock Inc.", 0.072, 1_083_000_000),
        ]
    )
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    c = YFinanceClient()
    snap: HoldersSnapshot = asyncio.run(c.get_holders("AAPL", kind="institutional"))
    assert snap.ticker == "AAPL"
    assert snap.kind == "institutional"
    assert len(snap.holders) == 2
    assert snap.holders[0][0] == "Vanguard Group Inc"
    assert snap.holders[0][1] == pytest.approx(0.085)


# ── major ──────────────────────────────────────────────────────────────────


def test_get_holders_major(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_yf_with_holders(
        major=[("Insiders", 0.0007, 0), ("Institutions", 0.6020, 0)]
    )
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    c = YFinanceClient()
    snap = asyncio.run(c.get_holders("AAPL", kind="major"))
    assert snap.kind == "major"
    assert len(snap.holders) == 2


# ── mutualfund ─────────────────────────────────────────────────────────────


def test_get_holders_mutualfund(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_yf_with_holders(
        mutualfund=[("Vanguard Total Stock Mkt Idx", 0.034, 510_000_000)]
    )
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    c = YFinanceClient()
    snap = asyncio.run(c.get_holders("AAPL", kind="mutualfund"))
    assert snap.kind == "mutualfund"
    assert len(snap.holders) == 1


# ── empty / missing data ───────────────────────────────────────────────────


def test_get_holders_empty_dataframe(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _fake_yf_with_holders(institutional=[])
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    c = YFinanceClient()
    snap = asyncio.run(c.get_holders("AAPL", kind="institutional"))
    assert snap.holders == ()


def test_get_holders_none_attribute(monkeypatch: pytest.MonkeyPatch) -> None:
    # WHY: yfinance returns None for unknown tickers / network errors.
    fake = _fake_yf_with_holders(institutional=None)
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    c = YFinanceClient()
    snap = asyncio.run(c.get_holders("ZZZZ", kind="institutional"))
    assert snap.holders == ()


# ── snapshot integration ───────────────────────────────────────────────────


def test_snapshot_integration(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = _fake_yf_with_holders(
        institutional=[("Vanguard", 0.085, 1280_000_000)]
    )
    monkeypatch.setitem(sys.modules, "yfinance", fake)
    broker = SnapshotBroker(root=tmp_path / "snap")
    c = YFinanceClient(snapshot_broker=broker)
    asyncio.run(c.get_holders("AAPL", kind="institutional"))
    rows = list(broker.list_snapshots(edge_type="holders.institutional"))
    assert len(rows) == 1
    assert c.last_snapshot_id is not None
    broker.close()
