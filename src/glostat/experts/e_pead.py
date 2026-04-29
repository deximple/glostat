from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

import structlog

from glostat.data.yfinance_client import (
    YFinanceClient,
    YFinanceDataError,
    YFinanceUnavailableError,
)
from glostat.data.yfinance_types import EarningsCalendar
from glostat.phase1b.types import PhaseSignal

# E5a — PEAD (Post-Earnings Announcement Drift) Expert.
# Universe: S&P 500 top 50 (config/universes/sp500_top50.txt).
# Signal:
#   For each ticker, fetch earnings_dates (last ~8 quarters).
#   For each historical event with both eps_actual + eps_estimate:
#     surprise = (actual - estimate) / abs(estimate)
#     entry day = next trading day after announcement (T+1)
#     exit day  = T+30
#     direction = LONG if surprise > +SURPRISE_THRESHOLD; SHORT if < -threshold
#     score = clip(surprise * SCORE_SCALE, -3, +3)
# Hindcast loop iterates EPS events (not (date, ticker) grid) since signals
# only occur on T+1 of an earnings release.

log: Final = structlog.get_logger(__name__)

_SURPRISE_THRESHOLD: Final[float] = 0.025      # 2.5% surprise minimum
_SCORE_SCALE: Final[float] = 30.0              # 5% surprise → score 1.5
_SCORE_CLIP: Final[float] = 3.0
_CONFIDENCE_BASE: Final[float] = 0.6


@dataclass(frozen=True, slots=True)
class PeadEvent:
    ticker: str
    earnings_date: date
    actual_eps: float
    estimate_eps: float
    surprise_pct: float

    @property
    def entry_day(self) -> date:
        # T+1 — handle weekend by skipping forward.
        d = self.earnings_date + timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d


class EPeadExpert:
    name = "E_PEAD"

    def __init__(
        self,
        *,
        yf_client: YFinanceClient,
        start_date: date,
        end_date: date,
    ) -> None:
        self._yf = yf_client
        self._start = start_date
        self._end = end_date
        self._calendars: dict[str, EarningsCalendar] = {}

    async def get_events(self, ticker: str) -> list[PeadEvent]:
        ticker_u = ticker.upper().strip()
        cal = self._calendars.get(ticker_u)
        if cal is None:
            try:
                cal = await self._yf.get_earnings_calendar(ticker_u)
            except (YFinanceUnavailableError, YFinanceDataError) as exc:
                log.warning("pead.calendar_failed", ticker=ticker_u, err=str(exc))
                return []
            except Exception as exc:
                log.warning("pead.calendar_unexpected", ticker=ticker_u, err=str(exc))
                return []
            self._calendars[ticker_u] = cal
        out: list[PeadEvent] = []
        for ev in cal.upcoming:
            if ev.eps_actual is None or ev.eps_estimate is None:
                continue
            est = float(ev.eps_estimate)
            if est == 0.0:
                continue
            d = ev.earnings_date.date() if hasattr(ev.earnings_date, "date") else ev.earnings_date
            if not isinstance(d, date):
                continue
            if d < self._start or d > self._end:
                continue
            surprise = (float(ev.eps_actual) - est) / abs(est)
            out.append(
                PeadEvent(
                    ticker=ticker_u,
                    earnings_date=d,
                    actual_eps=float(ev.eps_actual),
                    estimate_eps=est,
                    surprise_pct=surprise,
                )
            )
        return out

    def signal_from_event(self, ev: PeadEvent) -> PhaseSignal:
        raw = ev.surprise_pct * _SCORE_SCALE
        score = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
        if abs(ev.surprise_pct) < _SURPRISE_THRESHOLD:
            direction = "NEUTRAL"
            score = 0.0
        elif ev.surprise_pct > 0:
            direction = "LONG"
        else:
            direction = "SHORT"
        return PhaseSignal(
            expert=self.name,
            ticker=ev.ticker,
            day=ev.entry_day,
            score=score,
            direction=direction,
            confidence=min(1.0, _CONFIDENCE_BASE + abs(ev.surprise_pct)),
            metadata=(
                ("earnings_date", ev.earnings_date.isoformat()),
                ("actual_eps", f"{ev.actual_eps:.4f}"),
                ("estimate_eps", f"{ev.estimate_eps:.4f}"),
                ("surprise_pct", f"{ev.surprise_pct * 100:.2f}"),
            ),
        )


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


__all__ = ["EPeadExpert", "PeadEvent"]
