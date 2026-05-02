from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import DataRouter, normalize_kr_ticker, to_yfinance_kr_ticker
from glostat.data.kr_calendar_client import KrCalendarClient
from glostat.data.yfinance_types import OhlcvSeries

# v1.6 P5 — KR Post-Earnings Announcement Drift (PEAD) expert.
#
# WHY (P5 Event-Driven panel finding):
#   v1.5 outputs are "static snapshots, not time-axis predictions". The 30d
#   horizon ignores upcoming earnings dates that drive volatility + drift.
#   This expert measures the ticker's price action in the 5-30 day window
#   AFTER its most recent quarterly-report deadline (KIFRS Q-end + 45d).
#
# Score formula:
#   post_earnings_return = (close[T+30] - close[T+5]) / close[T+5]
#   drift_signal         = clamp(post_earnings_return * 10.0, ±2.0)
#   net_score            = drift_signal
#
# Universe gate: KR equities (XKRX/XKOS); needs ≥30d of OHLCV after
# expected last earnings date. Otherwise ExpertSkipError.

log: Final = structlog.get_logger(__name__)

_DRIFT_GAIN: Final[float] = 10.0       # +20% post-earnings move → +2.0 raw
_SCORE_CLIP: Final[float] = 2.0
_DIRECTION_THRESHOLD: Final[float] = 0.4
_SWING_HORIZON_DAYS: Final[int] = 30
_EARNINGS_FILING_LAG_DAYS: Final[int] = 45     # KIFRS quarterly deadline
_DRIFT_WINDOW_START: Final[int] = 5            # T+5 from filing
_DRIFT_WINDOW_END: Final[int] = 30             # T+30 from filing
_OHLCV_LOOKBACK_DAYS: Final[int] = 120


@dataclass(frozen=True, slots=True)
class PeadKrScore:
    last_earnings_date: str    # ISO date of expected last filing
    days_since_earnings: int
    drift_5_to_30: float
    raw_score: float
    net_score: float

    @property
    def direction(self) -> str:
        if self.net_score > _DIRECTION_THRESHOLD:
            return "LONG"
        if self.net_score < -_DIRECTION_THRESHOLD:
            return "SHORT"
        return "NEUTRAL"

    @property
    def confidence(self) -> float:
        return min(1.0, abs(self.net_score) / _SCORE_CLIP)


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    snapshot_id: str


class EPeadKrExpert:
    """KR Post-Earnings Announcement Drift expert.

    Uses kr_calendar_client to estimate the most-recent expected earnings
    filing date (KIFRS Q-end + 45d), then measures actual OHLCV drift in
    the T+5 → T+30 window. KOSPI 200 / KOSDAQ liquidity required.
    """

    name = "E_PEAD_KR"

    def __init__(
        self,
        *,
        router: DataRouter,
        calendar: KrCalendarClient,
    ) -> None:
        self._router = router
        self._calendar = calendar

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        code = normalize_kr_ticker(ticker)
        sources: list[_Source] = []
        last_earnings = _last_expected_earnings_date(ts.date())
        days_since = (ts.date() - last_earnings).days
        if days_since < _DRIFT_WINDOW_END:
            raise ExpertSkipError(
                f"E_PEAD_KR: only {days_since}d since expected earnings filing "
                f"({last_earnings.isoformat()}); need ≥{_DRIFT_WINDOW_END}d for drift window"
            )
        ohlcv = await self._fetch_ohlcv(code, ts, sources)
        drift, t5_close, t30_close = _compute_drift(ohlcv, last_earnings)
        if drift is None:
            raise ExpertSkipError(
                f"E_PEAD_KR: insufficient OHLCV in T+5..T+30 window for "
                f"{code} after {last_earnings.isoformat()}"
            )
        score = _score(last_earnings, days_since, drift)
        return _build_signal(
            code=code, ts=ts, score=score, sources=sources,
            t5_close=t5_close, t30_close=t30_close,
        )

    async def _fetch_ohlcv(
        self, code: str, ts: datetime, sources: list[_Source],
    ) -> OhlcvSeries:
        client, method = self._router.route("E_PEAD_KR", "ohlcv")
        yf_ticker = to_yfinance_kr_ticker(code)
        end = ts.date()
        start = end - timedelta(days=_OHLCV_LOOKBACK_DAYS)
        try:
            series: OhlcvSeries = await getattr(client, method)(
                yf_ticker, start=start, end=end,
            )
        except Exception as exc:
            raise ExpertSkipError(
                f"E_PEAD_KR: yfinance OHLCV failed for {yf_ticker}: {exc}"
            ) from exc
        snap_id = getattr(client, "last_snapshot_id", None)
        if snap_id is not None:
            sources.append(_Source(name="yfinance.history.kr_pead", snapshot_id=snap_id))
        return series


def _last_expected_earnings_date(today: date) -> date:
    # Most-recent (Q-end + 45d) that is ≤ today.
    q_ends = [
        date(today.year, 3, 31),
        date(today.year, 6, 30),
        date(today.year, 9, 30),
        date(today.year, 12, 31),
    ]
    candidates: list[date] = [
        q + timedelta(days=_EARNINGS_FILING_LAG_DAYS) for q in q_ends
    ]
    # Add prior year's Q4 as the boundary for early-Q1 today.
    candidates.insert(
        0, date(today.year - 1, 12, 31) + timedelta(days=_EARNINGS_FILING_LAG_DAYS),
    )
    past = [d for d in candidates if d <= today]
    return max(past) if past else candidates[0]


def _compute_drift(
    series: OhlcvSeries, last_earnings: date,
) -> tuple[float | None, float | None, float | None]:
    target_t5 = last_earnings + timedelta(days=_DRIFT_WINDOW_START)
    target_t30 = last_earnings + timedelta(days=_DRIFT_WINDOW_END)
    close_t5 = _close_on_or_after(series, target_t5)
    close_t30 = _close_on_or_after(series, target_t30)
    if close_t5 is None or close_t30 is None or close_t5 == 0:
        return None, close_t5, close_t30
    drift = (close_t30 - close_t5) / close_t5
    return drift, close_t5, close_t30


def _close_on_or_after(series: OhlcvSeries, target: date) -> float | None:
    for bar in series.bars:
        if bar.ts.date() >= target and bar.close is not None:
            return float(bar.close)
    return None


def _score(last_earnings: date, days_since: int, drift: float) -> PeadKrScore:
    raw = max(-_SCORE_CLIP, min(_SCORE_CLIP, drift * _DRIFT_GAIN))
    return PeadKrScore(
        last_earnings_date=last_earnings.isoformat(),
        days_since_earnings=days_since,
        drift_5_to_30=drift,
        raw_score=raw,
        net_score=raw,
    )


def _build_signal(
    *,
    code: str,
    ts: datetime,
    score: PeadKrScore,
    sources: list[_Source],
    t5_close: float | None,
    t30_close: float | None,
) -> ExpertSignal:
    basis = (
        f"last_earnings≈{score.last_earnings_date} "
        f"(D+{score.days_since_earnings}); "
        f"drift T+5→T+30 = {score.drift_5_to_30:+.2%}; "
        f"closes ${t5_close} → ${t30_close}; "
        f"net={score.net_score:+.2f}"
    )
    metadata = tuple(sorted({
        "last_earnings_date": score.last_earnings_date,
        "days_since_earnings": str(score.days_since_earnings),
        "drift_5_to_30": f"{score.drift_5_to_30:.6f}",
        "t5_close": str(t5_close) if t5_close is not None else "n/a",
        "t30_close": str(t30_close) if t30_close is not None else "n/a",
        "raw_score": f"{score.raw_score:.4f}",
        "net_score": f"{score.net_score:.4f}",
        "code": code,
    }.items()))
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_pead_kr.synthetic",)
    return ExpertSignal(
        expert_name="E_PEAD_KR",  # type: ignore[arg-type]
        ticker=code,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="continuation",   # post-earnings drift = trend-follow
        basis=basis,
        sources=source_strings,
        expires_at=ts + timedelta(days=_SWING_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "EPeadKrExpert",
    "PeadKrScore",
]
