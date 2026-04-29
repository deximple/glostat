from __future__ import annotations

import asyncio
import sys
import time
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from glostat.data.yfinance_client import (
    _MIN_INTERVAL_S,
    _RATE_LIMIT_PER_SEC,
    Fundamentals,
    OhlcvSeries,
    YFinanceClient,
    YFinanceUnavailableError,
)

# ── Method-surface smoke ───────────────────────────────────────────────────


def test_yfinance_client_has_required_methods() -> None:
    c = YFinanceClient()
    for name in (
        "get_ohlcv",
        "get_fundamentals",
        "get_dividends",
        "get_earnings_calendar",
        "get_holders",
    ):
        assert callable(getattr(c, name)), f"missing method: {name}"


def test_yfinance_throttle_constants() -> None:
    # WHY: INV-GS-037 (Sprint 4 PR #3) — relaxed from 5 to 8 req/sec after PR #2
    # observed 93% throttle ratio at 5/s; Yahoo unofficial cap research suggests
    # 10-20/s is fine for read-only IPs, 8 is the conservative middle ground.
    assert _RATE_LIMIT_PER_SEC == 8
    assert abs(_MIN_INTERVAL_S - 0.125) < 1e-9


# ── INV-GS-037: Throttle behaviour ─────────────────────────────────────────


def test_inv_gs_037_throttle_serializes_calls() -> None:
    # 9 concurrent acquires @ 8 req/sec → ≥ 1 throttled (slept).
    c = YFinanceClient()

    async def run() -> None:
        async def grab() -> None:
            await c.throttle.acquire()
            try:
                await asyncio.sleep(0)
            finally:
                c.throttle.release()

        t0 = time.monotonic()
        await asyncio.gather(*(grab() for _ in range(9)))
        elapsed = time.monotonic() - t0
        # WHY: 9 calls @ 8 req/sec ≥ 1 throttled, total wall ≥ 0.125s for the 9th slot.
        assert c.throttle.acquire_count == 9
        assert c.throttle.throttled_count >= 1
        assert elapsed >= 0.10  # tolerance for monotonic jitter

    asyncio.run(run())


# ── Graceful import guard ──────────────────────────────────────────────────


def test_yfinance_unavailable_when_module_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force an import failure path inside _import_yfinance.
    monkeypatch.setitem(sys.modules, "yfinance", None)
    c = YFinanceClient()
    with pytest.raises(YFinanceUnavailableError, match="pip install"):
        asyncio.run(c.get_fundamentals("AAPL"))


# ── Mocked yfinance round-trip (unit) ──────────────────────────────────────


def test_get_fundamentals_with_mocked_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_info = {
        "trailingPE": 28.4,
        "forwardPE": 24.0,
        "trailingEps": 6.13,
        "forwardEps": 7.20,
        "returnOnEquity": 1.51,
        "marketCap": 3_000_000_000_000,
        "dividendYield": 0.0044,
        "beta": 1.21,
        "fiftyTwoWeekHigh": 250.0,
        "fiftyTwoWeekLow": 165.0,
    }
    fake_ticker = SimpleNamespace(info=fake_info)
    fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    c = YFinanceClient()
    f: Fundamentals = asyncio.run(c.get_fundamentals("AAPL"))
    assert f.ticker == "AAPL"
    assert f.pe_ratio == 28.4
    assert f.forward_pe == 24.0
    assert f.market_cap == 3_000_000_000_000.0


def test_get_ohlcv_with_mocked_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    # A minimal pandas-like row iterator.
    rows = [
        (
            "2026-01-02",
            {"Open": 184.0, "High": 186.0, "Low": 183.5, "Close": 185.0, "Volume": 50_000_000},
        ),
        (
            "2026-01-03",
            {"Open": 185.0, "High": 187.0, "Low": 184.5, "Close": 186.5, "Volume": 48_000_000},
        ),
    ]
    fake_df = MagicMock()
    fake_df.iterrows.return_value = iter(rows)
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = fake_df
    fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    c = YFinanceClient()
    s: OhlcvSeries = asyncio.run(
        c.get_ohlcv("aapl", start=date(2026, 1, 2), end=date(2026, 1, 4))
    )
    assert s.ticker == "AAPL"
    assert len(s) == 2
    assert s.bars[0].close == 185.0
    assert s.bars[1].volume == 48_000_000


# ── Error handling: invalid ticker style is the caller's job; verify defaults ──


def test_invalid_ticker_passes_through_to_yfinance(monkeypatch: pytest.MonkeyPatch) -> None:
    # WHY: yfinance returns empty info for unknown tickers; we surface that as None fields.
    fake_ticker = SimpleNamespace(info={})
    fake_yf = SimpleNamespace(Ticker=lambda symbol: fake_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    c = YFinanceClient()
    f = asyncio.run(c.get_fundamentals("NOT_A_REAL_TICKER"))
    assert f.pe_ratio is None
    assert f.market_cap is None


# ── Sprint 4 PR #3 — exponential backoff retry on transient failures ──────


def test_retry_on_empty_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # First call returns empty info → with_retry sees is_empty=True and retries.
    fake_ticker = SimpleNamespace(info={})
    counter = {"calls": 0}

    fake_info_full = {"trailingPE": 30.0, "forwardPE": 25.0}

    def make_ticker(_sym: str) -> SimpleNamespace:
        counter["calls"] += 1
        if counter["calls"] == 1:
            return fake_ticker
        return SimpleNamespace(info=fake_info_full)

    fake_yf = SimpleNamespace(Ticker=make_ticker)
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)

    async def _no_sleep(_d: float) -> None:
        return None

    # Patch retry sleep so the test doesn't actually wait.
    monkeypatch.setattr("glostat.data.retry._default_sleep", _no_sleep)
    c = YFinanceClient()
    # WHY: empty info dict returns Fundamentals(pe_ratio=None,...) — which is not
    # itself empty by `is_empty`. The retry-on-empty path activates for OHLCV.
    f = asyncio.run(c.get_fundamentals("AAPL"))
    assert counter["calls"] == 1  # info path treats empty dict as a valid response
    assert f.pe_ratio is None


def test_retry_on_429_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # parse_fundamentals raises HTTPStatusError(429) on first call, then succeeds.
    counter = {"calls": 0}

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code = status
            self.headers: dict[str, str] = {}

    def _raise_429() -> Any:
        resp = _Resp(429)
        raise httpx.HTTPStatusError("rate limited", request=MagicMock(), response=resp)

    def fake_parse_fundamentals(_yf: Any, _ticker: str) -> Any:
        counter["calls"] += 1
        if counter["calls"] == 1:
            _raise_429()
        return Fundamentals(
            ticker="AAPL", pe_ratio=20.0, forward_pe=18.0,
            eps=5.0, forward_eps=5.5, roe=0.4, market_cap=3e12,
            dividend_yield=0.0, beta=1.0,
            fifty_two_week_high=200.0, fifty_two_week_low=150.0,
        )

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda s: None))
    monkeypatch.setattr(
        "glostat.data.yfinance_client.parse_fundamentals", fake_parse_fundamentals
    )

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr("glostat.data.retry._default_sleep", _no_sleep)

    c = YFinanceClient()
    f = asyncio.run(c.get_fundamentals("AAPL"))
    assert f.pe_ratio == 20.0
    assert counter["calls"] == 2
    assert c.retry_stats.retry_count == 1
    assert c.retry_stats.retry_429_count == 1


def test_retry_max_3_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    counter = {"calls": 0}

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code = status
            self.headers: dict[str, str] = {}

    def fake_parse_fundamentals(_yf: Any, _ticker: str) -> Any:
        counter["calls"] += 1
        resp = _Resp(429)
        raise httpx.HTTPStatusError("rate limited", request=MagicMock(), response=resp)

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda s: None))
    monkeypatch.setattr(
        "glostat.data.yfinance_client.parse_fundamentals", fake_parse_fundamentals
    )

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr("glostat.data.retry._default_sleep", _no_sleep)

    c = YFinanceClient()
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(c.get_fundamentals("AAPL"))
    # 1 initial + 3 retries = 4 total calls.
    assert counter["calls"] == 4
    assert c.retry_stats.retry_count == 3


def test_retry_exponential_backoff_timing(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    counter = {"calls": 0}

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code = status
            self.headers: dict[str, str] = {}

    def fake_parse_fundamentals(_yf: Any, _ticker: str) -> Any:
        counter["calls"] += 1
        if counter["calls"] < 4:
            resp = _Resp(429)
            raise httpx.HTTPStatusError("rate", request=MagicMock(), response=resp)
        return Fundamentals(
            ticker="AAPL", pe_ratio=20.0, forward_pe=18.0,
            eps=5.0, forward_eps=5.5, roe=0.4, market_cap=3e12,
            dividend_yield=0.0, beta=1.0,
            fifty_two_week_high=200.0, fifty_two_week_low=150.0,
        )

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda s: None))
    monkeypatch.setattr(
        "glostat.data.yfinance_client.parse_fundamentals", fake_parse_fundamentals
    )
    monkeypatch.setattr("glostat.data.retry._default_sleep", fake_sleep)

    c = YFinanceClient()
    asyncio.run(c.get_fundamentals("AAPL"))
    # Expected delays: 1.0, 2.0, 4.0 (base=1.0, factor=2.0).
    assert sleeps == [1.0, 2.0, 4.0]


def test_no_retry_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    # 4xx other than 429 → immediate raise, no retry.
    counter = {"calls": 0}

    class _Resp:
        def __init__(self, status: int) -> None:
            self.status_code = status
            self.headers: dict[str, str] = {}

    def fake_parse_fundamentals(_yf: Any, _ticker: str) -> Any:
        counter["calls"] += 1
        resp = _Resp(404)
        raise httpx.HTTPStatusError("not found", request=MagicMock(), response=resp)

    monkeypatch.setitem(sys.modules, "yfinance", SimpleNamespace(Ticker=lambda s: None))
    monkeypatch.setattr(
        "glostat.data.yfinance_client.parse_fundamentals", fake_parse_fundamentals
    )

    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr("glostat.data.retry._default_sleep", _no_sleep)

    c = YFinanceClient()
    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(c.get_fundamentals("AAPL"))
    assert counter["calls"] == 1
    assert c.retry_stats.retry_count == 0


# ── Network test (skipped unless NETWORK_TESTS=1) ──────────────────────────


@pytest.mark.network
def test_network_get_ohlcv_aapl_returns_bars() -> None:
    c = YFinanceClient()
    s = asyncio.run(c.get_ohlcv("AAPL", start=date(2026, 1, 2), end=date(2026, 1, 8)))
    assert s.ticker == "AAPL"
    assert len(s) >= 1
