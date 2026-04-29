from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

# Yahoo client value types extracted to a leaf module so client + parsers can import freely.

HoldersKind = Literal["institutional", "major", "mutualfund", "insider"]


@dataclass(frozen=True, slots=True)
class OhlcvBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    adj_close: float | None = None


@dataclass(frozen=True, slots=True)
class OhlcvSeries:
    ticker: str
    bars: tuple[OhlcvBar, ...]
    interval: str = "1d"

    def __len__(self) -> int:
        return len(self.bars)


@dataclass(frozen=True, slots=True)
class Fundamentals:
    ticker: str
    pe_ratio: float | None
    forward_pe: float | None
    eps: float | None
    forward_eps: float | None
    roe: float | None
    market_cap: float | None
    dividend_yield: float | None
    beta: float | None
    fifty_two_week_high: float | None
    fifty_two_week_low: float | None
    raw: tuple[tuple[str, Any], ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DividendEvent:
    ex_date: date
    amount: float


@dataclass(frozen=True, slots=True)
class DividendHistory:
    ticker: str
    events: tuple[DividendEvent, ...]


@dataclass(frozen=True, slots=True)
class EarningsEvent:
    ticker: str
    earnings_date: datetime
    eps_estimate: float | None
    eps_actual: float | None
    revenue_estimate: float | None


@dataclass(frozen=True, slots=True)
class EarningsCalendar:
    ticker: str
    upcoming: tuple[EarningsEvent, ...]


@dataclass(frozen=True, slots=True)
class HoldersSnapshot:
    ticker: str
    kind: HoldersKind
    holders: tuple[tuple[str, float], ...]   # (holder_name, pct_held) — back-compat surface
    fetched_at: datetime
    # Sprint 5 PR #1: full holders payload includes shares + reported_at so the
    # E_FUND_FLOW delta classifier can detect institutional accumulation/distribution
    # without re-fetching. ``holders`` (legacy) stays for read-only callers.
    rows: tuple[tuple[str, float, int, str], ...] = field(default_factory=tuple)


__all__ = [
    "DividendEvent",
    "DividendHistory",
    "EarningsCalendar",
    "EarningsEvent",
    "Fundamentals",
    "HoldersKind",
    "HoldersSnapshot",
    "OhlcvBar",
    "OhlcvSeries",
]
