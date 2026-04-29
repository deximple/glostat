from __future__ import annotations

import os
from datetime import UTC, date, datetime, timedelta

import pytest

from glostat.data.ecos_client import EcosClient, is_ecos_configured
from glostat.experts.e_macro_kr import EMacroKrExpert

# Live ECOS smoke tests. Skip unless NETWORK_TESTS=1 AND GLOSTAT_ECOS_API_KEY set.
# Pinned to KOSPI 200 megacap (005930 삼성전자) so the KR expert runs end-to-end
# with real macro data. Cost: ~4 ECOS calls per smoke run (cached locally
# after the first hit).


def _ecos_skip_reason() -> str | None:
    if os.environ.get("NETWORK_TESTS") != "1":
        return "network smoke — set NETWORK_TESTS=1"
    if not is_ecos_configured():
        return "ECOS API key missing — export GLOSTAT_ECOS_API_KEY"
    return None


_SKIP_REASON = _ecos_skip_reason()


@pytest.mark.network
@pytest.mark.skipif(_SKIP_REASON is not None, reason=str(_SKIP_REASON))
@pytest.mark.asyncio
async def test_live_ecos_base_rate_returns_recent_observations() -> None:
    client = EcosClient()
    try:
        end = date.today().replace(day=1) - timedelta(days=1)
        start = end - timedelta(days=120)
        series = await client.get_base_rate(start, end)
    finally:
        await client.aclose()
    assert series.n_valid() > 0
    latest = series.latest()
    assert latest is not None
    # BoK base rate is sane (0% .. 10% in any modern era).
    assert latest.value is not None
    assert 0.0 <= latest.value <= 10.0


@pytest.mark.network
@pytest.mark.skipif(_SKIP_REASON is not None, reason=str(_SKIP_REASON))
@pytest.mark.asyncio
async def test_live_ecos_krw_usd_returns_recent_observations() -> None:
    client = EcosClient()
    try:
        end = date.today() - timedelta(days=2)
        start = end - timedelta(days=14)
        series = await client.get_krw_usd(start, end)
    finally:
        await client.aclose()
    assert series.n_valid() > 0
    latest = series.latest()
    assert latest is not None and latest.value is not None
    # KRW/USD reasonable band (sanity check; not a target).
    assert 800.0 < latest.value < 2500.0


@pytest.mark.network
@pytest.mark.skipif(_SKIP_REASON is not None, reason=str(_SKIP_REASON))
@pytest.mark.asyncio
async def test_live_macro_kr_signal_for_samsung() -> None:
    expert = EMacroKrExpert.from_env()
    assert expert is not None
    try:
        signal = await expert.compute(
            "005930", datetime.now(tz=UTC),
        )
    finally:
        if expert._ecos is not None:  # type: ignore[attr-defined]
            await expert._ecos.aclose()  # type: ignore[attr-defined]
    assert signal.expert_name == "E_MACRO_KR"
    assert signal.ticker == "005930"
    assert signal.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert -3.0 <= signal.net_score <= 3.0
    # Basis must reference the macro components for downstream auditability.
    assert "BoK" in signal.basis
    assert "KRW/USD" in signal.basis
