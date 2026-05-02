from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.data.commodity_client import (
    CommodityClient,
    CommodityDataError,
    CommodityKey,
)
from glostat.data.data_router import to_yfinance_kr_ticker
from glostat.data.naver_kr_client import KrFlowBar
from glostat.data.sector_classifier_kr import (
    CycleClass,
    KrSector,
    cycle_class_of,
    is_refining,
    sector_of,
)
from glostat.data.yfinance_client import YFinanceClient
from glostat.experts.e_commodity_index_kr import (
    _MOMENTUM_GAIN,
    _SUB_SIGNAL_CLIP,
)
from glostat.experts.e_commodity_index_kr import (
    _SCORE_CLIP as _COMMODITY_SCORE_CLIP,
)
from glostat.experts.e_foreign_reversal import score_reversal_at
from glostat.experts.e_fundamental_kr import EFundamentalKrExpert
from glostat.experts.e_fundamental_kr_cyclical import (
    _SECTOR_CYCLE_KEY,
    _SECTOR_EV_EBITDA,
    _W_CYCLE,
    _W_VALUE,
)
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


async def evaluate_fundamental_kr_cyclical(
    *,
    fundamental: EFundamentalKrExpert,
    commodity: CommodityClient,
    code: str,
    day: date,
    ts: datetime,
    yf: YFinanceClient,
    horizon_days: int,
    accumulator: Any,
) -> None:
    # v1.6.2 wave 2: cyclical-sector EV/EBITDA + commodity cycle hindcast.
    # Universe gate: cyclical sectors only (refining, steel, chemicals,
    # shipping, construction, consumer cyclical). Falls through to skip
    # otherwise so the report's skip rate honestly reflects the gate.
    accumulator.n_evaluated += 1
    if cycle_class_of(code) != CycleClass.CYCLICAL:
        accumulator.record_skip(f"not_cyclical_sector ({sector_of(code).value})")
        return
    sector = sector_of(code)
    # Fetch fundamentals via the live expert (yfinance Fundamentals.raw
    # carries enterpriseToEbitda when present).
    try:
        sig = await fundamental.compute(code, ts)
    except ExpertSkipError as exc:
        accumulator.record_skip(f"fundamental_fetch ({exc})")
        return
    except Exception as exc:
        accumulator.record_skip(f"unexpected ({exc})")
        return
    # Pull EV/EBITDA from yfinance.info.raw via the existing expert path.
    ev_ebitda = _ev_ebitda_from_signal(sig)
    cycle_pctile = await _cycle_percentile_for_sector(
        commodity=commodity, sector=sector, as_of=day,
    )
    if cycle_pctile is None:
        accumulator.record_skip("commodity_cycle_unavailable")
        return
    raw = _cyclical_score(sector, ev_ebitda, cycle_pctile)
    direction = (
        "LONG" if raw > 0.5 else ("SHORT" if raw < -0.5 else "NEUTRAL")
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


async def evaluate_commodity_index_kr(
    *,
    commodity: CommodityClient,
    code: str,
    day: date,
    yf: YFinanceClient,
    horizon_days: int,
    accumulator: Any,
) -> None:
    # v1.6.2 wave 2: WTI + crack spread momentum hindcast (refining only).
    accumulator.n_evaluated += 1
    if not is_refining(code):
        accumulator.record_skip(f"not_refining ({sector_of(code).value})")
        return
    try:
        wti = await commodity.get_cycle(CommodityKey.WTI, as_of=day)
        crack = await commodity.get_crack_spread(as_of=day)
    except CommodityDataError as exc:
        accumulator.record_skip(f"commodity_fetch ({exc})")
        return
    wti_signal = max(
        -_SUB_SIGNAL_CLIP, min(_SUB_SIGNAL_CLIP, wti.momentum_30d * _MOMENTUM_GAIN),
    )
    crack_signal = max(
        -_SUB_SIGNAL_CLIP,
        min(_SUB_SIGNAL_CLIP, crack.momentum_30d * _MOMENTUM_GAIN),
    )
    raw = max(
        -_COMMODITY_SCORE_CLIP,
        min(_COMMODITY_SCORE_CLIP, 0.5 * wti_signal + 0.5 * crack_signal),
    )
    direction = (
        "LONG" if raw > 0.3 else ("SHORT" if raw < -0.3 else "NEUTRAL")
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


def _ev_ebitda_from_signal(sig: object) -> float | None:
    # The cyclical expert reads enterpriseToEbitda from Fundamentals.raw.
    # The generic E_FUNDAMENTAL_KR signal doesn't carry EV/EBITDA in metadata,
    # so we conservatively return None — caller falls back to cycle-only score.
    metadata = getattr(sig, "metadata", ())
    for k, v in metadata:
        if k != "ev_ebitda":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            return None
    return None


async def _cycle_percentile_for_sector(
    *, commodity: CommodityClient, sector: KrSector, as_of: date,
) -> float | None:
    if sector == KrSector.REFINING:
        try:
            crack = await commodity.get_crack_spread(as_of=as_of)
        except CommodityDataError:
            return None
        return crack.cycle_percentile
    key = _SECTOR_CYCLE_KEY.get(sector)
    if key is None:
        return None
    try:
        cycle = await commodity.get_cycle(key, as_of=as_of)
    except CommodityDataError:
        return None
    return cycle.cycle_percentile


def _cyclical_score(
    sector: KrSector, ev_ebitda: float | None, cycle_percentile: float,
) -> float:
    median, stddev = _SECTOR_EV_EBITDA.get(sector, (7.0, 3.0))
    ev_ebitda_z = (
        0.0 if ev_ebitda is None
        else (ev_ebitda - median) / max(stddev, 1e-3)
    )
    cycle_term = cycle_percentile - 0.5
    raw = -_W_VALUE * ev_ebitda_z + _W_CYCLE * (-cycle_term * 2.0)
    return max(-3.0, min(3.0, raw))


__all__ = [
    "close_on_or_before",
    "evaluate_commodity_index_kr",
    "evaluate_foreign_reversal",
    "evaluate_fundamental",
    "evaluate_fundamental_kr_cyclical",
    "evaluate_pead_kr",
    "evaluate_time",
    "forward_return_yfinance",
    "idx_at_or_before",
]
