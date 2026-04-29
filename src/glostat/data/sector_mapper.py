from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Final, Protocol

import structlog

# SIC → GICS-11 sector mapping (Sprint 1 PR #4).
# WHY: SEC EDGAR returns SIC codes (Standard Industrial Classification, 4-digit).
# Sector-aware z-score in E_FUNDAMENTAL needs the 11 GICS sector buckets that
# institutional research reports use. We translate SIC ranges → GICS buckets
# using major-group rules from the SEC's Division of Corporation Finance index.
# Table simplified for MVP — no GICS sub-industry granularity.

log: Final = structlog.get_logger(__name__)

GICS_SECTORS: Final[tuple[str, ...]] = (
    "Technology",
    "Healthcare",
    "Financials",
    "ConsumerDiscretionary",
    "ConsumerStaples",
    "Industrials",
    "Energy",
    "Materials",
    "Utilities",
    "RealEstate",
    "Communications",
    "OTHER",
)

UNKNOWN_SECTOR: Final[str] = "UNKNOWN"


# (low, high, sector) — closed interval. NARROWEST RANGES FIRST so specific
# sub-industries (e.g., pharmaceuticals 2830-2836) win over broad parent ranges
# (e.g., chemicals 2800-2899 → Materials).
_SIC_RANGES: Final[tuple[tuple[int, int, str], ...]] = (
    # ── narrow / specific GICS overrides first ────────────────────────────
    # Healthcare specifics
    (2830, 2836, "Healthcare"),       # pharmaceuticals (carved out of Materials)
    (3840, 3851, "Healthcare"),       # medical devices (carved out of Industrials)
    (8000, 8099, "Healthcare"),
    # Technology specifics
    (3570, 3579, "Technology"),       # computer hardware (carved out of Industrials)
    (3670, 3679, "Technology"),       # electronic components (carved out of Industrials)
    (3825, 3829, "Technology"),       # instruments
    (7370, 7379, "Technology"),       # computer services / software
    # Consumer Discretionary motor vehicles (carved out of Industrials transport)
    (3711, 3713, "ConsumerDiscretionary"),
    # Communications carve-outs
    (4830, 4841, "Communications"),   # broadcasting/cable (carved out of telecom)
    (7810, 7829, "Communications"),   # motion pictures
    # Energy carve-outs
    (1300, 1499, "Energy"),
    (2900, 2999, "Energy"),

    # ── broad GICS buckets ────────────────────────────────────────────────
    # Materials
    (1000, 1299, "Materials"),
    (2400, 2499, "Materials"),
    (2600, 2699, "Materials"),
    (2800, 2899, "Materials"),
    (3300, 3399, "Materials"),
    # Industrials
    (1500, 1799, "Industrials"),
    (3400, 3599, "Industrials"),
    (3700, 3799, "Industrials"),
    (4000, 4799, "Industrials"),
    # Consumer Staples
    (100, 999, "ConsumerStaples"),
    (2000, 2199, "ConsumerStaples"),
    (5400, 5499, "ConsumerStaples"),
    # Consumer Discretionary
    (2200, 2399, "ConsumerDiscretionary"),
    (2500, 2599, "ConsumerDiscretionary"),
    (3000, 3199, "ConsumerDiscretionary"),
    (5000, 5399, "ConsumerDiscretionary"),
    (5500, 5999, "ConsumerDiscretionary"),
    (7000, 7299, "ConsumerDiscretionary"),
    (7800, 7999, "ConsumerDiscretionary"),
    # Financials
    (6000, 6199, "Financials"),
    (6200, 6299, "Financials"),
    (6300, 6399, "Financials"),
    (6700, 6799, "Financials"),
    # Real Estate
    (6500, 6599, "RealEstate"),
    # Communications (broad)
    (2700, 2799, "Communications"),
    (4800, 4899, "Communications"),
    # Utilities
    (4900, 4999, "Utilities"),
)


class _SecClientProto(Protocol):
    async def ticker_to_cik(self, ticker: str) -> str: ...

    async def get_company_facts(self, cik: str): ...  # type: ignore[no-untyped-def]


def sic_to_gics(sic_code: str | int | None) -> str:
    if sic_code is None:
        return UNKNOWN_SECTOR
    try:
        sic_int = int(str(sic_code).strip())
    except (TypeError, ValueError):
        return UNKNOWN_SECTOR
    if sic_int <= 0:
        return UNKNOWN_SECTOR
    for low, high, sector in _SIC_RANGES:
        if low <= sic_int <= high:
            return sector
    return "OTHER"


# Optional async resolver — used only by `glostat universe build` for live mode.
# Mock mode bypasses this entirely (sector taken from fixture).

ResolveSic = Callable[[str], Awaitable[str | None]]


async def get_sector(
    ticker: str,
    *,
    resolve_sic: ResolveSic,
) -> str:
    # WHY: keep network code (httpx) out of this module — caller injects an async
    # function that returns SIC string for the ticker. That function reads either
    # the SEC EDGAR submissions endpoint (live) or the fixture (mock).
    try:
        sic = await resolve_sic(ticker)
    except Exception as exc:
        log.warning("sector_mapper.resolve_failed", ticker=ticker, err=str(exc))
        return UNKNOWN_SECTOR
    return sic_to_gics(sic)


async def resolve_sic_via_sec(
    ticker: str,
    *,
    sec_client: _SecClientProto,
    submissions_fetcher: Callable[[str], Awaitable[dict]],
) -> str | None:
    # WHY: SEC EDGAR companyfacts JSON does NOT include SIC. The submissions
    # endpoint (CIK########.json) does. Caller wires submissions_fetcher.
    try:
        cik = await sec_client.ticker_to_cik(ticker)
    except KeyError:
        return None
    try:
        body = await submissions_fetcher(cik)
    except Exception as exc:
        log.warning("sector_mapper.submissions_fetch_failed", ticker=ticker, err=str(exc))
        return None
    return str(body.get("sic") or "") or None


__all__ = [
    "GICS_SECTORS",
    "UNKNOWN_SECTOR",
    "get_sector",
    "resolve_sic_via_sec",
    "sic_to_gics",
]
