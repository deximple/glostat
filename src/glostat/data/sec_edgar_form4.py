from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final
from xml.etree import ElementTree as ET

import structlog

from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.sec_edgar_types import Filing

# SEC Form 4 (insider transaction) parser.
# Form 4 reporting persons file within 2 business days of any reportable
# transaction. Transaction codes that interest us:
#   P = open-market or private purchase
#   S = open-market or private sale
#   A = grant/award/other (not informative for insider conviction)
# We compute a per-issuer "insider buying cluster" by counting unique reporters
# with a P transaction in the trailing window.

log: Final = structlog.get_logger(__name__)

_BASE_WWW: Final = "https://www.sec.gov"
_NS_RE: Final = re.compile(r"^\{[^}]+\}")


@dataclass(frozen=True, slots=True)
class Form4Transaction:
    issuer_cik: str
    accession: str
    filed_at: date
    transaction_date: date
    reporter_name: str
    reporter_cik: str
    reporter_role: str         # Director / Officer / 10% owner
    code: str                   # P / S / A / ...
    shares: float
    price: float
    value_usd: float

    @property
    def is_buy(self) -> bool:
        return self.code.upper() == "P"

    @property
    def is_sell(self) -> bool:
        return self.code.upper() == "S"


async def get_form4_transactions(
    client: SecEdgarClient,
    cik: str,
    *,
    days_back: int = 180,
    limit: int = 60,
    parallel: int = 3,
) -> list[Form4Transaction]:
    # Pull the issuer's recent filings, filter to Form 4, fetch each .xml in
    # bounded-parallel batches to keep total wall time tractable across many
    # tickers (each 13F-style fetch is 2 sequential network calls).
    import asyncio  # noqa: PLC0415

    filings = await client.get_filings(
        cik, form_types=("4", "4/A"), limit=limit
    )
    cutoff = date.today()
    eligible = [f for f in filings if (cutoff - f.filing_date).days <= days_back]
    sem = asyncio.Semaphore(parallel)

    async def fetch_one(f: Filing) -> list[Form4Transaction]:
        async with sem:
            try:
                return await _fetch_and_parse_form4(client, f)
            except Exception as exc:
                log.warning(
                    "form4.fetch_failed",
                    cik=f.cik, accession=f.accession_number, err=str(exc),
                )
                return []

    if not eligible:
        return []
    batches = await asyncio.gather(*(fetch_one(f) for f in eligible))
    out: list[Form4Transaction] = []
    for b in batches:
        out.extend(b)
    return out


async def _fetch_and_parse_form4(
    client: SecEdgarClient, filing: Filing
) -> list[Form4Transaction]:
    # Locate the primary doc: SEC Form 4 .xml. Index endpoint lists files.
    digits = "".join(c for c in filing.cik if c.isdigit())
    acc_no_dashes = filing.accession_number.replace("-", "")
    index_url = (
        f"{_BASE_WWW}/Archives/edgar/data/{int(digits)}/"
        f"{acc_no_dashes}/index.json"
    )
    try:
        idx = await client._get_json(index_url)
    except Exception as exc:
        log.warning("form4.index_failed", cik=filing.cik, err=str(exc))
        return []
    items = ((idx.get("directory", {}) or {}).get("item", [])) or []
    xml_url: str | None = None
    # Score candidates so the most-likely Form 4 XML wins, never FilingSummary.
    best_score = -1
    for item in items:
        name = str(item.get("name", "")).lower()
        if not name.endswith(".xml"):
            continue
        if name == "filingsummary.xml":
            continue
        score = 0
        if "form4" in name:
            score = 5
        elif name.startswith("wf-form4") or name.startswith("wk-form4"):
            score = 4
        elif name.startswith("primary_doc"):
            score = 3
        elif name.startswith("doc"):
            score = 2
        else:
            score = 1
        if score > best_score:
            best_score = score
            xml_url = (
                f"{_BASE_WWW}/Archives/edgar/data/{int(digits)}/"
                f"{acc_no_dashes}/{item['name']}"
            )
    if xml_url is None:
        return []
    try:
        text = await client._get_text(xml_url)
    except Exception as exc:
        log.warning(
            "form4.xml_fetch_failed", url=xml_url, err=str(exc),
        )
        return []
    return parse_form4_xml(
        text,
        issuer_cik=filing.cik,
        accession=filing.accession_number,
        filed_at=filing.filing_date,
    )


def parse_form4_xml(
    xml_text: str, *, issuer_cik: str, accession: str, filed_at: date
) -> list[Form4Transaction]:
    if not xml_text or not xml_text.strip().startswith("<"):
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        log.warning("form4.parse_error", err=str(exc))
        return []
    reporter_name = _findtext(root, "reportingOwner/reportingOwnerId/rptOwnerName") or ""
    reporter_cik = _findtext(root, "reportingOwner/reportingOwnerId/rptOwnerCik") or ""
    reporter_role = _detect_role(root)

    out: list[Form4Transaction] = []
    txns = _findall(root, "nonDerivativeTable/nonDerivativeTransaction")
    for t in txns:
        code = _findtext(t, "transactionCoding/transactionCode") or ""
        if code.upper() not in {"P", "S"}:
            continue
        try:
            txn_date_text = _findtext(t, "transactionDate/value") or ""
            txn_date = date.fromisoformat(txn_date_text[:10])
        except (ValueError, TypeError):
            continue
        shares = _to_float(_findtext(t, "transactionAmounts/transactionShares/value"))
        price = _to_float(
            _findtext(t, "transactionAmounts/transactionPricePerShare/value")
        )
        value = shares * price
        out.append(
            Form4Transaction(
                issuer_cik=issuer_cik,
                accession=accession,
                filed_at=filed_at,
                transaction_date=txn_date,
                reporter_name=reporter_name,
                reporter_cik=reporter_cik,
                reporter_role=reporter_role,
                code=code,
                shares=shares,
                price=price,
                value_usd=value,
            )
        )
    return out


def cluster_buy_count(
    transactions: Sequence[Form4Transaction],
    *,
    window_end: date,
    window_days: int = 14,
) -> int:
    cutoff = date.fromordinal(window_end.toordinal() - window_days)
    unique_reporters: set[str] = set()
    for t in transactions:
        if not t.is_buy:
            continue
        if cutoff <= t.transaction_date <= window_end:
            unique_reporters.add(t.reporter_cik or t.reporter_name)
    return len(unique_reporters)


def cluster_buy_value(
    transactions: Sequence[Form4Transaction],
    *,
    window_end: date,
    window_days: int = 14,
) -> float:
    cutoff = date.fromordinal(window_end.toordinal() - window_days)
    total = 0.0
    for t in transactions:
        if not t.is_buy:
            continue
        if cutoff <= t.transaction_date <= window_end:
            total += t.value_usd
    return total


def _detect_role(root: ET.Element) -> str:
    if _findtext(root, "reportingOwner/reportingOwnerRelationship/isDirector") in {"1", "true"}:
        return "Director"
    if _findtext(root, "reportingOwner/reportingOwnerRelationship/isOfficer") in {"1", "true"}:
        return "Officer"
    if _findtext(
        root, "reportingOwner/reportingOwnerRelationship/isTenPercentOwner"
    ) in {"1", "true"}:
        return "10% Owner"
    return "Other"


def _strip_ns(tag: str) -> str:
    return _NS_RE.sub("", tag)


def _iter_path(element: ET.Element, path: str) -> list[ET.Element]:
    parts = path.split("/")
    current = [element]
    for part in parts:
        nxt: list[ET.Element] = []
        for el in current:
            for child in el:
                if _strip_ns(child.tag) == part:
                    nxt.append(child)
        current = nxt
        if not current:
            return []
    return current


def _findtext(element: ET.Element, path: str) -> str | None:
    matches = _iter_path(element, path)
    if not matches:
        return None
    text = matches[0].text
    return text.strip() if text else None


def _findall(element: ET.Element, path: str) -> list[ET.Element]:
    return _iter_path(element, path)


def _to_float(s: str | None) -> float:
    if s is None:
        return 0.0
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "Form4Transaction",
    "cluster_buy_count",
    "cluster_buy_value",
    "get_form4_transactions",
    "parse_form4_xml",
]
