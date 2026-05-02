from __future__ import annotations

from datetime import date, timedelta
from itertools import pairwise

import pytest

from glostat.data.kr_calendar_client import (
    _BOK_2026,
    _OPEC_MINISTER_2026_FALLBACK,
    CalendarEvent,
    EventKind,
    KrCalendarClient,
    _extract_dates,
    _next_bok_rate,
    _next_kr_earnings,
    _next_opec_jmmc,
    _parse_opec_minister_dates,
)

# ── Pure helpers ──────────────────────────────────────────────────────────


class TestNextKrEarnings:
    def test_within_q1_window(self) -> None:
        # Early Feb → next earnings = Q4-prior-year + 45d (which is mid-Feb,
        # but if past, then Q1 + 45d = mid-May).
        d = _next_kr_earnings(date(2026, 2, 1))
        # Result should be either 2026-02-14 (Q4 2025+45) or 2026-05-15.
        assert d >= date(2026, 2, 14)

    def test_past_q4_returns_next_year_q1(self) -> None:
        # Late December → must wrap to next year's Q1 + 45d.
        d = _next_kr_earnings(date(2026, 12, 28))
        # Either 2026 Q4 (12-31 + 45 = 2027-02-14) or beyond.
        assert d >= date(2027, 2, 14)


class TestNextBokRate:
    def test_jan_returns_jan_meeting(self) -> None:
        d = _next_bok_rate(date(2026, 1, 1))
        assert d == _BOK_2026[0]

    def test_after_last_returns_none(self) -> None:
        last = _BOK_2026[-1]
        assert _next_bok_rate(last + timedelta(days=1)) is None

    def test_returns_first_future_meeting(self) -> None:
        # Day after first BoK meeting → second meeting.
        d = _next_bok_rate(_BOK_2026[0] + timedelta(days=1))
        assert d == _BOK_2026[1]


class TestNextOpecJmmc:
    def test_first_wednesday_of_current_month(self) -> None:
        # March 2026: 1st Wed = 4 March.
        d = _next_opec_jmmc(date(2026, 3, 1))
        assert d == date(2026, 3, 4)

    def test_after_first_wed_rolls_to_next_month(self) -> None:
        # March 5 (after 1st Wed = March 4) → April 1st Wed = April 1.
        d = _next_opec_jmmc(date(2026, 3, 5))
        assert d == date(2026, 4, 1)


class TestExtractDates:
    def test_day_month_year_format(self) -> None:
        text = "The 5 December 2026 meeting"
        out = _extract_dates(text)
        assert date(2026, 12, 5) in out

    def test_month_day_year_format(self) -> None:
        text = "Scheduled June 2, 2026 in Vienna"
        out = _extract_dates(text)
        assert date(2026, 6, 2) in out

    def test_invalid_month_skipped(self) -> None:
        text = "5 Notamonth 2026"
        out = _extract_dates(text)
        assert not out

    def test_invalid_date_skipped(self) -> None:
        # February 30 doesn't exist; parser must filter via ValueError.
        text = "30 February 2026"
        out = _extract_dates(text)
        # We can't construct date(2026, 2, 30), but we can verify the set
        # contains no February 2026 entry from this parse.
        feb_2026 = {d for d in out if d.year == 2026 and d.month == 2}
        assert not feb_2026


class TestParseOpecMinisterDates:
    def test_finds_ministerial_paragraphs(self) -> None:
        html = """
        <html><body>
        <p>The 200th Ministerial Conference will be held on 5 December 2026.</p>
        <p>An ad-hoc meeting on 2 June 2026.</p>
        </body></html>
        """
        out = _parse_opec_minister_dates(html)
        # Both dates should be picked up since they're in the future
        # (test runs after test fixture date 2026-04 typically).
        assert any(d in out for d in (date(2026, 12, 5), date(2026, 6, 2)))

    def test_no_ministerial_keyword_returns_empty(self) -> None:
        html = "<p>5 December 2026 unrelated text</p>"
        # No "Ministerial" or "Conference" anchor → returns empty tuple
        # (per the parser's contextual guardrail).
        out = _parse_opec_minister_dates(html)
        assert out == ()


# ── Calendar event dataclass ──────────────────────────────────────────────


class TestCalendarEvent:
    def test_imminent_within_7_days(self) -> None:
        e = CalendarEvent(
            kind=EventKind.BOK_RATE, date_utc=date(2026, 5, 10),
            label="BoK", days_to=5,
        )
        assert e.is_imminent
        assert not e.is_very_imminent

    def test_very_imminent_within_3_days(self) -> None:
        e = CalendarEvent(
            kind=EventKind.OPEC_JMMC, date_utc=date(2026, 5, 5),
            label="JMMC", days_to=2,
        )
        assert e.is_imminent
        assert e.is_very_imminent


# ── Client integration ────────────────────────────────────────────────────


class TestKrCalendarClient:
    @pytest.mark.asyncio
    async def test_next_events_returns_sorted(self) -> None:
        client = KrCalendarClient()
        # Use a date near multiple events.
        events = await client.next_events(today=date(2026, 4, 1), lookahead_days=120)
        assert len(events) >= 1
        # Events sorted ascending by date.
        for a, b in pairwise(events):
            assert a.date_utc <= b.date_utc

    @pytest.mark.asyncio
    async def test_lookahead_filters(self) -> None:
        client = KrCalendarClient()
        events = await client.next_events(today=date(2026, 4, 1), lookahead_days=10)
        for e in events:
            assert e.days_to <= 10

    @pytest.mark.asyncio
    async def test_includes_bok_when_in_lookahead(self) -> None:
        client = KrCalendarClient()
        # 2026-04-01: next BoK is 2026-04-10 (in _BOK_2026).
        events = await client.next_events(today=date(2026, 4, 1), lookahead_days=30)
        bok_events = [e for e in events if e.kind == EventKind.BOK_RATE]
        assert len(bok_events) == 1
        assert bok_events[0].date_utc == date(2026, 4, 10)
        assert bok_events[0].days_to == 9


# ── Hardcoded constants sanity ────────────────────────────────────────────


class TestHardcodedConstants:
    def test_bok_2026_chronological(self) -> None:
        assert list(_BOK_2026) == sorted(_BOK_2026)

    def test_bok_2026_eight_meetings(self) -> None:
        # BoK standing pattern = 8 meetings/year.
        assert len(_BOK_2026) == 8

    def test_opec_minister_fallback_in_future(self) -> None:
        # All fallback dates should be in 2026.
        for d in _OPEC_MINISTER_2026_FALLBACK:
            assert d.year == 2026
