from __future__ import annotations

import asyncio
import math
import os
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from glostat.cli import _load_market_meta
from glostat.cli_hindcast_live import run_live_hindcast, trading_days_count
from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import YFinanceClient
from glostat.replay.live_hindcast import (
    LiveActualReturnFetcher,
    LiveHindcastVerdictBuilder,
    SecEdgarUserAgentError,
    make_live_components,
    summarize_network,
)

# Sprint 4 PR #2 — live data hindcast tests. ALL @pytest.mark.network so the
# default offline CI run skips them. Set NETWORK_TESTS=1 + GLOSTAT_SEC_USER_AGENT
# to a real contact to run them locally.

# ── unit-level (no network) ────────────────────────────────────────────────


def test_trading_days_count_excludes_weekends() -> None:
    # 2026-02-01 (Sun) → 2026-02-07 (Sat): trading days = Mon..Fri = 5.
    assert trading_days_count(date(2026, 2, 2), date(2026, 2, 6)) == 5
    assert trading_days_count(date(2026, 2, 1), date(2026, 2, 7)) == 5


def test_live_actual_return_fetcher_drops_future_days(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Today is 2026-04-28 per CLAUDE.md. day=2026-04-15 + horizon=30 → 2026-05-15
    # is in the future → fetcher returns None and counts a drop.
    today = date(2026, 4, 28)
    fake_yf = MagicMock(spec=YFinanceClient)
    f = LiveActualReturnFetcher(
        yf_client=fake_yf,
        cache_path=Path("/tmp/glostat_dummy_cache.parquet"),
    )
    f._today = today
    res = asyncio.run(f.fetch("AAPL", date(2026, 4, 15), 30))
    assert res is None
    assert f.dropped_count == 1
    fake_yf.get_ohlcv.assert_not_called()


def test_live_actual_return_fetcher_caches_per_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    # Two consecutive fetches for same (ticker, day) → only one yfinance call.
    fake_yf = MagicMock(spec=YFinanceClient)
    fake_series = MagicMock()
    fake_series.bars = (
        MagicMock(ts=datetime(2026, 1, 5, 16, 0, tzinfo=UTC), close=180.0),
        MagicMock(ts=datetime(2026, 2, 4, 16, 0, tzinfo=UTC), close=189.0),
    )

    async def fake_get_ohlcv(*_a: Any, **_kw: Any) -> Any:
        return fake_series

    fake_yf.get_ohlcv = fake_get_ohlcv
    cache = tmp_path / "actuals.parquet"
    f = LiveActualReturnFetcher(yf_client=fake_yf, cache_path=cache)
    f._today = date(2026, 4, 28)
    r1 = asyncio.run(f.fetch("AAPL", date(2026, 1, 5), 30))
    r2 = asyncio.run(f.fetch("AAPL", date(2026, 1, 5), 30))
    assert r1 == r2 != 0
    assert f.fetch_count == 1
    assert f.cache_hit_count == 1


def test_live_actual_return_fetcher_persist_roundtrip(tmp_path: Path) -> None:
    # Persist cache then reopen with a fresh fetcher → cache repopulated.
    fake_yf = MagicMock(spec=YFinanceClient)
    cache = tmp_path / "actuals.parquet"
    f1 = LiveActualReturnFetcher(yf_client=fake_yf, cache_path=cache)
    f1._cache["AAPL|2026-01-05"] = 0.05
    f1._cache["MSFT|2026-01-05"] = -0.02
    f1.persist()
    assert cache.exists()
    f2 = LiveActualReturnFetcher(yf_client=fake_yf, cache_path=cache)
    assert math.isclose(f2._cache["AAPL|2026-01-05"], 0.05, rel_tol=1e-12)
    assert math.isclose(f2._cache["MSFT|2026-01-05"], -0.02, rel_tol=1e-12)


def test_summarize_network_includes_all_counters() -> None:
    fake_yf = MagicMock(spec=YFinanceClient)
    fake_yf.throttle = MagicMock(acquire_count=10, throttled_count=2)
    fake_sec = MagicMock()
    fake_sec.throttle = MagicMock(acquire_count=5, throttled_count=0)
    market_meta = _load_market_meta("XNAS")
    builder = LiveHindcastVerdictBuilder(
        market_meta=market_meta, horizon_days=30,
        yf_client=fake_yf, sec_client=fake_sec,
        router=MagicMock(),
    )
    builder._build_count = 3
    builder._failure_count = 1
    builder._failed_tickers.add("XYZ")
    fetcher = LiveActualReturnFetcher(
        yf_client=fake_yf, cache_path=Path("/tmp/x.parquet"),
    )
    fetcher.fetch_count = 4
    fetcher.cache_hit_count = 2
    fetcher.dropped_count = 1
    s = summarize_network(builder, fetcher)
    assert s["yfinance_calls"] == 10
    assert s["sec_edgar_calls"] == 5
    assert s["actual_return_dropped"] == 1
    assert s["verdicts_built"] == 3
    assert "XYZ" in s["failed_tickers"]


def test_sec_edgar_user_agent_error_is_runtime() -> None:
    e = SecEdgarUserAgentError("bad")
    assert isinstance(e, RuntimeError)


# ── network-bound (skipped offline) ────────────────────────────────────────


def _skip_if_no_user_agent() -> None:
    if not os.environ.get("GLOSTAT_SEC_USER_AGENT"):
        pytest.skip("GLOSTAT_SEC_USER_AGENT not set — required for live SEC calls")


@pytest.mark.network
def test_live_actual_return_fetcher_real_aapl_2026q1(tmp_path: Path) -> None:
    # AAPL Feb 1 2026 → May 1 forward return: must be finite and non-zero.
    cache = tmp_path / "actuals.parquet"
    yf_client = YFinanceClient()
    fetcher = LiveActualReturnFetcher(yf_client=yf_client, cache_path=cache)
    res = asyncio.run(fetcher.fetch("AAPL", date(2026, 2, 2), 30))
    if res is None:
        pytest.skip("yfinance unavailable for AAPL 2026-02-02 — no fallback in CI")
    assert math.isfinite(res)
    # 30-day swing return for a megacap should land within ±0.5 in normal markets.
    assert -0.5 < res < 0.5


@pytest.mark.network
def test_live_sec_edgar_user_agent_real_call() -> None:
    _skip_if_no_user_agent()

    async def _go() -> str:
        client = SecEdgarClient()
        try:
            return await client.ticker_to_cik("AAPL")
        finally:
            await client.aclose()

    cik = asyncio.run(_go())
    assert cik.isdigit()
    assert len(cik) == 10
    assert int(cik) == 320193  # Apple's CIK


@pytest.mark.network
def test_live_5_ticker_hindcast_runs_in_under_300s(tmp_path: Path) -> None:
    _skip_if_no_user_agent()
    market_meta = _load_market_meta("XNAS")
    snapshot_root = tmp_path / "snapshots"
    actual_cache = tmp_path / "actuals.parquet"
    t0 = time.monotonic()
    result = run_live_hindcast(
        market_meta=market_meta,
        horizon_days=30,
        tickers=("AAPL", "MSFT", "NVDA", "GOOGL", "META"),
        start_date=date(2026, 2, 17),
        end_date=date(2026, 2, 27),
        split=0.7,
        parallel_tickers=5,
        snapshot_root=snapshot_root,
        actual_cache=actual_cache,
    )
    elapsed = time.monotonic() - t0
    assert elapsed < 300, f"hindcast took {elapsed:0.1f}s — over budget"
    assert result["aborted_reason"] is None, result["aborted_reason"]
    assert result["report"] is not None
    assert result["report"].n_verdicts > 0
    assert result["summary"]["yfinance_calls"] > 0


@pytest.mark.network
def test_live_handles_missing_recent_data(tmp_path: Path) -> None:
    # end=2026-04-15 → some days have no full 30-day forward return → dropped > 0.
    _skip_if_no_user_agent()
    market_meta = _load_market_meta("XNAS")
    result = run_live_hindcast(
        market_meta=market_meta,
        horizon_days=30,
        tickers=("AAPL",),
        start_date=date(2026, 4, 13),
        end_date=date(2026, 4, 15),
        split=0.7,
        parallel_tickers=2,
        snapshot_root=tmp_path / "snap",
        actual_cache=tmp_path / "actuals.parquet",
    )
    summary = result["summary"]
    # WHY: today is 2026-04-28; 2026-04-15 + 30d = 2026-05-15 → in future → drop.
    assert summary.get("actual_return_dropped", 0) > 0


@pytest.mark.network
def test_live_snapshot_persistence(tmp_path: Path) -> None:
    _skip_if_no_user_agent()
    market_meta = _load_market_meta("XNAS")
    snapshot_root = tmp_path / "snapshots"
    result = run_live_hindcast(
        market_meta=market_meta,
        horizon_days=30,
        tickers=("AAPL",),
        start_date=date(2026, 2, 17),
        end_date=date(2026, 2, 19),
        split=0.7,
        parallel_tickers=2,
        snapshot_root=snapshot_root,
        actual_cache=tmp_path / "actuals.parquet",
    )
    assert result["report"] is not None
    broker = SnapshotBroker(root=snapshot_root)
    try:
        rows = list(broker._db.execute(
            "SELECT COUNT(*) AS n FROM snapshots"
        ).fetchall())
    finally:
        broker.close()
    assert rows and int(rows[0]["n"]) > 0


@pytest.mark.network
def test_live_make_components_smoke(tmp_path: Path) -> None:
    _skip_if_no_user_agent()
    market_meta = _load_market_meta("XNAS")
    broker = SnapshotBroker(root=tmp_path / "snap")
    try:
        builder, fetcher, sec_client = make_live_components(
            market_meta=market_meta,
            horizon_days=30,
            snapshot_broker=broker,
            actual_cache_path=tmp_path / "actuals.parquet",
        )
    finally:
        # WHY: aclose must run inside an event loop — wrap.
        async def _close() -> None:
            await sec_client.aclose()

        try:
            asyncio.run(_close())
        finally:
            broker.close()
    assert builder is not None
    assert fetcher is not None


# Suppress unused-import noise.
_ = sys
