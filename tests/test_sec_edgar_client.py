from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from glostat.core.errors import ConfigError
from glostat.data.sec_edgar_client import (
    _MIN_INTERVAL_S,
    _RATE_LIMIT_PER_SEC,
    SecEdgarClient,
)

_VALID_AGENT = "GLOSTAT test@gloss.dev"


# ── INV-GS-038: User-Agent enforcement ─────────────────────────────────────


def test_inv_gs_038_default_agent_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # Default agent contains "example.com" → must refuse. Monkeypatch the env
    # in case the suite is run with a real GLOSTAT_SEC_USER_AGENT for live-mode.
    monkeypatch.delenv("GLOSTAT_SEC_USER_AGENT", raising=False)
    with pytest.raises(ConfigError, match="INV-GS-038"):
        SecEdgarClient()


def test_inv_gs_038_env_default_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLOSTAT_SEC_USER_AGENT", "GLOSTAT research@example.com")
    with pytest.raises(ConfigError, match="INV-GS-038"):
        SecEdgarClient()


def test_inv_gs_038_explicit_agent_accepted() -> None:
    c = SecEdgarClient(user_agent=_VALID_AGENT)
    assert c.user_agent == _VALID_AGENT


def test_inv_gs_038_user_agent_header_sent() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["user_agent"] = request.headers.get("user-agent", "")
        return httpx.Response(200, json={"AAPL_ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(
        transport=transport,
        headers={"User-Agent": _VALID_AGENT},
    )
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http)
    asyncio.run(c._get_json("https://data.sec.gov/test"))
    assert captured["user_agent"] == _VALID_AGENT
    asyncio.run(c.aclose())


# ── INV-GS-038: 10 req/sec rate limit ──────────────────────────────────────


def test_inv_gs_038_throttle_constants() -> None:
    assert _RATE_LIMIT_PER_SEC == 10
    assert abs(_MIN_INTERVAL_S - 0.1) < 1e-9


def test_inv_gs_038_throttle_serializes_calls() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"x": 1})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http)

    async def run() -> None:
        t0 = time.monotonic()
        await asyncio.gather(*(c._get_json("https://data.sec.gov/x") for _ in range(11)))
        elapsed = time.monotonic() - t0
        # 11 calls @ 10 req/sec → at least 1 throttle event, total wall ≥ 0.1s
        assert c.throttle.acquire_count == 11
        assert c.throttle.throttled_count >= 1
        assert elapsed >= 0.09
        await c.aclose()

    asyncio.run(run())


# ── ticker → CIK lookup (mocked company_tickers.json) ──────────────────────


def _fake_tickers_payload() -> dict[str, Any]:
    return {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
    }


def test_ticker_to_cik_uses_cache(tmp_path: Path) -> None:
    cache = tmp_path / "sec_tickers.json"
    cache.write_text(json.dumps({"by_ticker": {"AAPL": "0000320193"}}))

    def handler(request: httpx.Request) -> httpx.Response:
        # Cache hit means this should never fire.
        raise AssertionError("network should not be hit when cache exists")

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http, ticker_cache=cache)

    cik = asyncio.run(c.ticker_to_cik("aapl"))
    assert cik == "0000320193"
    asyncio.run(c.aclose())


def test_ticker_to_cik_fetches_and_writes_cache(tmp_path: Path) -> None:
    cache = tmp_path / "sec_tickers.json"

    def handler(request: httpx.Request) -> httpx.Response:
        if "company_tickers.json" in str(request.url):
            return httpx.Response(200, json=_fake_tickers_payload())
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http, ticker_cache=cache)

    cik_aapl = asyncio.run(c.ticker_to_cik("AAPL"))
    cik_msft = asyncio.run(c.ticker_to_cik("MSFT"))
    assert cik_aapl == "0000320193"
    assert cik_msft == "0000789019"
    assert cache.exists()
    written = json.loads(cache.read_text())
    assert written["by_ticker"]["AAPL"] == "0000320193"
    asyncio.run(c.aclose())


def test_ticker_to_cik_unknown_raises(tmp_path: Path) -> None:
    cache = tmp_path / "sec_tickers.json"
    cache.write_text(json.dumps({"by_ticker": {"AAPL": "0000320193"}}))
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http, ticker_cache=cache)
    with pytest.raises(KeyError, match="ZZZZ"):
        asyncio.run(c.ticker_to_cik("ZZZZ"))
    asyncio.run(c.aclose())


# ── get_filings now implemented (Sprint 1 PR #3) ───────────────────────────


def test_get_filings_returns_empty_on_http_error() -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http)
    result = asyncio.run(c.get_filings("0000320193", form_types=("10-K",)))
    assert result == ()
    asyncio.run(c.aclose())


def test_get_13f_holdings_none_when_no_filings() -> None:
    # No filings → no holdings (graceful empty return).
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http)
    assert asyncio.run(c.get_13f_holdings("0000320193")) is None
    asyncio.run(c.aclose())


# ── Sprint 4 PR #3: 429 retry + Retry-After honor ─────────────────────────


def test_retry_on_429_with_retry_after_header(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []

    async def fake_sleep(d: float) -> None:
        sleeps.append(d)

    monkeypatch.setattr("glostat.data.retry._default_sleep", fake_sleep)

    counter = {"calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        if counter["calls"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0.25"}, json={"err": "rate"})
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http)

    result = asyncio.run(c._get_json("https://data.sec.gov/test"))
    assert result == {"ok": True}
    assert counter["calls"] == 2
    # Retry-After "0.25" must override the default 1.0s base delay.
    assert sleeps == [0.25]
    assert c.retry_stats.retry_count == 1
    assert c.retry_stats.retry_429_count == 1
    asyncio.run(c.aclose())


def test_retry_max_3_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr("glostat.data.retry._default_sleep", _no_sleep)

    counter = {"calls": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return httpx.Response(429, json={"err": "rate"})

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(c._get_json("https://data.sec.gov/test"))
    # 1 initial + 3 retries.
    assert counter["calls"] == 4
    assert c.retry_stats.retry_count == 3
    asyncio.run(c.aclose())


def test_no_retry_on_403(monkeypatch: pytest.MonkeyPatch) -> None:
    # 403 (User-Agent rejection) is permanent — never retry.
    async def _no_sleep(_d: float) -> None:
        return None

    monkeypatch.setattr("glostat.data.retry._default_sleep", _no_sleep)

    counter = {"calls": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        counter["calls"] += 1
        return httpx.Response(403, text="forbidden")

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http)

    with pytest.raises(httpx.HTTPStatusError):
        asyncio.run(c._get_json("https://data.sec.gov/test"))
    assert counter["calls"] == 1
    assert c.retry_stats.retry_count == 0
    asyncio.run(c.aclose())
