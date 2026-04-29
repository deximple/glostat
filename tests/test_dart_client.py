from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

import httpx
import pytest

from glostat.data.dart_client import (
    DartApiError,
    DartApiKeyMissingError,
    DartClient,
    _build_executive_txn,
    _market_from_code,
    _parse_corp_code_zip,
    _parse_financial_items,
    _resolve_api_key,
    is_dart_configured,
)
from glostat.data.dart_types import (
    CorpCodeEntry,
    DartFinancialStatements,
    _parse_number,
)

# ── pure helpers ────────────────────────────────────────────────────────


def test_resolve_api_key_uses_override() -> None:
    assert _resolve_api_key(override="abc123") == "abc123"


def test_resolve_api_key_falls_back_to_env(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_DART_API_KEY", "envkey")
    assert _resolve_api_key() == "envkey"


def test_resolve_api_key_raises_when_missing(monkeypatch) -> None:
    monkeypatch.delenv("GLOSTAT_DART_API_KEY", raising=False)
    with pytest.raises(DartApiKeyMissingError):
        _resolve_api_key()


def test_resolve_api_key_raises_on_empty_override(monkeypatch) -> None:
    monkeypatch.delenv("GLOSTAT_DART_API_KEY", raising=False)
    with pytest.raises(DartApiKeyMissingError):
        _resolve_api_key(override="   ")


def test_is_dart_configured_with_key(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_DART_API_KEY", "x")
    assert is_dart_configured() is True


def test_is_dart_configured_without_key(monkeypatch) -> None:
    monkeypatch.delenv("GLOSTAT_DART_API_KEY", raising=False)
    assert is_dart_configured() is False


def test_market_from_code_kospi() -> None:
    assert _market_from_code("Y") == "KOSPI"


def test_market_from_code_kosdaq() -> None:
    assert _market_from_code("K") == "KOSDAQ"


def test_market_from_code_unknown_returns_other() -> None:
    assert _market_from_code("Z") == "OTHER"


def test_parse_number_handles_dash() -> None:
    assert _parse_number("-") is None


def test_parse_number_handles_commas() -> None:
    assert _parse_number("1,234,567") == 1234567.0


def test_parse_number_handles_empty() -> None:
    assert _parse_number("") is None
    assert _parse_number(None) is None


# ── corp code zip parser ────────────────────────────────────────────────


def _make_corp_code_zip(entries: list[CorpCodeEntry]) -> bytes:
    root = ET.Element("result")
    for e in entries:
        node = ET.SubElement(root, "list")
        ET.SubElement(node, "corp_code").text = e.corp_code
        ET.SubElement(node, "corp_name").text = e.corp_name
        ET.SubElement(node, "stock_code").text = e.stock_code
        ET.SubElement(node, "modify_date").text = e.modify_date
    xml_bytes = ET.tostring(root, encoding="utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("CORPCODE.xml", xml_bytes)
    return buf.getvalue()


def test_parse_corp_code_zip_extracts_entries() -> None:
    zipped = _make_corp_code_zip([
        CorpCodeEntry(corp_code="00126380", corp_name="삼성전자",
                       stock_code="005930", modify_date="20240101"),
        CorpCodeEntry(corp_code="00164742", corp_name="SK이노베이션",
                       stock_code="096770", modify_date="20240105"),
    ])
    parsed = _parse_corp_code_zip(zipped)
    assert len(parsed) == 2
    assert parsed[0].stock_code == "005930"
    assert parsed[1].corp_code == "00164742"


def test_parse_corp_code_zip_handles_bad_zip() -> None:
    parsed = _parse_corp_code_zip(b"not a zip")
    assert parsed == []


# ── financial items parser ──────────────────────────────────────────────


def test_parse_financial_items_basic() -> None:
    rows = [
        {
            "account_id": "ifrs-full_Revenue", "account_nm": "수익",
            "fs_div": "CFS", "sj_div": "IS",
            "thstrm_amount": "279,604,799,000,000",
            "frmtrm_amount": "258,935,494,000,000",
            "bfefrmtrm_amount": "0",
            "thstrm_nm": "당기",
        },
    ]
    items = _parse_financial_items(rows)
    assert len(items) == 1
    it = items[0]
    assert it.account_id == "ifrs-full_Revenue"
    assert it.thstrm_value == pytest.approx(279_604_799_000_000.0)
    assert it.frmtrm_value == pytest.approx(258_935_494_000_000.0)


def test_financial_statements_find_by_id() -> None:
    items = _parse_financial_items([
        {"account_id": "ifrs-full_Revenue", "account_nm": "매출액",
         "fs_div": "CFS", "sj_div": "IS", "thstrm_amount": "100",
         "frmtrm_amount": "90", "bfefrmtrm_amount": "0",
         "thstrm_nm": "당기"},
    ])
    statements = DartFinancialStatements(
        corp_code="x", bsns_year="2024", reprt_code="11011",
        items=tuple(items),
    )
    assert statements.find("Revenue") is not None
    assert statements.find_by_name("매출액") is not None
    assert statements.find("notpresent") is None


# ── executive transaction builder ───────────────────────────────────────


def test_build_executive_txn_classifies_buy_from_irds_cnt() -> None:
    row = {
        "repror": "홍길동", "isu_exctv_rgist_at": "Y",
        "isu_exctv_ofcps": "이사",
        "isu_main_shrholdr": "본인",
        "sp_stock_lmp_cnt": "10000", "sp_stock_lmp_irds_cnt": "1000",
        "sp_stock_lmp_irds_rate": "0.10",
        "bsis_dt": "20260101", "rcept_dt": "20260103",
        "trd_kind": "장내매수",
    }
    txn = _build_executive_txn("00126380", row)
    assert txn is not None
    assert txn.is_buy is True
    assert txn.is_sell is False


def test_build_executive_txn_classifies_sell_from_negative_irds() -> None:
    row = {
        "repror": "임원", "sp_stock_lmp_irds_cnt": "-5000",
        "trd_kind": "장내매도", "bsis_dt": "20260201", "rcept_dt": "20260203",
    }
    txn = _build_executive_txn("xx", row)
    assert txn is not None
    assert txn.is_sell is True
    assert txn.is_buy is False


def test_build_executive_txn_handles_korean_kind() -> None:
    row = {
        "repror": "Lee", "sp_stock_lmp_irds_cnt": "0",
        "trd_kind": "취득(증여)", "bsis_dt": "20250901",
    }
    txn = _build_executive_txn("yy", row)
    assert txn is not None
    assert txn.is_buy is True


# ── DartClient end-to-end with httpx mock ───────────────────────────────


@pytest.mark.asyncio
async def test_get_company_overview_mocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_DART_API_KEY", "k1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "status": "000", "message": "OK",
            "corp_code": "00126380", "corp_name": "삼성전자",
            "corp_name_eng": "Samsung Electronics",
            "stock_code": "005930", "ceo_nm": "이재용",
            "est_dt": "19690113", "induty_code": "264",
            "corp_cls": "Y",
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw_client:
        client = DartClient(client=raw_client, corp_code_cache=tmp_path / "cc.parquet")
        ov = await client.get_company_overview("00126380")
    assert ov.corp_name == "삼성전자"
    assert ov.market == "KOSPI"


@pytest.mark.asyncio
async def test_get_financial_statements_mocked(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_DART_API_KEY", "k1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "status": "000", "message": "OK",
            "list": [
                {"account_id": "ifrs-full_Revenue", "account_nm": "수익",
                 "fs_div": "CFS", "sj_div": "IS",
                 "thstrm_amount": "300000000000",
                 "frmtrm_amount": "250000000000",
                 "bfefrmtrm_amount": "200000000000",
                 "thstrm_nm": "2024"},
            ],
        })

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw_client:
        client = DartClient(client=raw_client, corp_code_cache=tmp_path / "cc.parquet")
        stmts = await client.get_financial_statements("00126380", year=2024)
    assert len(stmts.items) == 1
    assert stmts.items[0].thstrm_value == pytest.approx(3.0e11)


@pytest.mark.asyncio
async def test_dart_api_error_when_status_non_zero(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_DART_API_KEY", "k1")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "020", "message": "API 호출 한도 초과"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw_client:
        client = DartClient(client=raw_client, corp_code_cache=tmp_path / "cc.parquet")
        with pytest.raises(DartApiError) as exc:
            await client.get_financial_statements("xx", year=2024)
    assert "020" in str(exc.value)


@pytest.mark.asyncio
async def test_get_corp_code_uses_cache(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_DART_API_KEY", "k1")
    # Pre-seed the cache with one entry so we never need to fetch.
    import pyarrow as pa
    import pyarrow.parquet as pq

    cache_path = tmp_path / "cc.parquet"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist([{
        "corp_code": "00126380", "corp_name": "삼성전자",
        "stock_code": "005930", "modify_date": "20240101",
    }])
    pq.write_table(table, cache_path, compression="zstd")

    # Transport that should NOT be called (we want cache to win).
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(500, text="should not be called")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as raw_client:
        client = DartClient(client=raw_client, corp_code_cache=cache_path)
        corp_code = await client.get_corp_code("005930")
    assert corp_code == "00126380"
    assert calls == []  # cache hit, no http


@pytest.mark.asyncio
async def test_get_corp_code_rejects_non_6digit(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_DART_API_KEY", "k1")
    async with httpx.AsyncClient() as raw_client:
        client = DartClient(client=raw_client, corp_code_cache=tmp_path / "cc.parquet")
        with pytest.raises(DartApiError):
            await client.get_corp_code("AAPL")
