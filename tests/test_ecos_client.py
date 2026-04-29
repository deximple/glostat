from __future__ import annotations

from datetime import date

import httpx
import pytest

from glostat.data.ecos_client import (
    EcosApiError,
    EcosApiKeyMissingError,
    EcosClient,
    _fmt_period,
    _resolve_api_key,
    _row_to_observation,
    is_ecos_configured,
)
from glostat.data.ecos_types import EcosObservation, EcosSeries, _parse_value

# ── pure helpers ────────────────────────────────────────────────────────


def test_resolve_api_key_uses_override() -> None:
    assert _resolve_api_key(override="abc123") == "abc123"


def test_resolve_api_key_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "envkey")
    assert _resolve_api_key() == "envkey"


def test_resolve_api_key_raises_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("GLOSTAT_ECOS_API_KEY", raising=False)
    with pytest.raises(EcosApiKeyMissingError):
        _resolve_api_key()


def test_resolve_api_key_raises_on_blank_override(monkeypatch) -> None:
    monkeypatch.delenv("GLOSTAT_ECOS_API_KEY", raising=False)
    with pytest.raises(EcosApiKeyMissingError):
        _resolve_api_key(override="   ")


def test_is_ecos_configured(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "x")
    assert is_ecos_configured() is True
    monkeypatch.delenv("GLOSTAT_ECOS_API_KEY", raising=False)
    assert is_ecos_configured() is False


def test_parse_value_handles_dash_and_blank() -> None:
    assert _parse_value("-") is None
    assert _parse_value("") is None
    assert _parse_value(None) is None
    assert _parse_value("nan") is None


def test_parse_value_handles_commas_and_floats() -> None:
    assert _parse_value("1,234.5") == 1234.5
    assert _parse_value("2.75") == 2.75


def test_fmt_period_monthly_default() -> None:
    assert _fmt_period(date(2026, 3, 15), "M") == "202603"


def test_fmt_period_daily() -> None:
    assert _fmt_period(date(2026, 3, 15), "D") == "20260315"


def test_fmt_period_quarterly_and_annual() -> None:
    assert _fmt_period(date(2026, 4, 1), "Q") == "2026Q2"
    assert _fmt_period(date(2026, 6, 1), "A") == "2026"


# ── EcosObservation / EcosSeries ────────────────────────────────────────


def test_observation_period_date_monthly() -> None:
    obs = EcosObservation(stat_code="x", item_code="y", period="202603", value=1.0)
    assert obs.period_date == date(2026, 3, 1)


def test_observation_period_date_daily() -> None:
    obs = EcosObservation(stat_code="x", item_code="y", period="20260315", value=1.0)
    assert obs.period_date == date(2026, 3, 15)


def test_observation_period_date_garbage_returns_none() -> None:
    obs = EcosObservation(stat_code="x", item_code="y", period="garbage", value=1.0)
    assert obs.period_date is None


def test_series_latest_and_n_valid() -> None:
    series = EcosSeries(
        stat_code="722Y001", item_code="0101000", cycle="M",
        observations=(
            EcosObservation("722Y001", "0101000", "202601", 3.00),
            EcosObservation("722Y001", "0101000", "202602", 2.75),
            EcosObservation("722Y001", "0101000", "202603", None),
        ),
    )
    assert series.n_valid() == 2
    assert series.latest() is not None
    assert series.latest().period == "202603"
    assert series.values() == (3.00, 2.75)


# ── _row_to_observation ─────────────────────────────────────────────────


def test_row_to_observation_basic() -> None:
    row = {"STAT_CODE": "722Y001", "ITEM_CODE1": "0101000",
           "TIME": "202601", "DATA_VALUE": "3.00", "UNIT_NAME": "연%"}
    obs = _row_to_observation(row, "722Y001", "0101000")
    assert obs is not None
    assert obs.value == 3.0
    assert obs.period == "202601"
    assert obs.unit == "연%"


def test_row_to_observation_dash_value() -> None:
    row = {"STAT_CODE": "x", "ITEM_CODE1": "y", "TIME": "202601",
           "DATA_VALUE": "-", "UNIT_NAME": ""}
    obs = _row_to_observation(row, "x", "y")
    assert obs is not None
    assert obs.value is None


def test_row_to_observation_missing_period_returns_none() -> None:
    row = {"STAT_CODE": "x", "ITEM_CODE1": "y", "TIME": "",
           "DATA_VALUE": "1.0"}
    assert _row_to_observation(row, "x", "y") is None


# ── EcosClient end-to-end with httpx mock ───────────────────────────────


@pytest.mark.asyncio
async def test_get_statistic_mocked(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "k1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"StatisticSearch": {
            "list_total_count": 2,
            "row": [
                {"STAT_CODE": "722Y001", "ITEM_CODE1": "0101000",
                 "TIME": "202601", "DATA_VALUE": "3.00", "UNIT_NAME": "연%"},
                {"STAT_CODE": "722Y001", "ITEM_CODE1": "0101000",
                 "TIME": "202602", "DATA_VALUE": "2.75", "UNIT_NAME": "연%"},
            ],
        }})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = EcosClient(client=raw)
        s = await client.get_statistic(
            "722Y001", "0101000", date(2026, 1, 1), date(2026, 2, 28), cycle="M",
        )
    assert s.n_valid() == 2
    assert s.values() == (3.00, 2.75)


@pytest.mark.asyncio
async def test_get_base_rate_routes_to_correct_stat(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "k1")
    seen_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_urls.append(str(request.url))
        return httpx.Response(200, json={"StatisticSearch": {"row": []}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = EcosClient(client=raw)
        await client.get_base_rate(date(2026, 1, 1), date(2026, 3, 31))
    assert "722Y001" in seen_urls[0]
    assert "0101000" in seen_urls[0]
    assert "/M/" in seen_urls[0]


@pytest.mark.asyncio
async def test_get_krw_usd_routes_correctly(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "k1")
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"StatisticSearch": {"row": []}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = EcosClient(client=raw)
        await client.get_krw_usd(date(2026, 1, 1), date(2026, 1, 31))
    assert "731Y001" in urls[0]
    assert "/D/" in urls[0]


@pytest.mark.asyncio
async def test_ecos_no_data_returns_empty_series(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "k1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"RESULT": {"CODE": "INFO-200",
                                                    "MESSAGE": "no data"}})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = EcosClient(client=raw)
        s = await client.get_base_rate(date(2025, 1, 1), date(2025, 1, 31))
    assert s.observations == ()


@pytest.mark.asyncio
async def test_ecos_invalid_key_raises(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "k1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"RESULT": {
            "CODE": "INFO-100", "MESSAGE": "인증키 오류",
        }})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = EcosClient(client=raw)
        with pytest.raises(EcosApiError) as exc:
            await client.get_base_rate(date(2026, 1, 1), date(2026, 1, 31))
    assert "INFO-100" in str(exc.value)


@pytest.mark.asyncio
async def test_ecos_http_error_raises(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "k1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream gone")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw:
        client = EcosClient(client=raw)
        with pytest.raises(EcosApiError):
            await client.get_base_rate(date(2026, 1, 1), date(2026, 1, 31))


@pytest.mark.asyncio
async def test_ecos_snapshot_broker_recorded(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "k1")
    from glostat.data.snapshot_broker import SnapshotBroker  # noqa: PLC0415

    broker = SnapshotBroker(root=tmp_path / "snaps")
    try:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"StatisticSearch": {"row": [
                {"STAT_CODE": "722Y001", "ITEM_CODE1": "0101000",
                 "TIME": "202601", "DATA_VALUE": "3.00", "UNIT_NAME": "연%"},
            ]}})

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as raw:
            client = EcosClient(client=raw, snapshot_broker=broker)
            await client.get_base_rate(date(2026, 1, 1), date(2026, 1, 31))
        assert client.last_snapshot_id is not None
        # 64-char hex from MerkleLeaf.compute.
        assert len(client.last_snapshot_id) == 64
    finally:
        broker.close()
