from __future__ import annotations

import asyncio
import os
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.sec_edgar_parsers import (
    parse_13f_infotable,
    parse_submissions_filings,
)
from glostat.data.snapshot_broker import SnapshotBroker

_VALID_AGENT = "GLOSTAT test@gloss.dev"
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


# ── parse_submissions_filings (pure function) ──────────────────────────────


def _fake_submissions_payload() -> dict[str, Any]:
    return {
        "filings": {
            "recent": {
                "accessionNumber": [
                    "0000320193-26-000020",
                    "0000320193-26-000010",
                    "0000320193-25-000080",
                    "0000320193-25-000050",
                    "0000320193-26-000005",
                ],
                "form": ["13F-HR", "13F-HR", "13F-HR", "13F-HR", "10-K"],
                "filingDate": [
                    "2026-04-15", "2026-01-15",
                    "2025-10-15", "2025-07-15",
                    "2025-11-01",
                ],
                "primaryDocument": [
                    "primary_doc.html", "primary_doc.html",
                    "primary_doc.html", "primary_doc.html",
                    "aapl-20251101.htm",
                ],
            }
        }
    }


def test_get_filings_filters_by_form_type() -> None:
    payload = _fake_submissions_payload()
    filings = parse_submissions_filings(
        "0000320193", payload, form_types=("13F",), limit=50
    )
    assert len(filings) == 4
    assert all(f.form_type.startswith("13F") for f in filings)


def test_get_filings_respects_limit() -> None:
    payload = _fake_submissions_payload()
    filings = parse_submissions_filings(
        "0000320193", payload, form_types=("13F",), limit=2
    )
    assert len(filings) == 2


def test_get_filings_includes_10k_when_requested() -> None:
    payload = _fake_submissions_payload()
    filings = parse_submissions_filings(
        "0000320193", payload, form_types=("10-K",), limit=10
    )
    assert len(filings) == 1
    assert filings[0].form_type == "10-K"


def test_get_filings_empty_when_no_matches() -> None:
    payload = _fake_submissions_payload()
    filings = parse_submissions_filings(
        "0000320193", payload, form_types=("4",), limit=10
    )
    assert filings == []


def test_get_filings_url_constructed_correctly() -> None:
    payload = _fake_submissions_payload()
    filings = parse_submissions_filings(
        "0000320193", payload, form_types=("13F",), limit=1
    )
    f = filings[0]
    assert f.primary_doc_url.startswith("https://www.sec.gov/Archives/edgar/data/320193/")
    # Accession number with dashes removed: "0000320193-26-000020" → "000032019326000020".
    assert "000032019326000020" in f.primary_doc_url
    assert f.primary_doc_url.endswith("primary_doc.html")


# ── parse_13f_infotable ────────────────────────────────────────────────────


def test_get_13f_holdings_parses_xml() -> None:
    xml = (_FIXTURES_DIR / "sample_13f_infotable.xml").read_text("utf-8")
    positions = parse_13f_infotable(xml)
    assert len(positions) == 3
    apple = next(p for p in positions if p.cusip == "037833100")
    assert apple.name == "APPLE INC"
    assert apple.shares == 4500000000
    assert apple.market_value_usd == 967500000000.0
    assert apple.put_call is None


def test_get_13f_holdings_extracts_put_call_flag() -> None:
    xml = (_FIXTURES_DIR / "sample_13f_infotable.xml").read_text("utf-8")
    positions = parse_13f_infotable(xml)
    spdr = next(p for p in positions if p.cusip == "78462F103")
    assert spdr.put_call == "Put"


def test_get_13f_holdings_empty_when_malformed() -> None:
    assert parse_13f_infotable("") == []
    assert parse_13f_infotable("<not>valid</xml") == []
    assert parse_13f_infotable("<empty/>") == []


def test_get_13f_holdings_handles_missing_namespace() -> None:
    xml = """
<informationTable>
  <infoTable>
    <nameOfIssuer>TEST CORP</nameOfIssuer>
    <cusip>123456789</cusip>
    <value>1000000</value>
    <shrsOrPrnAmt>
      <sshPrnamt>10000</sshPrnamt>
    </shrsOrPrnAmt>
  </infoTable>
</informationTable>
"""
    positions = parse_13f_infotable(xml)
    assert len(positions) == 1
    assert positions[0].name == "TEST CORP"
    assert positions[0].shares == 10000


# ── Live client integration with mocked HTTP ───────────────────────────────


def test_get_filings_records_snapshot(tmp_path: Path) -> None:
    payload = _fake_submissions_payload()

    def handler(request: httpx.Request) -> httpx.Response:
        if "submissions/CIK" in str(request.url):
            return httpx.Response(200, json=payload)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    broker = SnapshotBroker(root=tmp_path / "snap")
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http, snapshot_broker=broker)

    filings = asyncio.run(c.get_filings("0000320193", form_types=("13F",)))
    assert len(filings) == 4
    rows = list(broker.list_snapshots(edge_type="filings"))
    assert len(rows) == 1
    asyncio.run(c.aclose())
    broker.close()


def test_get_filings_returns_empty_on_404(tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda r: httpx.Response(404))
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    broker = SnapshotBroker(root=tmp_path / "snap")
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http, snapshot_broker=broker)
    filings = asyncio.run(c.get_filings("0000999999", form_types=("13F",)))
    assert filings == ()
    asyncio.run(c.aclose())
    broker.close()


# ── 13F end-to-end via mocked HTTP ─────────────────────────────────────────


def test_get_13f_holdings_end_to_end(tmp_path: Path) -> None:
    payload = _fake_submissions_payload()
    xml = (_FIXTURES_DIR / "sample_13f_infotable.xml").read_text("utf-8")
    index_payload = {
        "directory": {
            "item": [
                {"name": "primary_doc.html"},
                {"name": "infotable.xml"},
            ]
        }
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "submissions/CIK" in url:
            return httpx.Response(200, json=payload)
        if "index.json" in url:
            return httpx.Response(200, json=index_payload)
        if "infotable.xml" in url:
            return httpx.Response(200, text=xml)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    broker = SnapshotBroker(root=tmp_path / "snap")
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http, snapshot_broker=broker)

    holdings = asyncio.run(c.get_13f_holdings("0000320193"))
    assert holdings is not None
    assert len(holdings.positions) == 3
    assert holdings.positions[0].cusip == "037833100"
    asyncio.run(c.aclose())
    broker.close()


# ── Snapshot determinism ───────────────────────────────────────────────────


def test_snapshot_integration_for_13f(tmp_path: Path) -> None:
    payload = _fake_submissions_payload()
    xml = (_FIXTURES_DIR / "sample_13f_infotable.xml").read_text("utf-8")
    index_payload = {
        "directory": {"item": [{"name": "infotable.xml"}]}
    }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "submissions/CIK" in url:
            return httpx.Response(200, json=payload)
        if "index.json" in url:
            return httpx.Response(200, json=index_payload)
        if "infotable.xml" in url:
            return httpx.Response(200, text=xml)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, headers={"User-Agent": _VALID_AGENT})
    broker = SnapshotBroker(root=tmp_path / "snap")
    c = SecEdgarClient(user_agent=_VALID_AGENT, client=http, snapshot_broker=broker)

    asyncio.run(c.get_13f_holdings("0000320193"))
    rows_holdings = list(broker.list_snapshots(edge_type="13f_holdings"))
    rows_filings = list(broker.list_snapshots(edge_type="filings"))
    assert len(rows_holdings) == 1
    assert len(rows_filings) == 1
    asyncio.run(c.aclose())
    broker.close()


# ── Filing dates parsed correctly ──────────────────────────────────────────


def test_filing_dates_are_dates() -> None:
    payload = _fake_submissions_payload()
    filings = parse_submissions_filings(
        "0000320193", payload, form_types=("13F",), limit=4
    )
    for f in filings:
        assert isinstance(f.filing_date, date)
    # Latest first.
    assert filings[0].filing_date == date(2026, 4, 15)
    assert filings[-1].filing_date == date(2025, 7, 15)


# ── Network test — opt-in ──────────────────────────────────────────────────


@pytest.mark.network
def test_network_real_aapl_13f(tmp_path: Path) -> None:
    if not os.environ.get("GLOSTAT_SEC_USER_AGENT"):
        pytest.skip("GLOSTAT_SEC_USER_AGENT not set")
    broker = SnapshotBroker(root=tmp_path / "snap")

    async def _go() -> tuple:
        c = SecEdgarClient(snapshot_broker=broker)
        try:
            return await c.get_filings(
                "0000320193", form_types=("13F",), limit=2
            )
        finally:
            await c.aclose()

    try:
        filings = asyncio.run(_go())
    finally:
        broker.close()
    assert isinstance(filings, tuple)
