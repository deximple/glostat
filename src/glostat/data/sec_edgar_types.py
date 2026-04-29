from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

# SEC EDGAR value types extracted to a leaf module so client + parsers can import freely.

FormType = str  # 10-K, 10-Q, 8-K, 13F-HR, ...


@dataclass(frozen=True, slots=True)
class Filing:
    cik: str
    accession_number: str
    form_type: FormType
    filing_date: date
    primary_document: str
    primary_doc_url: str


@dataclass(frozen=True, slots=True)
class HoldingPosition:
    cusip: str
    name: str
    shares: int
    market_value_usd: float
    put_call: str | None = None  # "Put", "Call", or None


@dataclass(frozen=True, slots=True)
class ThirteenFHoldings:
    cik: str
    period_of_report: date
    accession_number: str
    positions: tuple[HoldingPosition, ...]


@dataclass(frozen=True, slots=True)
class CompanyFact:
    concept: str
    unit: str
    value: float
    period_end: date
    fiscal_year: int
    fiscal_period: str
    form: FormType


@dataclass(frozen=True, slots=True)
class CompanyFacts:
    cik: str
    entity_name: str
    facts: tuple[CompanyFact, ...]


@dataclass(frozen=True, slots=True)
class TickerCikMap:
    by_ticker: dict[str, str] = field(default_factory=dict)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(tz=UTC))


__all__ = [
    "CompanyFact",
    "CompanyFacts",
    "Filing",
    "FormType",
    "HoldingPosition",
    "ThirteenFHoldings",
    "TickerCikMap",
]
