from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.data.data_router import to_yfinance_kr_ticker
from glostat.data.naver_kr_client import KrFlowBar
from glostat.data.yfinance_client import YFinanceClient
from glostat.experts.e_foreign_reversal import score_reversal_at
from glostat.experts.e_fundamental_kr import EFundamentalKrExpert
from glostat.experts.e_pead_kr import (
    _DRIFT_GAIN,
    _DRIFT_WINDOW_END,
    _DRIFT_WINDOW_START,
    _SCORE_CLIP,
    _last_expected_earnings_date,
)
from glostat.experts.e_time import ETimeExpert

# v1.2 L1 — per-thesis evaluation helpers used by phase_kr_hindcast orchestrator.
# Split out so phase_kr_hindcast.py stays under the 400-line cap.

log: Final = structlog.get_logger(__name__)

_DEFAULT_OHLCV_PADDING_DAYS: Final[int] = 14


async def forward_return_yfinance(
    yf: YFinanceClient, ticker: str, *, day: date, horizon_days: int,
    padding_days: int = _DEFAULT_OHLCV_PADDING_DAYS,
) -> float | None:
    yf_ticker = to_yfinance_kr_ticker(ticker)
    end_target = day + timedelta(days=horizon_days)
    start = day - timedelta(days=padding_days)
    end = end_target + timedelta(days=padding_days + 1)
    try:
        series = await yf.get_ohlcv(yf_ticker, start=start, end=end)
    except Exception as exc:
        log.warning("phase_kr.yf_fail", ticker=ticker, day=day.isoformat(), err=str(exc))
        return None
    if not series.bars:
        return None
    p0 = close_on_or_before(series.bars, day)
    p1 = close_on_or_before(series.bars, end_target)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return (p1 - p0) / p0


def close_on_or_before(bars: Sequence[object], day: date) -> float | None:
    best: float | None = None
    best_day: date | None = None
    for bar in bars:
        ts = getattr(bar, "ts", None)
        bar_day = ts.date() if hasattr(ts, "date") else ts
        if not isinstance(bar_day, date):
            continue
        if bar_day > day:
            continue
        if best_day is None or bar_day > best_day:
            best_day = bar_day
            best = float(getattr(bar, "close", 0.0))
    return best


def idx_at_or_before(bars_by_date: Mapping[date, int], day: date) -> int | None:
    if not bars_by_date:
        return None
    best: int | None = None
    best_day: date | None = None
    for bd, i in bars_by_date.items():
        if bd > day:
            continue
        if best_day is None or bd > best_day:
            best_day = bd
            best = i
    return best


async def evaluate_fundamental(
    *,
    fundamental: EFundamentalKrExpert,
    code: str, day: date, ts: datetime,
    yf: YFinanceClient, horizon_days: int,
    accumulator,
) -> None:
    accumulator.n_evaluated += 1
    try:
        sig = await fundamental.compute(code, ts)
    except ExpertSkipError as exc:
        accumulator.record_skip(str(exc))
        return
    except Exception as exc:
        accumulator.record_skip(f"unexpected: {exc}")
        return
    fwd = await forward_return_yfinance(
        yf, code, day=day, horizon_days=horizon_days,
    )
    if fwd is None:
        accumulator.record_skip("no_forward_return")
        return
    accumulator.record_signal(
        ticker=code, day=day, raw_score=sig.net_score,
        direction=sig.direction, forward_return=fwd,
    )


async def evaluate_time(
    *,
    time_expert: ETimeExpert,
    code: str, day: date, ts: datetime,
    yf: YFinanceClient, horizon_days: int,
    accumulator,
) -> None:
    accumulator.n_evaluated += 1
    try:
        sig = await time_expert.compute(code, ts)
    except ExpertSkipError as exc:
        accumulator.record_skip(str(exc))
        return
    except Exception as exc:
        accumulator.record_skip(f"unexpected: {exc}")
        return
    fwd = await forward_return_yfinance(
        yf, code, day=day, horizon_days=horizon_days,
    )
    if fwd is None:
        accumulator.record_skip("no_forward_return")
        return
    accumulator.record_signal(
        ticker=code, day=day, raw_score=sig.net_score,
        direction=sig.direction, forward_return=fwd,
    )


def evaluate_foreign_reversal(
    *,
    naver_bars: Sequence[KrFlowBar],
    bars_by_date: Mapping[date, int],
    code: str,
    day: date,
    horizon_days: int,
    accumulator,
) -> None:
    accumulator.n_evaluated += 1
    if not naver_bars:
        accumulator.record_skip("no_naver_bars")
        return
    idx = idx_at_or_before(bars_by_date, day)
    if idx is None or idx < 4:
        accumulator.record_skip("insufficient_history")
        return
    score = score_reversal_at(list(naver_bars), current_idx=idx, required_prior=4)
    if score.is_skip or score.direction == "NEUTRAL":
        accumulator.record_skip(f"pattern_{score.pattern}")
        return
    target_idx = idx + horizon_days
    if target_idx >= len(naver_bars):
        accumulator.record_skip("no_forward_bar")
        return
    p0 = naver_bars[idx].close_price
    p1 = naver_bars[target_idx].close_price
    if p0 <= 0:
        accumulator.record_skip("invalid_price")
        return
    fwd = (p1 - p0) / p0
    accumulator.record_signal(
        ticker=code, day=naver_bars[idx].bar_date, raw_score=score.net_score,
        direction=score.direction, forward_return=fwd,
    )


async def evaluate_pead_kr(
    *,
    code: str,
    day: date,
    yf: YFinanceClient,
    horizon_days: int,
    accumulator: Any,    # _ThesisAccumulator (avoid circular import)
) -> None:
    # v1.6 P5 — KR Post-Earnings Announcement Drift point-in-time hindcast.
    # For each (ticker, day) sample, compute T+5..T+30 OHLCV drift after the
    # most-recent expected filing date (KIFRS Q-end + 45d) that precedes `day`.
    # Skip cleanly when day is too close to the last earnings (drift window
    # not yet observable) or OHLCV bars are missing.
    accumulator.n_evaluated += 1
    last_e = _last_expected_earnings_date(day)
    days_since = (day - last_e).days
    if days_since < _DRIFT_WINDOW_END:
        accumulator.record_skip(
            f"too_close_to_earnings (D+{days_since})"
        )
        return
    drift = await _measure_pead_drift(
        yf=yf, code=code, day=day, last_e=last_e,
    )
    if drift is None:
        accumulator.record_skip("no_drift_window_data")
        return
    raw = max(-_SCORE_CLIP, min(_SCORE_CLIP, drift * _DRIFT_GAIN))
    direction = (
        "LONG" if raw > 0.4 else ("SHORT" if raw < -0.4 else "NEUTRAL")
    )
    fwd = await forward_return_yfinance(
        yf, code, day=day, horizon_days=horizon_days,
    )
    if fwd is None:
        accumulator.record_skip("no_forward_return")
        return
    accumulator.record_signal(
        ticker=code, day=day, raw_score=raw,
        direction=direction, forward_return=fwd,
    )


async def _measure_pead_drift(
    *,
    yf: YFinanceClient,
    code: str,
    day: date,
    last_e: date,
) -> float | None:
    # Fetch OHLCV from `last_e - padding` to `day` (point-in-time: never use
    # bars beyond `day` for the drift calculation, only for forward_return).
    yf_ticker = to_yfinance_kr_ticker(code)
    start = last_e - timedelta(days=_DEFAULT_OHLCV_PADDING_DAYS)
    end = day + timedelta(days=1)   # inclusive of `day`
    try:
        series = await yf.get_ohlcv(yf_ticker, start=start, end=end)
    except Exception as exc:
        log.warning(
            "phase_kr.pead_kr_yf_fail",
            ticker=code, day=day.isoformat(), err=str(exc),
        )
        return None
    if not series.bars:
        return None
    target_t5 = last_e + timedelta(days=_DRIFT_WINDOW_START)
    target_t30 = last_e + timedelta(days=_DRIFT_WINDOW_END)
    close_t5 = _close_on_or_after(series.bars, target_t5)
    close_t30 = _close_on_or_after(series.bars, target_t30)
    if close_t5 is None or close_t30 is None or close_t5 == 0:
        return None
    return (close_t30 - close_t5) / close_t5


def _close_on_or_after(bars: Sequence[object], target: date) -> float | None:
    for bar in bars:
        ts = getattr(bar, "ts", None)
        if ts is None:
            continue
        bar_day = ts.date() if hasattr(ts, "date") else ts
        if not isinstance(bar_day, date):
            continue
        if bar_day < target:
            continue
        close = getattr(bar, "close", None)
        if close is None:
            continue
        return float(close)
    return None


__all__ = [
    "close_on_or_before",
    "evaluate_foreign_reversal",
    "evaluate_fundamental",
    "evaluate_pead_kr",
    "evaluate_time",
    "forward_return_yfinance",
    "idx_at_or_before",
]
