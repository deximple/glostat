from __future__ import annotations

from datetime import date

import httpx
import pytest

from glostat.data.krx_short_client import (
    KrxShortBalanceBar,
    KrxShortClient,
    KrxShortError,
    KrxShortVolumeBar,
    _normalize_code,
    _parse_krx_date,
    _parse_signed,
    _row_to_balance,
    _row_to_volume,
)


def test_normalize_code_strips_suffix() -> None:
    assert _normalize_code("005930.KS") == "005930"
    assert _normalize_code("005930.KQ") == "005930"
    assert _normalize_code("005930") == "005930"


def test_normalize_code_rejects_non_digits() -> None:
    with pytest.raises(KrxShortError):
        _normalize_code("AAPL")


def test_parse_krx_date_handles_slashes() -> None:
    assert _parse_krx_date("2026/04/15") == date(2026, 4, 15)
    assert _parse_krx_date("2026-04-15") == date(2026, 4, 15)
    assert _parse_krx_date("20260415") == date(2026, 4, 15)


def test_parse_krx_date_returns_none_on_garbage() -> None:
    assert _parse_krx_date("garbage") is None
    assert _parse_krx_date("") is None
    assert _parse_krx_date("2026/02/30") is None


def test_parse_signed_strips_commas() -> None:
    assert _parse_signed("1,234,567") == 1234567.0
    assert _parse_signed("+999") == 999.0
    assert _parse_signed("") == 0.0
    assert _parse_signed("-") == 0.0


def test_row_to_balance_parses_canonical_keys() -> None:
    row = {
        "TRD_DD": "2026/04/15", "BAL_QTY": "1,500,000", "BAL_AMT": "75000000000",
        "LIST_SHRS": "100,000,000", "BAL_RTO": "1.50",
    }
    bar = _row_to_balance(row, "005930")
    assert bar is not None
    assert bar.bar_date == date(2026, 4, 15)
    assert bar.short_balance_qty == 1500000.0
    assert bar.short_balance_ratio == 1.50


def test_row_to_balance_returns_none_on_bad_date() -> None:
    row = {"TRD_DD": "garbage", "BAL_QTY": "1000"}
    assert _row_to_balance(row, "005930") is None


def test_row_to_volume_parses_canonical_keys() -> None:
    row = {
        "TRD_DD": "2026/04/15", "CVSRTSELL_TRDVOL": "55,771",
        "CVSRTSELL_TRDVAL": "5500000000", "ACC_TRDVOL": "500,000",
        "TRDVOL_WT": "11.15",
    }
    bar = _row_to_volume(row, "005930")
    assert bar is not None
    assert bar.short_volume == 55771.0
    assert bar.short_ratio_pct == 11.15


# ── KrxShortClient with httpx mock ──────────────────────────────────────


def _balance_response() -> dict:
    return {
        "output": [
            {"TRD_DD": "2026/04/14", "BAL_QTY": "1,000,000", "BAL_AMT": "0",
             "LIST_SHRS": "100,000,000", "BAL_RTO": "1.00"},
            {"TRD_DD": "2026/04/15", "BAL_QTY": "1,500,000", "BAL_AMT": "0",
             "LIST_SHRS": "100,000,000", "BAL_RTO": "1.50"},
        ]
    }


def _volume_response() -> dict:
    return {
        "output": [
            {"TRD_DD": "2026/04/15", "CVSRTSELL_TRDVOL": "100,000",
             "CVSRTSELL_TRDVAL": "5000000000", "ACC_TRDVOL": "1,000,000",
             "TRDVOL_WT": "10.0"},
        ]
    }


@pytest.mark.asyncio
async def test_get_short_balance_returns_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_balance_response())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KrxShortClient(client=raw)
        bars = await client.get_short_balance("005930", days_back=10,
                                              end=date(2026, 4, 15))
    assert len(bars) == 2
    assert isinstance(bars[0], KrxShortBalanceBar)
    assert bars[-1].bar_date == date(2026, 4, 15)
    assert bars[-1].short_balance_qty == 1_500_000.0


@pytest.mark.asyncio
async def test_get_short_volume_returns_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_volume_response())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KrxShortClient(client=raw)
        bars = await client.get_short_volume("005930", days_back=5,
                                             end=date(2026, 4, 15))
    assert len(bars) == 1
    assert isinstance(bars[0], KrxShortVolumeBar)
    assert bars[0].short_volume == 100000.0
    assert bars[0].short_ratio_pct == 10.0


@pytest.mark.asyncio
async def test_krx_http_error_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream gone")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KrxShortClient(client=raw)
        with pytest.raises(KrxShortError):
            await client.get_short_balance("005930", days_back=5)


@pytest.mark.asyncio
async def test_krx_non_json_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KrxShortClient(client=raw)
        with pytest.raises(KrxShortError):
            await client.get_short_volume("005930", days_back=5)


@pytest.mark.asyncio
async def test_krx_empty_payload_returns_empty() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"output": []})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KrxShortClient(client=raw)
        bars = await client.get_short_balance("005930", days_back=5)
    assert bars == ()


@pytest.mark.asyncio
async def test_krx_handles_alt_block_key() -> None:
    # KRX sometimes returns OutBlock_1 instead of output.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"OutBlock_1": [
            {"TRD_DD": "20260415", "BAL_QTY": "100", "BAL_AMT": "0",
             "LIST_SHRS": "1000", "BAL_RTO": "10"},
        ]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KrxShortClient(client=raw)
        bars = await client.get_short_balance("005930", days_back=5)
    assert len(bars) == 1


@pytest.mark.asyncio
async def test_krx_snapshot_broker_recorded(tmp_path) -> None:
    from glostat.data.snapshot_broker import SnapshotBroker  # noqa: PLC0415

    broker = SnapshotBroker(root=tmp_path / "snaps")
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_balance_response())

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as raw:
            client = KrxShortClient(client=raw, snapshot_broker=broker)
            await client.get_short_balance("005930", days_back=5)
        assert client.last_snapshot_id is not None
        assert len(client.last_snapshot_id) == 64
    finally:
        broker.close()


@pytest.mark.asyncio
async def test_krx_invalid_ticker_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_balance_response())

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = KrxShortClient(client=raw)
        with pytest.raises(KrxShortError):
            await client.get_short_balance("AAPL", days_back=5)
