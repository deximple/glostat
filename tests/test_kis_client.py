from __future__ import annotations

import httpx
import pytest

from glostat.data.kis_client import (
    KisApiError,
    KisClient,
    KisCredentialsMissingError,
    _normalize_code,
    _parse_date,
    _parse_signed,
    _resolve_credentials,
    is_kis_configured,
)

# ── pure helpers ────────────────────────────────────────────────────────


def test_resolve_credentials_uses_overrides() -> None:
    key, secret = _resolve_credentials(app_key="k1", app_secret="s1")
    assert key == "k1"
    assert secret == "s1"


def test_resolve_credentials_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "ek")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "es")
    key, secret = _resolve_credentials()
    assert (key, secret) == ("ek", "es")


def test_resolve_credentials_raises_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("GLOSTAT_KIS_APP_KEY", raising=False)
    monkeypatch.delenv("GLOSTAT_KIS_APP_SECRET", raising=False)
    with pytest.raises(KisCredentialsMissingError):
        _resolve_credentials()


def test_resolve_credentials_raises_on_blank(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "  ")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "x")
    with pytest.raises(KisCredentialsMissingError):
        _resolve_credentials()


def test_is_kis_configured(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "k")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "s")
    assert is_kis_configured() is True
    monkeypatch.delenv("GLOSTAT_KIS_APP_KEY", raising=False)
    assert is_kis_configured() is False


def test_normalize_code_strips_suffix() -> None:
    assert _normalize_code("005930.KS") == "005930"
    assert _normalize_code("005930") == "005930"
    assert _normalize_code("005930.KQ") == "005930"


def test_normalize_code_rejects_non_digits() -> None:
    with pytest.raises(KisApiError):
        _normalize_code("AAPL")
    with pytest.raises(KisApiError):
        _normalize_code("1234")


def test_parse_signed_handles_commas_and_signs() -> None:
    assert _parse_signed("1,234") == 1234.0
    assert _parse_signed("+999") == 999.0
    assert _parse_signed("-500") == -500.0
    assert _parse_signed("") == 0.0
    assert _parse_signed(None) == 0.0
    assert _parse_signed("garbage") == 0.0


def test_parse_date_yyyymmdd() -> None:
    assert _parse_date("20260415") is not None
    assert _parse_date("2026-04-15") is None  # only YYYYMMDD accepted
    assert _parse_date("garbage") is None
    assert _parse_date("") is None


# ── KisClient with httpx mock ───────────────────────────────────────────


def _token_handler_factory():
    calls = {"token": 0, "investor": 0, "daily": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/tokenP"):
            calls["token"] += 1
            return httpx.Response(200, json={
                "access_token": "tok-123",
                "token_type": "Bearer",
                "expires_in": 3600,
            })
        if "inquire-investor" in request.url.path:
            calls["investor"] += 1
            return httpx.Response(200, json={
                "rt_cd": "0",
                "msg1": "OK",
                "output": [{
                    "frgn_ntby_qty": "1,500",
                    "orgn_ntby_qty": "-300",
                    "prsn_ntby_qty": "500",
                    "pgm_ntby_qty": "200",
                }],
            })
        if "inquire-daily-trade" in request.url.path:
            calls["daily"] += 1
            return httpx.Response(200, json={
                "rt_cd": "0",
                "msg1": "OK",
                "output": [{
                    "stck_bsop_date": "20260415",
                    "frgn_ntby_tr_pbmn": "1500000000",
                    "orgn_ntby_tr_pbmn": "-300000000",
                    "prsn_ntby_tr_pbmn": "500000000",
                }],
            })
        return httpx.Response(404, json={"rt_cd": "1", "msg1": "not found"})

    return handler, calls


@pytest.mark.asyncio
async def test_get_intraday_flows_returns_parsed(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "k1")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "s1")
    handler, calls = _token_handler_factory()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KisClient(client=raw)
        flow = await client.get_intraday_flows("005930.KS")
    assert flow.code == "005930"
    assert flow.foreign_net == 1500.0
    assert flow.institutional_net == -300.0
    assert flow.individual_net == 500.0
    assert flow.pgm_net == 200.0
    assert flow.source == "kis"
    assert calls["token"] == 1
    assert calls["investor"] == 1


@pytest.mark.asyncio
async def test_get_daily_summary_returns_parsed(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "k1")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "s1")
    handler, _ = _token_handler_factory()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KisClient(client=raw)
        summary = await client.get_daily_summary("005930")
    assert summary.code == "005930"
    assert summary.foreign_net_won == 1500000000.0
    assert summary.institutional_net_won == -300000000.0


@pytest.mark.asyncio
async def test_token_cached_across_calls(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "k1")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "s1")
    handler, calls = _token_handler_factory()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KisClient(client=raw)
        await client.get_intraday_flows("005930")
        await client.get_intraday_flows("005930")
    # 1 token request, 2 investor requests
    assert calls["token"] == 1
    assert calls["investor"] == 2


@pytest.mark.asyncio
async def test_kis_business_error_raises(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "k1")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "s1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/tokenP"):
            return httpx.Response(200, json={
                "access_token": "tok", "token_type": "Bearer", "expires_in": 3600,
            })
        return httpx.Response(200, json={
            "rt_cd": "1", "msg1": "한도 초과", "msg_cd": "EGW00100",
            "output": [],
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KisClient(client=raw)
        with pytest.raises(KisApiError) as exc:
            await client.get_intraday_flows("005930")
    assert "rt_cd=1" in str(exc.value)


@pytest.mark.asyncio
async def test_kis_http_error_raises(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "k1")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "s1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream gone")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KisClient(client=raw)
        with pytest.raises(KisApiError):
            await client.get_intraday_flows("005930")


@pytest.mark.asyncio
async def test_kis_token_missing_field_raises(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "k1")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "s1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "Bearer"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KisClient(client=raw)
        with pytest.raises(KisApiError):
            await client.get_intraday_flows("005930")


@pytest.mark.asyncio
async def test_kis_snapshot_broker_recorded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "k1")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "s1")
    from glostat.data.snapshot_broker import SnapshotBroker  # noqa: PLC0415

    broker = SnapshotBroker(root=tmp_path / "snaps")
    try:
        handler, _ = _token_handler_factory()
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as raw:
            client = KisClient(client=raw, snapshot_broker=broker)
            await client.get_intraday_flows("005930")
        assert client.last_snapshot_id is not None
        assert len(client.last_snapshot_id) == 64
    finally:
        broker.close()


@pytest.mark.asyncio
async def test_kis_normalize_rejects_invalid_ticker(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_KIS_APP_KEY", "k1")
    monkeypatch.setenv("GLOSTAT_KIS_APP_SECRET", "s1")
    handler, _ = _token_handler_factory()
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KisClient(client=raw)
        with pytest.raises(KisApiError):
            await client.get_intraday_flows("AAPL")


def test_kis_credentials_missing_message() -> None:
    err = KisCredentialsMissingError.make()
    assert "GLOSTAT_KIS_APP_KEY" in str(err)
    assert "docs/KIS_API_SETUP.md" in str(err)
