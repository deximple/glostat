from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

import structlog

from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseSignal

# E5b — FOMC Drift Expert.
# Universe: SPY + 11 sector ETFs (cross-asset reaction to monetary policy).
# Signal: on each FOMC announcement day's trading-day close, take the
# announcement-day return relative to the prior close. Direction of next-5-day
# drift is hypothesized to follow the announcement-day move (continuation).
#   announcement_day_return > +THRESHOLD → LONG (drift continues up)
#   announcement_day_return < -THRESHOLD → SHORT
# Entry day = announcement day + 1 trading day; horizon = 5 trading days
# (≈ 5 calendar days in the runner; passed as horizon param at hindcast time).
# Public Fed calendar — no API call needed; refresh manually each year.

log: Final = structlog.get_logger(__name__)

# 2024 FOMC announcement dates (8/year): https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
FOMC_DATES: Final[tuple[date, ...]] = (
    # 2024
    date(2024, 1, 31),
    date(2024, 3, 20),
    date(2024, 5, 1),
    date(2024, 6, 12),
    date(2024, 7, 31),
    date(2024, 9, 18),
    date(2024, 11, 7),
    date(2024, 12, 18),
    # 2025
    date(2025, 1, 29),
    date(2025, 3, 19),
    date(2025, 5, 7),
    date(2025, 6, 18),
    date(2025, 7, 30),
    date(2025, 9, 17),
    date(2025, 10, 29),
    date(2025, 12, 10),
    # 2026 (first quarter only — published)
    date(2026, 1, 28),
    date(2026, 3, 18),
    date(2026, 4, 29),
)

_REACTION_THRESHOLD: Final[float] = 0.0035   # 0.35% absolute move
_SCORE_SCALE: Final[float] = 200.0           # 1% reaction → score 2.0
_SCORE_CLIP: Final[float] = 3.0
_CONFIDENCE: Final[float] = 0.55


@dataclass(frozen=True, slots=True)
class FomcEvent:
    ticker: str
    fomc_day: date
    entry_day: date
    reaction_pct: float
    score: float
    direction: str


class EFomcDriftExpert:
    name = "E_FOMC_DRIFT"

    def __init__(
        self,
        *,
        price_cache: PriceCache,
        universe: tuple[str, ...],
        fomc_dates: tuple[date, ...] = FOMC_DATES,
    ) -> None:
        self._cache = price_cache
        self._universe = universe
        self._dates = tuple(d for d in fomc_dates)

    def event_dates_in_window(self, start: date, end: date) -> list[date]:
        return [d for d in self._dates if start <= d <= end]

    async def compute_event(
        self, ticker: str, fomc_day: date
    ) -> FomcEvent | None:
        await self._cache.get(ticker)
        # Reaction: close at FOMC_day vs close at FOMC_day - 1 trading day.
        # Use calendar-day lookback that handles weekends — the cache picks the
        # nearest trading-day bar at-or-before the requested date.
        c_pre = self._cache.close_at_or_before(ticker, fomc_day - timedelta(days=1))
        c_post = self._cache.close_at_or_before(ticker, fomc_day)
        if c_pre is None or c_post is None or c_pre <= 0:
            return None
        reaction = (c_post - c_pre) / c_pre
        raw = reaction * _SCORE_SCALE
        score = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
        if abs(reaction) < _REACTION_THRESHOLD:
            direction = "NEUTRAL"
            score = 0.0
        elif reaction > 0:
            direction = "LONG"
        else:
            direction = "SHORT"
        entry = fomc_day + timedelta(days=1)
        while entry.weekday() >= 5:
            entry += timedelta(days=1)
        return FomcEvent(
            ticker=ticker.upper(),
            fomc_day=fomc_day,
            entry_day=entry,
            reaction_pct=reaction,
            score=score,
            direction=direction,
        )

    def to_signal(self, ev: FomcEvent) -> PhaseSignal:
        return PhaseSignal(
            expert=self.name,
            ticker=ev.ticker,
            day=ev.entry_day,
            score=ev.score,
            direction=ev.direction,
            confidence=_CONFIDENCE if ev.direction != "NEUTRAL" else 0.0,
            metadata=(
                ("fomc_day", ev.fomc_day.isoformat()),
                ("reaction_pct", f"{ev.reaction_pct * 100:.2f}"),
            ),
        )

    @property
    def universe(self) -> tuple[str, ...]:
        return self._universe


__all__ = ["FOMC_DATES", "EFomcDriftExpert", "FomcEvent"]
