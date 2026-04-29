from __future__ import annotations

import asyncio

import pytest

from glostat.core.errors import ConfigError
from glostat.data.bigdata_client import BigdataBudget, BigdataClient
from glostat.data.data_router import DataRouter
from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.yfinance_client import YFinanceClient

# v0.6 INV-GS-036..040 — Free Stack Data Plane invariants.
# Source: docs/ssot/PLAN_v0.6.md §4.


# ── INV-GS-036: Bigdata blocked in MVP ────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_036_bigdata_blocked_in_mvp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    budget = BigdataBudget(monthly_call_cap=100, monthly_smart_cap=30)
    client = BigdataClient(budget=budget)
    with pytest.raises(ConfigError, match="INV-GS-036"):
        asyncio.run(client.find_companies(query="AAPL"))
    # WHY: phase guard must fire BEFORE budget reservation
    assert budget.used_calls == 0


@pytest.mark.invariant
def test_inv_gs_036_phase_2_unlocks_bigdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_2")
    budget = BigdataBudget(monthly_call_cap=10, monthly_smart_cap=3)
    client = BigdataClient(budget=budget)
    # Reaches the stub → NotImplementedError, not ConfigError.
    with pytest.raises(NotImplementedError, match="MCP wired in S1"):
        asyncio.run(client.bigdata_search(request={"q": "Apple earnings"}))


# ── INV-GS-037: yfinance throttle ──────────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_037_yfinance_rate_limit() -> None:
    # Sprint 4 PR #3 relaxed throttle from 5 → 8 req/sec; bump the test to 9
    # concurrent acquires so the assertion still triggers the throttle path.
    c = YFinanceClient()

    async def run() -> None:
        async def grab() -> None:
            await c.throttle.acquire()
            try:
                await asyncio.sleep(0)
            finally:
                c.throttle.release()

        await asyncio.gather(*(grab() for _ in range(9)))

    asyncio.run(run())
    assert c.throttle.acquire_count == 9
    assert c.throttle.throttled_count >= 1


# ── INV-GS-038: SEC EDGAR User-Agent ───────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_038_sec_user_agent_default_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Monkeypatch the env so the default ("example.com") is reached even when
    # the suite is run with a real GLOSTAT_SEC_USER_AGENT for live-mode tests.
    monkeypatch.delenv("GLOSTAT_SEC_USER_AGENT", raising=False)
    with pytest.raises(ConfigError, match="INV-GS-038"):
        SecEdgarClient()


@pytest.mark.invariant
def test_inv_gs_038_sec_user_agent_env_default_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLOSTAT_SEC_USER_AGENT", "GLOSTAT research@example.com")
    with pytest.raises(ConfigError, match="INV-GS-038"):
        SecEdgarClient()


@pytest.mark.invariant
def test_inv_gs_038_sec_user_agent_explicit_accepted() -> None:
    c = SecEdgarClient(user_agent="GLOSTAT test@gloss.dev")
    assert "example.com" not in c.user_agent
    asyncio.run(c.aclose())


# ── INV-GS-039: data_router phase gating ───────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_039_data_router_phase_blocks_bigdata_in_mvp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "mvp")
    r = DataRouter()
    r.register_client("yfinance", object())
    r.register_client("sec_edgar", object())
    r.register_client("bigdata", object())
    # Free routes work fine in MVP.
    _client, method = r.route("E_FUNDAMENTAL", "ohlcv")
    assert method == "get_ohlcv"
    # Bigdata-only routes are blocked.
    with pytest.raises(ConfigError, match=r"MVP|INV-GS-036|INV-GS-039"):
        r.route("E_NARRATIVE", "search")


# ── INV-GS-040: Phase 2/3 consent required ─────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_040_bigdata_consent_required_in_phase_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_2")
    r = DataRouter()
    r.register_client("yfinance", object())
    r.register_client("bigdata", object())
    # Without consent → ConfigError citing INV-GS-040.
    with pytest.raises(ConfigError, match="INV-GS-040"):
        r.route("E_NARRATIVE", "search")
    # After consent → resolves cleanly.
    r.grant_consent("phase_2")
    _client, method = r.route("E_NARRATIVE", "search")
    assert method == "bigdata_search"
