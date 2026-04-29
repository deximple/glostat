from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import structlog

from glostat.data.ccxt_client import CcxtBinanceClient
from glostat.data.naver_kr_client import KrFlowBar, NaverKrClient
from glostat.experts.e_foreign_reversal import (
    ALL_IN_BPS_KR,
    score_reversal_at,
)
from glostat.experts.e_foreign_reversal import (
    build_verdict as build_kr_verdict,
)
from glostat.experts.e_foreign_reversal import (
    realized_return as kr_realized_return,
)
from glostat.experts.e_funding_carry import (
    ALL_IN_BPS_BINANCE_PERP,
    score_funding_rate,
)
from glostat.experts.e_funding_carry import (
    build_verdict as build_carry_verdict,
)
from glostat.experts.e_funding_carry import (
    realized_return as carry_realized_return,
)
from glostat.replay.metrics import annualized_sharpe, auc_roc, max_drawdown

# Phase 1D standalone hindcast — orthogonal-asset thesis empirical screening.
# Bypasses the GLOSTAT Verdict pipeline because:
#   (1) thesis E7 is on crypto perpetual futures (not US equities)
#   (2) thesis E9 is on KR equities (not in MVP routing)
# Both are exploratory — produce raw metrics + Sprint 4 gate decision per thesis.

log: Final = structlog.get_logger(__name__)

_DEFAULT_OUTPUT_DIR: Final[Path] = Path("cache") / "hindcast" / "phase1d"


@dataclass(frozen=True, slots=True)
class HindcastTrade:
    universe_id: str
    bar_idx: int
    timestamp: datetime
    action: str            # BUY / SELL / HOLD
    direction: str         # LONG / SHORT / NEUTRAL
    edge_bps: float
    cost_passed: bool
    realized_return: float
    pattern: str


@dataclass(frozen=True, slots=True)
class ThesisHindcastReport:
    thesis: str
    universe: tuple[str, ...]
    n_bars_evaluated: int
    n_skip_insufficient: int
    n_neutral: int
    n_actionable: int          # BUY+SELL pre-cost-gate
    n_cost_passed: int
    n_traded: int              # post-cost-gate BUY+SELL
    is_sharpe: float
    oos_sharpe: float
    overall_sharpe: float
    is_auc: float
    oos_auc: float
    overall_auc: float
    overall_maxdd: float
    cost_passed_pct: float
    avg_actionable_return: float
    hit_rate_actionable: float  # fraction of actionable trades with positive return
    pattern_breakdown: dict[str, int] = field(default_factory=dict)
    pattern_hit_rates: dict[str, float] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def oos_degradation(self) -> float:
        if self.is_sharpe <= 0.0:
            return 1.0
        return max(0.0, 1.0 - (self.oos_sharpe / self.is_sharpe))


@dataclass(slots=True)
class _Counters:
    n_bars: int = 0
    n_skip: int = 0
    n_neutral: int = 0
    n_actionable: int = 0
    n_cost_passed: int = 0
    pattern_counts: dict[str, int] = field(default_factory=dict)
    pattern_pos: dict[str, int] = field(default_factory=dict)
    pattern_total: dict[str, int] = field(default_factory=dict)


def _accumulate(
    counters: _Counters, trades: list[HindcastTrade], *,
    score, verdict, actual: float | None, universe_id: str, ts: datetime, idx: int,
) -> None:
    counters.pattern_counts[score.pattern] = counters.pattern_counts.get(score.pattern, 0) + 1
    if score.direction == "NEUTRAL":
        counters.n_neutral += 1
        return
    counters.n_actionable += 1
    if not verdict.cost_passed:
        return
    counters.n_cost_passed += 1
    if actual is None:
        return
    counters.pattern_total[score.pattern] = counters.pattern_total.get(score.pattern, 0) + 1
    if actual > 0:
        counters.pattern_pos[score.pattern] = counters.pattern_pos.get(score.pattern, 0) + 1
    trades.append(HindcastTrade(
        universe_id=universe_id, bar_idx=idx, timestamp=ts,
        action=verdict.action, direction=score.direction,
        edge_bps=verdict.edge_bps, cost_passed=True,
        realized_return=actual, pattern=score.pattern,
    ))


# ---------- E7 Funding Carry hindcast --------------------------------------

async def hindcast_funding_carry(
    *,
    symbols: tuple[str, ...] = ("BTC/USDT:USDT", "ETH/USDT:USDT"),
    start: date,
    end: date,
    split_ratio: float = 0.7,
    cache_dir: Path | None = None,
) -> ThesisHindcastReport:
    since_ms = int(datetime(start.year, start.month, start.day, tzinfo=UTC).timestamp() * 1000)
    until_ms = int(datetime(end.year, end.month, end.day, tzinfo=UTC).timestamp() * 1000)
    trades: list[HindcastTrade] = []
    counters = _Counters()

    async with CcxtBinanceClient(cache_dir=cache_dir) as client:
        for symbol in symbols:
            log.info("phase1d.fetch_funding", symbol=symbol)
            funding_bars = await client.fetch_funding_history_paginated(
                symbol, since_ms=since_ms, until_ms=until_ms, page_limit=1000)
            log.info("phase1d.fetch_ohlcv", symbol=symbol, n_funding=len(funding_bars))
            ohlcv_bars = await client.fetch_ohlcv_paginated(
                symbol, timeframe="8h", since_ms=since_ms, until_ms=until_ms,
                page_limit=1000)
            if not funding_bars or not ohlcv_bars:
                log.warning("phase1d.empty_data", symbol=symbol,
                            n_funding=len(funding_bars), n_ohlcv=len(ohlcv_bars))
                continue
            rates, closes, timestamps = _align_funding_ohlcv(funding_bars, ohlcv_bars)
            if len(rates) < 100:
                log.warning("phase1d.insufficient_aligned", symbol=symbol, n=len(rates))
                continue
            counters.n_bars += len(rates)
            for idx in range(len(rates)):
                score = score_funding_rate(rates, current_idx=idx, lookback=90)
                if score.is_skip:
                    counters.n_skip += 1
                    continue
                verdict = build_carry_verdict(symbol, idx, score)
                actual = carry_realized_return(closes, entry_idx=idx, horizon_bars=3)
                _accumulate(counters, trades, score=score, verdict=verdict,
                            actual=actual, universe_id=symbol, ts=timestamps[idx], idx=idx)

    if not trades:
        return _empty_report("E7_FUNDING_CARRY", symbols, counters,
                             notes=("no actionable post-cost-gate trades collected",))
    trades.sort(key=lambda t: t.timestamp)
    return _build_report(
        thesis="E7_FUNDING_CARRY", universe=symbols, trades=trades,
        counters=counters, split_ratio=split_ratio,
        all_in_bps=ALL_IN_BPS_BINANCE_PERP,
        horizon_per_year=int(365 * 3 / 1))


def _align_funding_ohlcv(funding_bars, ohlcv_bars) -> tuple[list[float], list[float], list[datetime]]:
    # Snap each funding event to the latest OHLCV close at-or-before it.
    ohlcv_by_ts: dict[int, float] = {int(b.ts.timestamp()): b.close for b in ohlcv_bars}
    ohlcv_ts_sorted = sorted(ohlcv_by_ts)
    rates: list[float] = []
    closes: list[float] = []
    timestamps: list[datetime] = []
    for fb in funding_bars:
        fb_ts = int(fb.ts.timestamp())
        close_ts = _last_le(ohlcv_ts_sorted, fb_ts)
        if close_ts is None:
            continue
        rates.append(fb.funding_rate)
        closes.append(ohlcv_by_ts[close_ts])
        timestamps.append(fb.ts)
    return rates, closes, timestamps


def _last_le(sorted_seq: list[int], target: int) -> int | None:
    # binary-search the largest element ≤ target
    import bisect  # noqa: PLC0415

    pos = bisect.bisect_right(sorted_seq, target)
    if pos == 0:
        return None
    return sorted_seq[pos - 1]


# ---------- E9 Foreign Reversal hindcast -----------------------------------

async def hindcast_foreign_reversal(
    *,
    codes: tuple[str, ...],
    start: date,
    end: date,
    split_ratio: float = 0.7,
    max_pages: int = 30,
    use_cache: bool = True,
    cache_dir: Path | None = None,
) -> ThesisHindcastReport:
    client = NaverKrClient(cache_dir=cache_dir)
    trades: list[HindcastTrade] = []
    counters = _Counters()

    for code in codes:
        bars = await _load_or_fetch_kr(client, code, start=start, end=end,
                                       max_pages=max_pages, use_cache=use_cache)
        if bars is None:
            continue
        counters.n_bars += len(bars)
        for idx in range(len(bars)):
            score = score_reversal_at(bars, current_idx=idx, required_prior=4)
            if score.is_skip:
                counters.n_skip += 1
                continue
            verdict = build_kr_verdict(score)
            actual = kr_realized_return(bars, entry_idx=idx, horizon_days=7)
            ts = datetime(bars[idx].bar_date.year, bars[idx].bar_date.month,
                          bars[idx].bar_date.day, tzinfo=UTC)
            _accumulate(counters, trades, score=score, verdict=verdict,
                        actual=actual, universe_id=code, ts=ts, idx=idx)

    if not trades:
        return _empty_report("E9_FOREIGN_REVERSAL", codes, counters,
                             notes=("no actionable post-cost-gate trades collected",))
    trades.sort(key=lambda t: t.timestamp)
    return _build_report(
        thesis="E9_FOREIGN_REVERSAL", universe=codes, trades=trades,
        counters=counters, split_ratio=split_ratio,
        all_in_bps=ALL_IN_BPS_KR,
        horizon_per_year=int(252 / 7))


async def _load_or_fetch_kr(
    client: NaverKrClient, code: str, *, start: date, end: date,
    max_pages: int, use_cache: bool,
) -> list[KrFlowBar] | None:
    bars: list[KrFlowBar] = []
    if use_cache:
        bars = client.load_cached(code)
        log.info("phase1d.kr_cache_loaded", code=code, n=len(bars))
    if not bars or bars[-1].bar_date < end - timedelta(days=14):
        try:
            bars = await client.fetch_history(code, max_pages=max_pages, until_date=start)
            client.save_cache(code, bars)
        except Exception as exc:
            log.warning("phase1d.kr_fetch_failed", code=code, err=str(exc))
            return None
    bars = [b for b in bars if start <= b.bar_date <= end]
    if len(bars) < 30:
        log.warning("phase1d.kr_insufficient", code=code, n=len(bars))
        return None
    return bars


# ---------- Shared report builder ------------------------------------------

def _build_report(
    *, thesis: str, universe: tuple[str, ...], trades: list[HindcastTrade],
    counters: _Counters, split_ratio: float, all_in_bps: float, horizon_per_year: int,
) -> ThesisHindcastReport:
    n = len(trades)
    split_idx = int(n * split_ratio)
    is_t, oos_t = trades[:split_idx], trades[split_idx:]
    pattern_hr = {
        p: counters.pattern_pos.get(p, 0) / counters.pattern_total[p]
        for p in counters.pattern_total if counters.pattern_total[p] > 0
    }
    return ThesisHindcastReport(
        thesis=thesis, universe=universe,
        n_bars_evaluated=counters.n_bars,
        n_skip_insufficient=counters.n_skip,
        n_neutral=counters.n_neutral,
        n_actionable=counters.n_actionable,
        n_cost_passed=counters.n_cost_passed,
        n_traded=n,
        is_sharpe=_sharpe_for(is_t, horizon_per_year),
        oos_sharpe=_sharpe_for(oos_t, horizon_per_year),
        overall_sharpe=_sharpe_for(trades, horizon_per_year),
        is_auc=_auc_for(is_t),
        oos_auc=_auc_for(oos_t),
        overall_auc=_auc_for(trades),
        overall_maxdd=max_drawdown([t.realized_return for t in trades]),
        cost_passed_pct=counters.n_cost_passed / max(1, counters.n_actionable),
        avg_actionable_return=statistics.fmean([t.realized_return for t in trades]),
        hit_rate_actionable=sum(1 for t in trades if t.realized_return > 0) / n,
        pattern_breakdown=counters.pattern_counts,
        pattern_hit_rates=pattern_hr,
        notes=(f"trades={n} is={len(is_t)} oos={len(oos_t)} all_in_bps={all_in_bps:.1f}",),
    )


def _empty_report(
    thesis: str, universe: tuple[str, ...], counters: _Counters,
    *, notes: tuple[str, ...] = (),
) -> ThesisHindcastReport:
    return ThesisHindcastReport(
        thesis=thesis, universe=universe, n_bars_evaluated=counters.n_bars,
        n_skip_insufficient=counters.n_skip, n_neutral=counters.n_neutral,
        n_actionable=counters.n_actionable, n_cost_passed=counters.n_cost_passed,
        n_traded=0, is_sharpe=0.0, oos_sharpe=0.0, overall_sharpe=0.0,
        is_auc=0.5, oos_auc=0.5, overall_auc=0.5, overall_maxdd=0.0,
        cost_passed_pct=0.0, avg_actionable_return=0.0, hit_rate_actionable=0.0,
        notes=notes,
    )


def _sharpe_for(trades: list[HindcastTrade], horizon_per_year: int) -> float:
    if len(trades) < 2:
        return 0.0
    per_trade: list[float] = []
    for t in trades:
        sign = 1.0 if t.action == "BUY" else (-1.0 if t.action == "SELL" else 0.0)
        if sign == 0.0:
            continue
        per_trade.append(sign * t.realized_return)
    if len(per_trade) < 2:
        return 0.0
    return annualized_sharpe(per_trade, periods_per_year=max(1, horizon_per_year))


def _auc_for(trades: list[HindcastTrade]) -> float:
    if not trades:
        return 0.5
    scores: list[float] = []
    labels: list[int] = []
    for t in trades:
        sign = 1.0 if t.action == "BUY" else (-1.0 if t.action == "SELL" else 0.0)
        scores.append(sign * t.edge_bps)
        labels.append(1 if t.realized_return > 0 else 0)
    return auc_roc(scores, labels)


# Rendering helpers live in glostat.replay.phase1d_report (split for ≤400-line cap).
from glostat.replay.phase1d_report import (  # noqa: E402
    persist_phase1d_report,
    render_gate_summary,
    render_report_md,
)

__all__ = [
    "HindcastTrade",
    "ThesisHindcastReport",
    "hindcast_foreign_reversal",
    "hindcast_funding_carry",
    "persist_phase1d_report",
    "render_gate_summary",
    "render_report_md",
]
