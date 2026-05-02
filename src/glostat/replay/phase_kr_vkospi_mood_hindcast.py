from __future__ import annotations

import contextlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import structlog

from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.vkospi_client import VkospiClient, VkospiDataError
from glostat.data.vkospi_resolvers import (
    KospiSmallCapResolver,
    YFinanceReturnResolver,
)
from glostat.data.yfinance_client import YFinanceClient
from glostat.experts.e_vkospi_mood_kr import (
    SmallCapResolver,
    VkospiMoodInputs,
    score_vkospi_mood,
)
from glostat.replay.metrics import annualized_sharpe, auc_roc

# v1.10.8 — phase_kr_vkospi_mood hindcast harness.
#
# Lifts E_VKOSPI_MOOD_KR from bootstrap (n=0, weight=0) to a measured
# entry. Mirrors phase_us_regime_hindcast shape:
#   - per-event-day, per-ticker scoring against the same VKOSPI series
#   - 20d forward-return horizon (paper headline window)
#   - basket aggregation: 1 trade/event-day = mean forward return across
#     all aligned tickers on that day. Prevents the same ΔVKOSPI from
#     autocorrelating across the cross-section.
#   - IS/OOS split (default 0.7) for honest degradation measurement
#   - phase1b-shaped JSON output so calibration loader picks it up
#
# What the harness scores at each (ticker, day):
#   1. Daily simple return r_t at `day` from yfinance
#   2. ΔVKOSPI on `day` from VkospiClient (CSV-backed via attach_csv_provider)
#   3. small_cap classification from yfinance market_cap
#   4. score_vkospi_mood(VkospiMoodInputs(r_t, Δpct, small_cap))
#   5. If aligned and direction=LONG, record forward 20d return
#
# Skip categories surfaced in skip_breakdown:
#   - return_unavailable: yfinance returned no recent close
#   - vkospi_unavailable: CSV provider missing or out-of-range
#   - below_threshold: |r_t| < 10% (paper trigger gate)
#   - misaligned_or_neutral: regime classified but direction == NEUTRAL
#   - no_forward_return: yfinance forward window empty
#
# Caveats inherited from INV-GS-134 (12 caveats in expert docstring):
#   - Round-trip costs NOT applied (paper-comparable raw alpha)
#   - Look-ahead: r_t at `day` close, ΔVKOSPI at `day` close —
#     entry implicitly at t+1 open. Acceptable for IS/OOS measurement;
#     live execution must shift entry by 1 day.

log: Final = structlog.get_logger(__name__)

_DEFAULT_OUTPUT_DIR: Final[Path] = (
    Path("cache") / "hindcast" / "phase_kr_vkospi_mood"
)
_DEFAULT_HORIZON_DAYS: Final[int] = 20
_DEFAULT_SAMPLE_STRIDE_DAYS: Final[int] = 1   # paper triggers on every |r|>10%
_DEFAULT_SPLIT_RATIO: Final[float] = 0.7
_DEFAULT_OHLCV_PADDING_DAYS: Final[int] = 14
_MIN_BARS_FOR_AUC: Final[int] = 5


@dataclass(frozen=True, slots=True)
class VkospiMoodTrade:
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
class VkospiMoodReport:
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
    trades: list[VkospiMoodTrade] = field(default_factory=list)
    skip_breakdown: dict[str, int] = field(default_factory=dict)
    n_evaluated: int = 0
    n_skipped: int = 0
    n_actionable: int = 0

    def record_skip(self, reason: str) -> None:
        self.n_skipped += 1
        key = (reason or "unknown").strip()[:60]
        self.skip_breakdown[key] = self.skip_breakdown.get(key, 0) + 1

    def record_signal(
        self, *, day: date, raw_score: float, direction: str,
        forward_return: float, n_basket: int,
    ) -> None:
        self.n_actionable += 1
        self.trades.append(VkospiMoodTrade(
            thesis=self.thesis, entry_day=day,
            raw_score=raw_score, direction=direction,
            forward_return=forward_return, n_basket=n_basket,
        ))


def _compute_auc(trades: Sequence[VkospiMoodTrade]) -> float:
    if len(trades) < _MIN_BARS_FOR_AUC:
        return 0.5
    scores = [
        (1.0 if t.direction == "LONG" else
         -1.0 if t.direction == "SHORT" else 0.0) * t.raw_score
        for t in trades
    ]
    labels = [t.label for t in trades]
    return auc_roc(scores, labels)


def _compute_sharpe(
    trades: Sequence[VkospiMoodTrade], *, horizon_days: int,
) -> float:
    if len(trades) < 2:
        return 0.0
    rets = [t.signed_return for t in trades if t.direction in {"LONG", "SHORT"}]
    if len(rets) < 2:
        return 0.0
    periods_per_year = max(1, int(252 / max(1, horizon_days)))
    return annualized_sharpe(rets, periods_per_year=periods_per_year)


def _build_report(
    *, thesis: str, accumulator: _Accumulator,
    universe: Sequence[str],
    period_start: date, period_end: date, split_ratio: float,
) -> VkospiMoodReport:
    trades = sorted(accumulator.trades, key=lambda t: t.entry_day)
    n = len(trades)
    split_idx = int(n * split_ratio)
    is_t, oos_t = trades[:split_idx], trades[split_idx:]
    return VkospiMoodReport(
        thesis=thesis,
        universe=tuple(universe),
        period_start=period_start, period_end=period_end,
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
            "basket=mean forward return across aligned tickers per day",
            "trigger=|r_t|>10% AND aligned ΔVKOSPI sign",
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
class PhaseKrVkospiConfig:
    universe_tickers: tuple[str, ...]
    start: date
    end: date
    sample_stride_days: int = _DEFAULT_SAMPLE_STRIDE_DAYS
    split_ratio: float = _DEFAULT_SPLIT_RATIO
    horizon_days: int = _DEFAULT_HORIZON_DAYS


@dataclass(slots=True)
class PhaseKrVkospiResult:
    vkospi_mood_kr: VkospiMoodReport


async def run_phase_kr_vkospi_mood_hindcast(
    *,
    config: PhaseKrVkospiConfig,
    vkospi_client: VkospiClient,
    snapshot_broker: SnapshotBroker | None = None,
    yf_client: YFinanceClient | None = None,
    small_cap_resolver: SmallCapResolver | None = None,
) -> PhaseKrVkospiResult:
    yf = yf_client or YFinanceClient(snapshot_broker=snapshot_broker)
    return_resolver = YFinanceReturnResolver(yf_client=yf)
    small_cap = small_cap_resolver or KospiSmallCapResolver(yf_client=yf)

    acc = _Accumulator(
        thesis="E_VKOSPI_MOOD_KR", horizon_days=config.horizon_days,
    )
    sample_days = _sample_days(
        start=config.start, end=config.end,
        stride=config.sample_stride_days,
    )
    today = datetime.now(tz=UTC).date()
    forward_cache: dict[tuple[str, date], float | None] = {}

    for day in sample_days:
        if day + timedelta(days=config.horizon_days) > today:
            continue
        acc.n_evaluated += 1
        await _process_event_day(
            day=day, config=config,
            vkospi_client=vkospi_client, yf=yf,
            return_resolver=return_resolver, small_cap=small_cap,
            forward_cache=forward_cache, acc=acc,
        )

    report = _build_report(
        thesis="E_VKOSPI_MOOD_KR", accumulator=acc,
        universe=config.universe_tickers,
        period_start=config.start, period_end=config.end,
        split_ratio=config.split_ratio,
    )
    return PhaseKrVkospiResult(vkospi_mood_kr=report)


async def _resolve_delta_pct(
    *, vkospi_client: VkospiClient, day: date,
) -> float | None:
    with contextlib.suppress(VkospiDataError):
        delta = await vkospi_client.get_delta_at(day)
        return delta.pct_change
    return None


async def _process_event_day(
    *,
    day: date,
    config: PhaseKrVkospiConfig,
    vkospi_client: VkospiClient,
    yf: YFinanceClient,
    return_resolver: YFinanceReturnResolver,
    small_cap: SmallCapResolver,
    forward_cache: dict[tuple[str, date], float | None],
    acc: _Accumulator,
) -> None:
    delta_pct = await _resolve_delta_pct(
        vkospi_client=vkospi_client, day=day,
    )
    if delta_pct is None:
        acc.record_skip("vkospi_unavailable")
        return

    aligned: list[tuple[str, float, float]] = []
    counts = {"below_threshold": 0, "misaligned": 0, "return_failures": 0}

    for code in config.universe_tickers:
        result = await _score_one_ticker(
            code=code, day=day, delta_pct=delta_pct,
            return_resolver=return_resolver, small_cap=small_cap,
            yf=yf, horizon_days=config.horizon_days,
            forward_cache=forward_cache,
        )
        if result is None:
            counts["return_failures"] += 1
        elif result == "below_threshold":
            counts["below_threshold"] += 1
        elif result == "misaligned":
            counts["misaligned"] += 1
        else:
            aligned.append(result)

    for _ in range(counts["return_failures"]):
        acc.record_skip("return_unavailable")
    for _ in range(counts["below_threshold"]):
        acc.record_skip("below_threshold")
    for _ in range(counts["misaligned"]):
        acc.record_skip("misaligned_or_neutral")

    if not aligned:
        return

    # Basket trade: average raw_score + mean forward return → 1 trade/day.
    # WHY: prevents the shared ΔVKOSPI from creating correlated samples
    # across the cross-section.
    avg_score = sum(s for _, s, _ in aligned) / len(aligned)
    avg_fwd = sum(f for _, _, f in aligned) / len(aligned)
    acc.record_signal(
        day=day, raw_score=avg_score, direction="LONG",
        forward_return=avg_fwd, n_basket=len(aligned),
    )


async def _score_one_ticker(
    *,
    code: str,
    day: date,
    delta_pct: float,
    return_resolver: YFinanceReturnResolver,
    small_cap: SmallCapResolver,
    yf: YFinanceClient,
    horizon_days: int,
    forward_cache: dict[tuple[str, date], float | None],
) -> tuple[str, float, float] | str | None:
    """Score a single ticker on a given event day.

    Returns:
      tuple (code, raw_score, forward_return) when the ticker fires LONG.
      "below_threshold" / "misaligned" string sentinel for those skip cases.
      None when the return fetch or forward-return fetch fails.
    """
    r_t = await return_resolver.get_recent_daily_return(code, day)
    if r_t is None:
        return None
    small = await small_cap.is_small_cap(code, day)
    inputs = VkospiMoodInputs(
        return_t=r_t, delta_pct=delta_pct, small_cap=small,
    )
    score = score_vkospi_mood(inputs)
    if score.regime == "below_threshold":
        return "below_threshold"
    if score.direction != "LONG":
        return "misaligned"
    fwd = await _ticker_forward_return(
        yf=yf, code=code, day=day,
        horizon_days=horizon_days, cache=forward_cache,
    )
    if fwd is None:
        return None
    return code, score.raw_score, fwd


async def _ticker_forward_return(
    *, yf: YFinanceClient, code: str, day: date,
    horizon_days: int, cache: dict[tuple[str, date], float | None],
) -> float | None:
    cache_key = (code, day)
    if cache_key in cache:
        return cache[cache_key]
    from glostat.data.data_router import to_yfinance_kr_ticker  # noqa: PLC0415
    yf_ticker = to_yfinance_kr_ticker(code)
    end_target = day + timedelta(days=horizon_days)
    start = day - timedelta(days=_DEFAULT_OHLCV_PADDING_DAYS)
    end = end_target + timedelta(days=_DEFAULT_OHLCV_PADDING_DAYS + 1)
    try:
        series = await yf.get_ohlcv(yf_ticker, start=start, end=end)
    except Exception as exc:
        log.warning(
            "phase_kr_vkospi.yf_fail",
            code=code, day=day.isoformat(), err=str(exc),
        )
        cache[cache_key] = None
        return None
    if not series.bars:
        cache[cache_key] = None
        return None
    p0 = _close_on_or_before(series.bars, day)
    p1 = _close_on_or_before(series.bars, end_target)
    if p0 is None or p1 is None or p0 <= 0:
        cache[cache_key] = None
        return None
    fwd = (p1 - p0) / p0
    cache[cache_key] = fwd
    return fwd


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


def persist_phase_kr_vkospi_reports(
    *, result: PhaseKrVkospiResult, output_dir: Path | None = None,
) -> Mapping[str, Path]:
    out = output_dir or _DEFAULT_OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    report = result.vkospi_mood_kr
    slug = report.thesis.lower()
    path = out / f"{slug}_report.json"
    path.write_text(
        json.dumps(report.to_phase1b_payload(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    paths[report.thesis] = path
    summary_path = out / "phase_kr_vkospi_summary.md"
    summary_path.write_text(
        render_phase_kr_vkospi_summary(result), encoding="utf-8",
    )
    paths["summary"] = summary_path
    return paths


def render_phase_kr_vkospi_summary(result: PhaseKrVkospiResult) -> str:
    r = result.vkospi_mood_kr
    lines: list[str] = [
        "# Phase KR VKOSPI Mood — E_VKOSPI_MOOD_KR hindcast",
        "",
        "Honest measurement of the Lee/Son/Lee 2024 thesis on KOSPI 200.",
        "Trigger: |r_t|>10% AND aligned ΔVKOSPI sign. Basket = 1 trade/day",
        "(mean forward return across all aligned tickers) so the shared",
        "ΔVKOSPI doesn't autocorrelate across the cross-section.",
        "",
        "| metric | value |",
        "|---|---:|",
        f"| universe size | {r.n_universe} |",
        f"| evaluated days | {r.n_evaluated} |",
        f"| skipped (cumulative) | {r.n_skipped} |",
        f"| actionable basket-days | {r.n_actionable} |",
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
        lines.append("## Top skip categories")
        lines.append("")
        top = sorted(r.skip_breakdown.items(), key=lambda kv: -kv[1])[:8]
        for k, v in top:
            lines.append(f"- {k}: {v}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "PhaseKrVkospiConfig",
    "PhaseKrVkospiResult",
    "VkospiMoodReport",
    "VkospiMoodTrade",
    "persist_phase_kr_vkospi_reports",
    "render_phase_kr_vkospi_summary",
    "run_phase_kr_vkospi_mood_hindcast",
]
