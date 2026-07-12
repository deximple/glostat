from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final

import httpx
import structlog

from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

# v1.6 P5 — KR event calendar client (P5 Event-Driven panel finding).
#
# WHY: P5 panel showed v1.5 outputs are "static snapshots, not time-axis
# predictions" — 30d horizon predictions ignore upcoming D-day events
# (earnings, BoK, OPEC) that drive volatility. This client surfaces the
# next ~90 days of relevant events so the composite predictor can:
#   1. Widen CI when an event is imminent (D-day < 7)
#   2. Populate next_triggers with concrete dates
#   3. Feed E_PEAD_KR with prior-quarter earnings dates
#
# Sources:
#   - KR earnings (heuristic): Q-end + 45 days for "expected next 분기보고서"
#   - BoK 금통위: hardcoded 2026 schedule (8 meetings/year, public)
#   - OPEC: scrape opec.org/.../meetings.htm (HTTP), fallback to monthly
#     first-Wednesday heuristic when scrape fails
#
# Auto OPEC source: opec.org publishes the year's meeting list as static
# HTML. Scrape once on first call, cache for 30 days.

log: Final = structlog.get_logger(__name__)

_OPEC_URL: Final = "https://www.opec.org/opec_web/en/data_graphs/40.htm"
_OPEC_TIMEOUT: Final[httpx.Timeout] = httpx.Timeout(10.0, connect=5.0)
_OPEC_CACHE_DAYS: Final[int] = 30

_REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[3]
_CACHE_DIR: Final[Path] = _REPO_ROOT / "cache" / "kr_calendar"
_OPEC_CACHE_FILE: Final[Path] = _CACHE_DIR / "opec_2026.html"


class EventKind(StrEnum):
    EARNINGS_KR     = "earnings_kr"
    BOK_RATE        = "bok_rate"
    OPEC_MINISTER   = "opec_minister"
    OPEC_JMMC       = "opec_jmmc"


@dataclass(frozen=True, slots=True)
class CalendarEvent:
    kind: EventKind
    date_utc: date
    label: str
    days_to: int

    @property
    def is_imminent(self) -> bool:
        return self.days_to <= 7

    @property
    def is_very_imminent(self) -> bool:
        return self.days_to <= 3


# ── BoK 2026 — hardcoded schedule ─────────────────────────────────────────
# Source: https://www.bok.or.kr/portal/bbs/B0000232/list.do (verified
# 2026-04-29). 8 meetings/year is BoK's standing pattern.
_BOK_2026: Final[tuple[date, ...]] = (
    date(2026, 1, 16),
    date(2026, 2, 27),
    date(2026, 4, 10),
    date(2026, 5, 30),
    date(2026, 7, 11),
    date(2026, 8, 29),
    date(2026, 10, 17),
    date(2026, 11, 28),
)


# ── OPEC heuristic fallback ───────────────────────────────────────────────
# OPEC ministerial: typically 2/year (June + December). JMMC: monthly.
_OPEC_MINISTER_2026_FALLBACK: Final[tuple[date, ...]] = (
    date(2026, 6, 2), date(2026, 12, 5),
)


def _next_kr_earnings(today: date) -> date:
    # WHY: KR 분기보고서 deadline = Q-end + 45 days (KIFRS rule).
    # Annual report deadline = year-end + 90 days.
    q_ends = [
        date(today.year, 3, 31),
        date(today.year, 6, 30),
        date(today.year, 9, 30),
        date(today.year, 12, 31),
    ]
    for q_end in q_ends:
        report_due = q_end + timedelta(days=45)
        if report_due >= today:
            return report_due
    # Past Q4 of this year — next Q1 of next year.
    return date(today.year + 1, 3, 31) + timedelta(days=45)


def _next_bok_rate(today: date) -> date | None:
    for d in _BOK_2026:
        if d >= today:
            return d
    return None


def _next_opec_jmmc(today: date) -> date:
    # JMMC = first Wednesday of each month (regular review).
    candidate = today.replace(day=1)
    while True:
        # Find first Wednesday of `candidate`'s month.
        first_wed = candidate
        while first_wed.weekday() != 2:    # 0=Mon..2=Wed
            first_wed += timedelta(days=1)
        if first_wed >= today:
            return first_wed
        # Move to next month.
        next_month = candidate.replace(day=28) + timedelta(days=4)
        candidate = next_month.replace(day=1)


def _next_opec_minister_fallback(today: date) -> date | None:
    for d in _OPEC_MINISTER_2026_FALLBACK:
        if d >= today:
            return d
    return None


class KrCalendarClient:
    """Fetch upcoming KR-relevant calendar events.

    Used by E_PEAD_KR (earnings dates), composite predictor (CI widening),
    and cli_predict_print.next_triggers (D-day countdown).
    """

    def __init__(
        self,
        *,
        snapshot_broker: SnapshotBroker | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._broker = snapshot_broker
        self._http = http_client
        self._opec_minister_cache: tuple[date, ...] | None = None

    async def next_events(
        self, *, today: date | None = None, lookahead_days: int = 60,
    ) -> tuple[CalendarEvent, ...]:
        ref = today or datetime.now(tz=UTC).date()
        events: list[CalendarEvent] = []

        # KR earnings (heuristic).
        kr_e = _next_kr_earnings(ref)
        if (kr_e - ref).days <= lookahead_days:
            events.append(_event(EventKind.EARNINGS_KR, kr_e, ref,
                                 label=f"KR 분기보고서 due ~{kr_e.isoformat()}"))

        # BoK 금통위.
        bok = _next_bok_rate(ref)
        if bok is not None and (bok - ref).days <= lookahead_days:
            events.append(_event(EventKind.BOK_RATE, bok, ref,
                                 label=f"BoK 금통위 {bok.isoformat()}"))

        # OPEC Minister (auto scrape with fallback).
        minister_dates = await self._opec_minister_dates()
        for d in minister_dates:
            if ref <= d <= ref + timedelta(days=lookahead_days):
                events.append(_event(EventKind.OPEC_MINISTER, d, ref,
                                     label=f"OPEC 장관급 회의 {d.isoformat()}"))

        # OPEC JMMC (monthly heuristic).
        jmmc = _next_opec_jmmc(ref)
        if (jmmc - ref).days <= lookahead_days:
            events.append(_event(EventKind.OPEC_JMMC, jmmc, ref,
                                 label=f"OPEC JMMC ~{jmmc.isoformat()}"))

        events.sort(key=lambda e: e.date_utc)
        return tuple(events)

    async def _opec_minister_dates(self) -> tuple[date, ...]:
        if self._opec_minister_cache is not None:
            return self._opec_minister_cache
        cached = _read_opec_cache()
        if cached is not None:
            parsed = _parse_opec_minister_dates(cached)
            if parsed:
                self._opec_minister_cache = parsed
                return parsed
        scraped = await self._scrape_opec()
        if scraped:
            _write_opec_cache(scraped)
            parsed = _parse_opec_minister_dates(scraped)
            if parsed:
                self._opec_minister_cache = parsed
                return parsed
        # Fallback to hardcoded 2026.
        log.info("kr_calendar.opec_fallback_used")
        self._opec_minister_cache = _OPEC_MINISTER_2026_FALLBACK
        return _OPEC_MINISTER_2026_FALLBACK

    async def _scrape_opec(self) -> str | None:
        client_owned = False
        client = self._http
        if client is None:
            client = httpx.AsyncClient(timeout=_OPEC_TIMEOUT)
            client_owned = True
        try:
            resp = await client.get(_OPEC_URL, follow_redirects=True)
            if resp.status_code != 200:
                return None
            self._record_snapshot(resp.text)
            return resp.text
        except (TimeoutError, httpx.HTTPError) as exc:
            log.info("kr_calendar.opec_scrape_failed", err=str(exc))
            return None
        finally:
            if client_owned:
                await client.aclose()

    def _record_snapshot(self, payload: str) -> None:
        if self._broker is None:
            return
        try:
            self._broker.save_snapshot(
                SnapshotKey(
                    uaid="OPEC.MEETINGS",
                    edge_type="opec_calendar",
                    ts_utc=datetime.now(tz=UTC),
                    tool="kr_calendar.opec",
                    params_canon='{"url":"opec.org/40.htm"}',
                ),
                {"html_size": len(payload)},
            )
        except Exception as exc:
            log.info("kr_calendar.opec_snapshot_skip", err=str(exc))


# ── helpers ────────────────────────────────────────────────────────────────


def _event(
    kind: EventKind, d: date, ref: date, *, label: str,
) -> CalendarEvent:
    return CalendarEvent(
        kind=kind, date_utc=d, label=label, days_to=(d - ref).days,
    )


def _read_opec_cache() -> str | None:
    if not _OPEC_CACHE_FILE.exists():
        return None
    age_days = (
        datetime.now(tz=UTC).timestamp()
        - _OPEC_CACHE_FILE.stat().st_mtime
    ) / 86400.0
    if age_days > _OPEC_CACHE_DAYS:
        return None
    try:
        return _OPEC_CACHE_FILE.read_text("utf-8")
    except OSError:
        return None


def _write_opec_cache(payload: str) -> None:
    try:
        _OPEC_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _OPEC_CACHE_FILE.write_text(payload, "utf-8")
    except OSError:
        pass


# Parse dates of the form "5 December 2026" / "5 Dec 2026" / "December 5, 2026"
# from the OPEC HTML body. Imperfect — fall back to heuristic when nothing matches.
_MONTH_NAMES: Final[dict[str, int]] = {
    "january": 1, "jan": 1, "february": 2, "feb": 2, "march": 3, "mar": 3,
    "april": 4, "apr": 4, "may": 5, "june": 6, "jun": 6, "july": 7, "jul": 7,
    "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "october": 10, "oct": 10, "november": 11, "nov": 11,
    "december": 12, "dec": 12,
}

# Patterns: "5 December 2026", "5 Dec 2026", "December 5, 2026"
_PATTERN_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})\b"
)
_PATTERN_MONTH_DAY_YEAR = re.compile(
    r"\b([A-Za-z]+)\s+(\d{1,2}),?\s+(\d{4})\b"
)


def _parse_opec_minister_dates(html: str) -> tuple[date, ...]:
    found: set[date] = set()
    # Look only inside the same paragraph as "Ministerial" or "Conference".
    for chunk in re.findall(
        r"[^.\n]*(?:Ministerial|Conference)[^.\n]*", html, flags=re.IGNORECASE,
    ):
        found.update(_extract_dates(chunk))
    # If no contextual matches, fall back to scanning the whole document
    # — risk of false positives, but we filter to current year + future.
    if not found:
        return ()
    today = datetime.now(tz=UTC).date()
    future = sorted(d for d in found if d >= today)
    return tuple(future[:6])  # cap at 6 entries to bound noise


def _extract_dates(text: str) -> set[date]:
    out: set[date] = set()
    for m in _PATTERN_DAY_MONTH_YEAR.finditer(text):
        day, month_name, year = m.group(1), m.group(2).lower(), m.group(3)
        month = _MONTH_NAMES.get(month_name)
        if month is None:
            continue
        try:
            out.add(date(int(year), month, int(day)))
        except ValueError:
            continue
    for m in _PATTERN_MONTH_DAY_YEAR.finditer(text):
        month_name, day, year = m.group(1).lower(), m.group(2), m.group(3)
        month = _MONTH_NAMES.get(month_name)
        if month is None:
            continue
        try:
            out.add(date(int(year), month, int(day)))
        except ValueError:
            continue
    return out


__all__ = [
    "CalendarEvent",
    "EventKind",
    "KrCalendarClient",
]
