from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import structlog

from glostat.data.regime_us_client import (
    RegimeDataError,
    RegimeKey,
    RegimeUsClient,
)
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import YFinanceClient
from glostat.experts.e_regime_us import RegimeUsInputs, score_regime_us
from glostat.replay.metrics import annualized_sharpe, auc_roc

# v1.10 — US regime hindcast harness producing real calibration for
# E_REGIME_US (replaces the n=0 bootstrap shipped in v1.10 wave 1).
#
# WHY: E_REGIME_US bootstraps with AUC=0.50 / n_samples=0 / weight=0 so the
# composite predictor surfaces the regime score in contributing_signals
# without steering p_up. To unlock real weight (INV-GS-103: weight ≥ 0.5
# needs AUC ≥ 0.60), we need a measured AUC + Sharpe over a US universe and
# a defined window. This module produces that measurement bottom-up — pure
# feature compute on yfinance data, no LLM call, snapshot-broker integrated.
#
# Shape mirrors phase_kr_hindcast:
#   - per-day regime score (market-wide — same score for all US tickers)
#   - per-ticker forward-return measurement
#   - basket aggregation: one trade per sample day (mean forward return
#     across the US universe). Avoids the autocorrelation that 50 trades on
#     the same regime score would introduce into AUC/Sharpe.
#   - 30d horizon (matches E_REGIME_US.expires_at default)
#   - skip cleanly when VIX or curve fetch fails so honest skip rates surface
#     in the report.
#
# Deferred: longer-window walk-forward retraining of the score weights;
# composite of E_REGIME_US with E_PEAD across a regime conditional table.

log: Final = structlog.get_logger(__name__)

_DEFAULT_OUTPUT_DIR: Final[Path] = Path("cache") / "hindcast" / "phase_us_regime"
_DEFAULT_HORIZON_DAYS: Final[int] = 30
_DEFAULT_SAMPLE_STRIDE_DAYS: Final[int] = 7
_DEFAULT_SPLIT_RATIO: Final[float] = 0.7
_DEFAULT_OHLCV_PADDING_DAYS: Final[int] = 14
_MIN_BARS_FOR_AUC: Final[int] = 5


@dataclass(frozen=True, slots=True)
class UsRegimeTrade:
    thesis: str
    entry_day: date
    raw_score: float
    direction: str
    forward_return: float
    n_basket: int

    @property
    def label(self) -> int:
        return 1 if self.forward_return > 0.0 else 0

    @property
    def signed_return(self) -> float:
        if self.direction == "LONG":
            return self.forward_return
        if self.direction == "SHORT":
            return -self.forward_return
        return 0.0


@dataclass(frozen=True, slots=True)
class UsRegimeReport:
    thesis: str
    universe: tuple[str, ...]
    period_start: date
    period_end: date
    horizon_days: int
    n_universe: int
    n_evaluated: int
    n_skipped: int
    n_actionable: int
    n_traded: int
    is_auc: float
    oos_auc: float
    overall_auc: float
    is_sharpe: float
    oos_sharpe: float
    overall_sharpe: float
    skip_breakdown: Mapping[str, int] = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def oos_degradation(self) -> float:
        if self.is_sharpe <= 0.0:
            return 1.0
        return max(0.0, 1.0 - (self.oos_sharpe / self.is_sharpe))

    def to_phase1b_payload(self) -> dict[str, object]:
        # WHY: layout matches calibration._calibration_from_phase1b loader so
        # the existing parser ingests this report unmodified.
        return {
            "report": {
                "expert": self.thesis,
                "universe": list(self.universe),
                "period_start": self.period_start.isoformat(),
                "period_end": self.period_end.isoformat(),
                "horizon_days": self.horizon_days,
                "n_universe": self.n_universe,
                "n_signals": self.n_actionable,
                "n_trades": self.n_traded,
                "n_skipped": self.n_skipped,
                "is_sharpe": self.is_sharpe,
                "oos_sharpe": self.oos_sharpe,
                "overall_sharpe": self.overall_sharpe,
                "is_auc": self.is_auc,
                "oos_auc": self.oos_auc,
                "overall_auc": self.overall_auc,
                "skip_breakdown": dict(self.skip_breakdown),
                "notes": list(self.notes),
            }
        }


@dataclass(slots=True)
class _Accumulator:
    thesis: str
    horizon_days: int
    trades: list[UsRegimeTrade] = field(default_factory=list)
    skip_breakdown: dict[str, int] = field(default_factory=dict)
    n_evaluated: int = 0
    n_skipped: int = 0
    n_actionable: int = 0

    def record_skip(self, reason: str) -> None:
        self.n_skipped += 1
        key = (reason or "unknown").strip()[:60]
        self.skip_breakdown[key] = self.skip_breakdown.get(key, 0) + 1

    def record_signal(self, *, day: date, raw_score: float, direction: str,
                      forward_return: float, n_basket: int) -> None:
        self.n_actionable += 1
        self.trades.append(UsRegimeTrade(
            thesis=self.thesis, entry_day=day,
            raw_score=raw_score, direction=direction,
            forward_return=forward_return, n_basket=n_basket,
        ))


def _compute_auc(trades: Sequence[UsRegimeTrade]) -> float:
    if len(trades) < _MIN_BARS_FOR_AUC:
        return 0.5
    scores: list[float] = []
    labels: list[int] = []
    for t in trades:
        sign = 1.0 if t.direction == "LONG" else (
            -1.0 if t.direction == "SHORT" else 0.0
        )
        scores.append(sign * t.raw_score)
        labels.append(t.label)
    return auc_roc(scores, labels)


def _compute_sharpe(
    trades: Sequence[UsRegimeTrade], *, horizon_days: int,
) -> float:
    if len(trades) < 2:
        return 0.0
    rets = [t.signed_return for t in trades if t.direction in {"LONG", "SHORT"}]
    if len(rets) < 2:
        return 0.0
    periods_per_year = max(1, int(252 / max(1, horizon_days)))
    return annualized_sharpe(rets, periods_per_year=periods_per_year)


def _build_report(
    *,
    thesis: str,
    accumulator: _Accumulator,
    universe: Sequence[str],
    period_start: date,
    period_end: date,
    split_ratio: float,
) -> UsRegimeReport:
    trades = sorted(accumulator.trades, key=lambda t: t.entry_day)
    n = len(trades)
    split_idx = int(n * split_ratio)
    is_t, oos_t = trades[:split_idx], trades[split_idx:]
    return UsRegimeReport(
        thesis=thesis,
        universe=tuple(universe),
        period_start=period_start,
        period_end=period_end,
        horizon_days=accumulator.horizon_days,
        n_universe=len(universe),
        n_evaluated=accumulator.n_evaluated,
        n_skipped=accumulator.n_skipped,
        n_actionable=accumulator.n_actionable,
        n_traded=n,
        is_auc=_compute_auc(is_t),
        oos_auc=_compute_auc(oos_t),
        overall_auc=_compute_auc(trades),
        is_sharpe=_compute_sharpe(is_t, horizon_days=accumulator.horizon_days),
        oos_sharpe=_compute_sharpe(oos_t, horizon_days=accumulator.horizon_days),
        overall_sharpe=_compute_sharpe(
            trades, horizon_days=accumulator.horizon_days,
        ),
        skip_breakdown=dict(accumulator.skip_breakdown),
        notes=(
            f"split_ratio={split_ratio:.2f} is={len(is_t)} oos={len(oos_t)}",
            f"skip_rate={accumulator.n_skipped / max(1, accumulator.n_evaluated):.2%}",
            "basket=mean forward return across US universe (1 trade/day)",
        ),
    )


def _sample_days(*, start: date, end: date, stride: int) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur = cur + timedelta(days=stride)
    return out


@dataclass(slots=True)
class PhaseUsRegimeConfig:
    universe_tickers: tuple[str, ...]
    start: date
    end: date
    sample_stride_days: int = _DEFAULT_SAMPLE_STRIDE_DAYS
    split_ratio: float = _DEFAULT_SPLIT_RATIO
    horizon_days: int = _DEFAULT_HORIZON_DAYS


@dataclass(slots=True)
class PhaseUsRegimeResult:
    regime_us: UsRegimeReport


async def run_phase_us_regime_hindcast(
    *,
    config: PhaseUsRegimeConfig,
    snapshot_broker: SnapshotBroker | None = None,
    yf_client: YFinanceClient | None = None,
    regime_client: RegimeUsClient | None = None,
) -> PhaseUsRegimeResult:
    broker = snapshot_broker
    yf = yf_client or YFinanceClient(snapshot_broker=broker)
    regime = regime_client or RegimeUsClient(
        yfinance_client=yf, snapshot_broker=broker,
    )

    acc = _Accumulator(
        thesis="E_REGIME_US", horizon_days=config.horizon_days,
    )

    # Prefetch all regime series ONCE for the full window so per-day point-in-
    # time slicing doesn't re-hit yfinance.
    earliest_as_of = config.start
    try:
        await regime.prefetch(
            keys=tuple(RegimeKey),
            earliest_as_of=earliest_as_of,
        )
    except Exception as exc:
        log.warning("phase_us_regime.prefetch_failed", err=str(exc))

    sample_days = _sample_days(
        start=config.start, end=config.end, stride=config.sample_stride_days,
    )
    today = datetime.now(tz=UTC).date()

    # Cache per-(ticker, day_pair) forward returns so the basket loop reuses them.
    fwd_cache: dict[tuple[str, date], float | None] = {}

    for day in sample_days:
        if day + timedelta(days=config.horizon_days) > today:
            continue
        acc.n_evaluated += 1

        score_signal = await _score_at(regime=regime, day=day)
        if score_signal is None:
            acc.record_skip("regime_data_unavailable")
            continue
        raw_score, direction = score_signal
        if direction == "NEUTRAL":
            acc.record_skip("neutral_direction")
            continue

        # Basket forward return = simple mean of per-ticker forward returns
        # (equal-weighted). NaN-safe: skip tickers with no data.
        basket_fwd = await _basket_forward_return(
            yf=yf, tickers=config.universe_tickers,
            day=day, horizon_days=config.horizon_days,
            cache=fwd_cache,
        )
        if basket_fwd is None:
            acc.record_skip("no_basket_forward_return")
            continue
        fwd_value, n_in_basket = basket_fwd
        acc.record_signal(
            day=day, raw_score=raw_score, direction=direction,
            forward_return=fwd_value, n_basket=n_in_basket,
        )

    report = _build_report(
        thesis="E_REGIME_US", accumulator=acc,
        universe=config.universe_tickers,
        period_start=config.start, period_end=config.end,
        split_ratio=config.split_ratio,
    )
    return PhaseUsRegimeResult(regime_us=report)


async def _score_at(
    *, regime: RegimeUsClient, day: date,
) -> tuple[float, str] | None:
    # Compose the same VIX-term + curve-slope inputs the live expert uses,
    # at point-in-time `as_of=day`. Either leg may fail; only NEUTRAL when
    # both fail.
    vix = None
    with contextlib.suppress(RegimeDataError):
        vix = await regime.get_vix_term(as_of=day)
    curve = None
    with contextlib.suppress(RegimeDataError):
        curve = await regime.get_curve_slope(as_of=day)
    if vix is None and curve is None:
        return None
    score = score_regime_us(RegimeUsInputs(vix_term=vix, curve=curve))
    return score.net_score, score.direction


async def _basket_forward_return(
    *,
    yf: YFinanceClient,
    tickers: Sequence[str],
    day: date,
    horizon_days: int,
    cache: dict[tuple[str, date], float | None],
) -> tuple[float, int] | None:
    end_target = day + timedelta(days=horizon_days)
    rets: list[float] = []
    for ticker in tickers:
        cache_key = (ticker, day)
        if cache_key in cache:
            r = cache[cache_key]
        else:
            r = await _ticker_forward_return(
                yf=yf, ticker=ticker, day=day, end_target=end_target,
            )
            cache[cache_key] = r
        if r is not None:
            rets.append(r)
    if not rets:
        return None
    return sum(rets) / len(rets), len(rets)


async def _ticker_forward_return(
    *, yf: YFinanceClient, ticker: str, day: date, end_target: date,
) -> float | None:
    start = day - timedelta(days=_DEFAULT_OHLCV_PADDING_DAYS)
    end = end_target + timedelta(days=_DEFAULT_OHLCV_PADDING_DAYS + 1)
    try:
        series = await yf.get_ohlcv(ticker, start=start, end=end)
    except Exception as exc:
        log.warning(
            "phase_us_regime.yf_fail",
            ticker=ticker, day=day.isoformat(), err=str(exc),
        )
        return None
    if not series.bars:
        return None
    p0 = _close_on_or_before(series.bars, day)
    p1 = _close_on_or_before(series.bars, end_target)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return (p1 - p0) / p0


def _close_on_or_before(bars: Sequence[object], day: date) -> float | None:
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


def persist_phase_us_regime_reports(
    *,
    result: PhaseUsRegimeResult,
    output_dir: Path | None = None,
) -> Mapping[str, Path]:
    out = output_dir or _DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    report = result.regime_us
    slug = report.thesis.lower()
    path = out / f"{slug}_report.json"
    path.write_text(
        json.dumps(report.to_phase1b_payload(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    paths[report.thesis] = path
    cmp_path = out / "phase_us_regime_summary.md"
    cmp_path.write_text(render_phase_us_regime_summary(result), encoding="utf-8")
    paths["summary"] = cmp_path
    return paths


def render_phase_us_regime_summary(result: PhaseUsRegimeResult) -> str:
    r = result.regime_us
    lines: list[str] = [
        "# Phase US Regime — E_REGIME_US hindcast",
        "",
        "Honest measurement of the US regime expert (VIX term + UST curve)",
        "against an equal-weighted US basket. Basket = 1 trade/day so the same",
        "regime score doesn't double-count across the universe.",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| universe size | {r.n_universe} |",
        f"| evaluated | {r.n_evaluated} |",
        f"| skipped | {r.n_skipped} |",
        f"| actionable | {r.n_actionable} |",
        f"| traded (n) | {r.n_traded} |",
        f"| **AUC (overall)** | {r.overall_auc:.4f} |",
        f"| AUC IS | {r.is_auc:.4f} |",
        f"| AUC OOS | {r.oos_auc:.4f} |",
        f"| **Sharpe (overall)** | {r.overall_sharpe:+.4f} |",
        f"| Sharpe IS | {r.is_sharpe:+.4f} |",
        f"| Sharpe OOS | {r.oos_sharpe:+.4f} |",
        f"| OOS degradation | {r.oos_degradation:.2%} |",
        "",
        "## Notes",
        "",
    ]
    for n in r.notes:
        lines.append(f"- {n}")
    if r.skip_breakdown:
        lines.append("")
        lines.append("## Top skips")
        lines.append("")
        top = sorted(r.skip_breakdown.items(), key=lambda kv: -kv[1])[:8]
        for k, v in top:
            lines.append(f"- {k}: {v}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "PhaseUsRegimeConfig",
    "PhaseUsRegimeResult",
    "UsRegimeReport",
    "UsRegimeTrade",
    "persist_phase_us_regime_reports",
    "render_phase_us_regime_summary",
    "run_phase_us_regime_hindcast",
]
