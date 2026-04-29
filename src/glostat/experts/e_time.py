from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import DataRouter
from glostat.data.yfinance_types import EarningsCalendar, OhlcvSeries
from glostat.experts.ichimoku import (
    compute_time_convergence_t,
    find_anchor_lows,
)

# E_TIME — TITAN B2 일목 기본수치 + earnings calendar Expert.
# Inputs:
#   yfinance OHLCV (last ~300 trading days; covers 257-day base safely)
#   yfinance earnings calendar (next event + recent history)
# Logic (PR #2 §3):
#   anchor = 257-day low date
#   T (time convergence) ∈ {0, 1.0, 1.5, 2.0} from compute_time_convergence_t
#   earnings_proximity = +0.5 if days_to_earnings ∈ [0, 14] else 0.0
#   net_score = clip(T × 0.75 + earnings_proximity, -3, +3)
#   direction = LONG if score > 1.0; SHORT if < -1.0; else NEUTRAL
#   confidence = min(T / 2.0, 1.0)
# INV-GS-008 hook: metadata["bonus_eligible_T"] = (T ≥ 1.5) — verdict_builder
# applies × 1.2 bonus once V (E_VALUATION) lands and signals V ≥ 1.0.

log: Final = structlog.get_logger(__name__)

_OHLCV_LOOKBACK_TRADING_DAYS: Final[int] = 300
_OHLCV_LOOKBACK_CALENDAR_DAYS: Final[int] = 420  # ~300 trading days incl. weekends
_ANCHOR_LOOKBACK_CALENDAR_DAYS: Final[int] = 257
_T_WEIGHT: Final[float] = 0.75
# Sprint 5 PR #1 — earnings window relaxed 14d → 30d, bonus reduced 0.5 → 0.3.
# Wider window catches more pre-earnings setups but with a smaller per-event
# magnitude so the earnings_proximity factor doesn't dominate T.
_EARNINGS_PRE_BONUS: Final[float] = 0.3
_EARNINGS_PRE_WINDOW_DAYS: Final[int] = 30
_DIRECTION_THRESHOLD: Final[float] = 1.0
_SCORE_CLIP: Final[float] = 3.0
_T_BONUS_THRESHOLD: Final[float] = 1.5
_SWING_HORIZON_DAYS: Final[int] = 30
# Sprint 4 PR #3 — minimum bars required to evaluate Ichimoku anchors. Below
# 200 the 257-day base is structurally impossible (no anchor lows) so emit
# ExpertSkipError instead of silently scoring t=0.
_MIN_OHLCV_BARS: Final[int] = 200


@dataclass(frozen=True, slots=True)
class TimeScore:
    t_value: float
    matched_bases: tuple[int, ...]
    earnings_proximity: float
    days_to_earnings: int | None
    net_score: float
    raw_score: float = 0.0

    @property
    def direction(self) -> str:
        if self.net_score > _DIRECTION_THRESHOLD:
            return "LONG"
        if self.net_score < -_DIRECTION_THRESHOLD:
            return "SHORT"
        return "NEUTRAL"

    @property
    def confidence(self) -> float:
        return min(self.t_value / 2.0, 1.0)

    @property
    def bonus_eligible_t(self) -> bool:
        return self.t_value >= _T_BONUS_THRESHOLD


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    snapshot_id: str
    ts: datetime


class ETimeExpert:
    name = "E_TIME"

    def __init__(self, *, router: DataRouter) -> None:
        self._router = router

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        ticker = ticker.upper().strip()
        sources: list[_Source] = []
        ohlcv = await self._fetch_ohlcv(ticker, ts, sources)
        # Sprint 4 PR #3: fail-fast guards. PR #2's `t=0.0` log spam came from
        # (1) tickers with < 200 trading days of bars and (2) tickers whose
        # 257-day window had no anchor lows. Both produced silent zero scores
        # diluting Sharpe. Surface as ExpertSkipError so the harness can
        # exclude or partial-build the verdict honestly.
        if ohlcv is None or len(ohlcv) < _MIN_OHLCV_BARS:
            n_bars = 0 if ohlcv is None else len(ohlcv)
            raise ExpertSkipError(
                f"E_TIME: insufficient OHLCV ({n_bars} bars < {_MIN_OHLCV_BARS}) "
                f"for {ticker}@{ts.date().isoformat()}"
            )
        calendar = await self._fetch_earnings_calendar(ticker, sources)
        score = self._score(ohlcv, calendar, ts.date())
        # Sprint 5 PR #1: anchors-empty + no-earnings is now a valid neutral
        # signal (score=0, NEUTRAL) rather than a skip. The relaxed ±7-day
        # convergence window already lifts E_TIME hit rate; treating the rare
        # no-signal case as silence-with-evidence avoids dropping verdicts that
        # E_FUNDAMENTAL or E_FUND_FLOW could still build from.
        return _build_signal(
            ticker=ticker,
            ts=ts,
            score=score,
            sources=sources,
        )

    async def _fetch_ohlcv(
        self, ticker: str, ts: datetime, sources: list[_Source]
    ) -> OhlcvSeries | None:
        client, method = self._router.route(self.name, "ohlcv")
        end_d = ts.date()
        start_d = end_d - timedelta(days=_OHLCV_LOOKBACK_CALENDAR_DAYS)
        try:
            result: OhlcvSeries = await getattr(client, method)(
                ticker, start=start_d, end=end_d
            )
        except Exception as exc:
            log.warning("e_time.ohlcv_fetch_failed", ticker=ticker, err=str(exc))
            return None
        snap_id = getattr(client, "last_snapshot_id", None)
        if snap_id is not None:
            sources.append(
                _Source(
                    name="yfinance.history",
                    snapshot_id=snap_id,
                    ts=datetime.now(tz=UTC),
                )
            )
        return result

    async def _fetch_earnings_calendar(
        self, ticker: str, sources: list[_Source]
    ) -> EarningsCalendar | None:
        try:
            client, method = self._router.route(self.name, "earnings_calendar")
        except Exception as exc:
            log.warning("e_time.earnings_route_failed", err=str(exc))
            return None
        try:
            result: EarningsCalendar = await getattr(client, method)(ticker)
        except Exception as exc:
            log.warning("e_time.earnings_fetch_failed", ticker=ticker, err=str(exc))
            return None
        snap_id = getattr(client, "last_snapshot_id", None)
        if snap_id is not None:
            sources.append(
                _Source(
                    name="yfinance.calendar",
                    snapshot_id=snap_id,
                    ts=datetime.now(tz=UTC),
                )
            )
        return result

    def _score(
        self,
        ohlcv: OhlcvSeries | None,
        calendar: EarningsCalendar | None,
        today: date,
    ) -> TimeScore:
        t, matched, anchors = _compute_t_with_anchor(ohlcv, today)
        days_to, earnings_p = _compute_earnings_proximity(calendar, today)
        raw = t * _T_WEIGHT + earnings_p
        net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
        log.debug(
            "e_time.score",
            t=t, matched=matched, anchors=[a.isoformat() for a in anchors],
            days_to=days_to, earnings_p=earnings_p, net=net,
        )
        return TimeScore(
            t_value=t,
            matched_bases=tuple(matched),
            earnings_proximity=earnings_p,
            days_to_earnings=days_to,
            net_score=net,
            raw_score=raw,
        )


def _compute_t_with_anchor(
    ohlcv: OhlcvSeries | None, today: date
) -> tuple[float, list[int], list[date]]:
    if ohlcv is None or len(ohlcv) == 0:
        return (0.0, [], [])
    bars = [(b.ts.date(), b.close) for b in ohlcv.bars]
    anchors = find_anchor_lows(bars, lookback_days=_ANCHOR_LOOKBACK_CALENDAR_DAYS)
    if not anchors:
        return (0.0, [], [])
    t, matched = compute_time_convergence_t(today, anchors)
    return (t, matched, anchors)


def _compute_earnings_proximity(
    calendar: EarningsCalendar | None, today: date
) -> tuple[int | None, float]:
    if calendar is None or not calendar.upcoming:
        return (None, 0.0)
    next_evt = next(
        (e for e in calendar.upcoming if e.earnings_date.date() >= today), None
    )
    if next_evt is None:
        return (None, 0.0)
    days_to = (next_evt.earnings_date.date() - today).days
    if 0 <= days_to <= _EARNINGS_PRE_WINDOW_DAYS:
        return (days_to, _EARNINGS_PRE_BONUS)
    return (days_to, 0.0)


def _build_signal(
    *,
    ticker: str,
    ts: datetime,
    score: TimeScore,
    sources: list[_Source],
) -> ExpertSignal:
    matched_str = ", ".join(str(n) for n in score.matched_bases) or "none"
    days_str = (
        f"in {score.days_to_earnings}d" if score.days_to_earnings is not None
        else "n/a"
    )
    basis = (
        f"T={score.t_value:.1f} ({len(score.matched_bases)} converge: [{matched_str}]), "
        f"earnings {days_str}"
    )
    archetype = (
        "continuation"
        if score.t_value * _T_WEIGHT >= score.earnings_proximity
        else "impulse"
    )
    metadata: tuple[tuple[str, str], ...] = tuple(
        sorted(
            {
                "t_value": f"{score.t_value:.4f}",
                "matched_bases": ",".join(str(n) for n in score.matched_bases),
                "earnings_proximity": f"{score.earnings_proximity:.4f}",
                "days_to_earnings": (
                    str(score.days_to_earnings)
                    if score.days_to_earnings is not None else "n/a"
                ),
                "net_score": f"{score.net_score:.4f}",
                "raw_score": f"{score.raw_score:.4f}",
                "bonus_eligible_T": str(score.bonus_eligible_t),
                "weight_T": f"{_T_WEIGHT}",
                "earnings_pre_bonus": f"{_EARNINGS_PRE_BONUS}",
                "earnings_pre_window_days": f"{_EARNINGS_PRE_WINDOW_DAYS}",
                "direction_threshold": f"{_DIRECTION_THRESHOLD}",
            }.items()
        )
    )
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_time.synthetic",)
    return ExpertSignal(
        expert_name="E_TIME",
        ticker=ticker,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype=archetype,  # type: ignore[arg-type]
        basis=basis,
        sources=source_strings,
        expires_at=ts + timedelta(days=_SWING_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = ["ETimeExpert", "TimeScore"]
