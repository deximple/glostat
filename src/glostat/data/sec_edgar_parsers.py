from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Sequence
from datetime import date
from typing import Any

from glostat.data.sec_edgar_types import (
    CompanyFact,
    CompanyFacts,
    Filing,
    HoldingPosition,
)

# SEC EDGAR response parsers extracted to keep sec_edgar_client.py ≤ 400 lines.
# Pure functions only — sync workers run inside asyncio.to_thread when called from clients.

_NS_VARIANTS: tuple[str, ...] = (
    "{http://www.sec.gov/edgar/document/thirteenf/informationtable}",
    "{http://www.sec.gov/edgar/13Fdocument/informationtable}",
    "{http://www.sec.gov/edgar/thirteenf/informationtable}",
    "",  # WHY: some filers omit the namespace entirely; tolerate that path.
)


def parse_submissions_filings(
    cik: str,
    payload: dict[str, Any],
    *,
    form_types: Sequence[str],
    limit: int,
) -> list[Filing]:
    # WHY: SEC submissions JSON keeps "recent" parallel arrays — zip them, filter, cap.
    recent = ((payload.get("filings", {}) or {}).get("recent", {}) or {})
    accessions: list[str] = list(recent.get("accessionNumber", []) or [])
    forms: list[str] = list(recent.get("form", []) or [])
    dates: list[str] = list(recent.get("filingDate", []) or [])
    primaries: list[str] = list(recent.get("primaryDocument", []) or [])
    n = min(len(accessions), len(forms), len(dates), len(primaries))
    wanted: set[str] = {f.upper() for f in form_types}
    out: list[Filing] = []
    for i in range(n):
        form = str(forms[i] or "").upper()
        if not _form_matches(form, wanted):
            continue
        try:
            filing_date = date.fromisoformat(str(dates[i]))
        except (TypeError, ValueError):
            continue
        accession = str(accessions[i] or "")
        primary_doc = str(primaries[i] or "")
        url = _primary_doc_url(cik, accession, primary_doc)
        out.append(
            Filing(
                cik=cik,
                accession_number=accession,
                form_type=form,
                filing_date=filing_date,
                primary_document=primary_doc,
                primary_doc_url=url,
            )
        )
        if len(out) >= limit:
            break
    return out


def _form_matches(actual: str, wanted: set[str]) -> bool:
    # WHY: callers pass "13F" but SEC stores "13F-HR", "13F-HR/A"; match prefix family.
    if actual in wanted:
        return True
    return any(actual.startswith(w) for w in wanted)


def _primary_doc_url(cik: str, accession: str, primary_doc: str) -> str:
    digits = "".join(c for c in cik if c.isdigit())
    acc_no_dashes = accession.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{int(digits)}/"
        f"{acc_no_dashes}/{primary_doc}"
    )


def filing_index_url(cik: str, accession: str) -> str:
    digits = "".join(c for c in cik if c.isdigit())
    acc_no_dashes = accession.replace("-", "")
    return (
        f"https://www.sec.gov/cgi-bin/browse-edgar?"
        f"action=getcompany&CIK={digits}&type=13F&dateb=&owner=include&count=40&"
        f"action=getcompany&accession_number={acc_no_dashes}"
    )


def parse_13f_infotable(xml_text: str) -> list[HoldingPosition]:
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    positions: list[HoldingPosition] = []
    for ns in _NS_VARIANTS:
        for entry in root.iter(f"{ns}infoTable"):
            pos = _parse_info_entry(entry, ns)
            if pos is not None:
                positions.append(pos)
        if positions:
            break
    return positions


def _parse_info_entry(entry: ET.Element, ns: str) -> HoldingPosition | None:
    cusip = _text(entry.find(f"{ns}cusip"))
    name = _text(entry.find(f"{ns}nameOfIssuer"))
    value = _float(entry.find(f"{ns}value"))
    sh = entry.find(f"{ns}shrsOrPrnAmt")
    shares = _int(sh.find(f"{ns}sshPrnamt")) if sh is not None else None
    if cusip is None or shares is None:
        return None
    # WHY: SEC filings before 2022 used "value in $1000s"; after 2022, raw USD.
    # Both shapes flow through; downstream consumers normalize if needed.
    market_value = float(value) if value is not None else 0.0
    put_call = _text(entry.find(f"{ns}putCall"))
    return HoldingPosition(
        cusip=cusip,
        name=name or "",
        shares=int(shares),
        market_value_usd=market_value,
        put_call=put_call or None,
    )


def _text(el: ET.Element | None) -> str | None:
    if el is None or el.text is None:
        return None
    s = el.text.strip()
    return s or None


def _int(el: ET.Element | None) -> int | None:
    s = _text(el)
    if s is None:
        return None
    try:
        return int(float(s))
    except (TypeError, ValueError):
        return None


def _float(el: ET.Element | None) -> float | None:
    s = _text(el)
    if s is None:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def filings_to_payload(filings: list[Filing]) -> dict[str, Any]:
    return {
        "count": len(filings),
        "filings": [
            {
                "cik": f.cik,
                "accession_number": f.accession_number,
                "form_type": f.form_type,
                "filing_date": f.filing_date.isoformat(),
                "primary_document": f.primary_document,
                "primary_doc_url": f.primary_doc_url,
            }
            for f in filings
        ],
    }


def holdings_to_payload(positions: list[HoldingPosition]) -> dict[str, Any]:
    return {
        "count": len(positions),
        "positions": [
            {
                "cusip": p.cusip,
                "name": p.name,
                "shares": p.shares,
                "market_value_usd": p.market_value_usd,
                "put_call": p.put_call,
            }
            for p in positions
        ],
    }


def parse_company_facts(cik_padded: str, data: dict[str, Any]) -> CompanyFacts:
    entity_name = str(data.get("entityName", "unknown"))
    facts: list[CompanyFact] = []
    us_gaap = (data.get("facts", {}) or {}).get("us-gaap", {}) or {}
    for concept, body in us_gaap.items():
        units = body.get("units", {}) or {}
        for unit, entries in units.items():
            for entry in entries:
                fact = _parse_fact_entry(concept, unit, entry)
                if fact is not None:
                    facts.append(fact)
    return CompanyFacts(cik=cik_padded, entity_name=entity_name, facts=tuple(facts))


def _parse_fact_entry(concept: str, unit: str, entry: dict[str, Any]) -> CompanyFact | None:
    val = entry.get("val")
    end = entry.get("end")
    fy = entry.get("fy")
    fp = entry.get("fp")
    form = entry.get("form")
    if val is None or end is None or fy is None:
        return None
    try:
        return CompanyFact(
            concept=concept,
            unit=unit,
            value=float(val),
            period_end=date.fromisoformat(end),
            fiscal_year=int(fy),
            fiscal_period=str(fp or ""),
            form=str(form or ""),
        )
    except (TypeError, ValueError):
        return None


def company_facts_to_payload(c: CompanyFacts) -> dict[str, Any]:
    return {
        "cik": c.cik,
        "entity_name": c.entity_name,
        "facts": [
            {
                "concept": f.concept,
                "unit": f.unit,
                "value": f.value,
                "period_end": f.period_end.isoformat(),
                "fiscal_year": f.fiscal_year,
                "fiscal_period": f.fiscal_period,
                "form": f.form,
            }
            for f in c.facts
        ],
    }


__all__ = [
    "company_facts_to_payload",
    "filing_index_url",
    "filings_to_payload",
    "holdings_to_payload",
    "parse_13f_infotable",
    "parse_company_facts",
    "parse_submissions_filings",
]
