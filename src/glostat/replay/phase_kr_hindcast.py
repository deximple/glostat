from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Final

import structlog

from glostat.data.commodity_client import CommodityClient, CommodityKey
from glostat.data.data_router import DataRouter
from glostat.data.naver_kr_client import KrFlowBar, NaverKrClient
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_client import YFinanceClient
from glostat.experts.e_fundamental_kr import EFundamentalKrExpert
from glostat.experts.e_time import ETimeExpert
from glostat.replay.metrics import annualized_sharpe, auc_roc
from glostat.replay.phase_kr_eval import (
    evaluate_commodity_index_kr,
    evaluate_foreign_reversal,
    evaluate_fundamental,
    evaluate_fundamental_kr_cyclical,
    evaluate_pead_kr,
    evaluate_time,
)

# v1.2 L1 — KR hindcast harness producing real (not bootstrapped) calibration.
#
# WHY: v1.1 K1 shipped E_FUNDAMENTAL_KR / E_FOREIGN_REVERSAL / E_TIME (KR mode)
# with a bootstrapped synthetic calibration (n=0 → weight=0). This module runs
# the three experts across the configurable KR universe + window and writes
# per-thesis JSON reports (auc, sharpe, n, oos_degradation) that the existing
# load_calibration() in calibration.py can ingest. Real calibration unlocks
# the composite predictor for KR tickers without faking measurement.
#
# Design trade-offs:
#   - bottom-up (no LLM call) — pure feature compute on Naver flows + yfinance
#     OHLCV, mirrors phase1d_hindcast pattern.
#   - per-day per-ticker tick: skip cleanly when expert raises ExpertSkipError
#     so honest skip rates surface in the report.
#   - 7d horizon for E_FOREIGN_REVERSAL (matches its existing horizon),
#     30d horizon for E_FUNDAMENTAL_KR / E_TIME (swing-position fundamentals).

log: Final = structlog.get_logger(__name__)

_DEFAULT_OUTPUT_DIR: Final[Path] = Path("cache") / "hindcast" / "phase_kr"
_DEFAULT_HORIZON_FUNDAMENTAL: Final[int] = 30
_DEFAULT_HORIZON_TIME: Final[int] = 30
_DEFAULT_HORIZON_REVERSAL: Final[int] = 7
_DEFAULT_HORIZON_PEAD: Final[int] = 30          # v1.6 P5
_DEFAULT_HORIZON_CYCLICAL: Final[int] = 30      # v1.6.2 wave 2
_DEFAULT_HORIZON_COMMODITY: Final[int] = 30     # v1.6.2 wave 2
_DEFAULT_SPLIT_RATIO: Final[float] = 0.7
_DEFAULT_SAMPLE_STRIDE_DAYS: Final[int] = 7
_DEFAULT_OHLCV_PADDING_DAYS: Final[int] = 14
_MIN_BARS_FOR_AUC: Final[int] = 5


@dataclass(frozen=True, slots=True)
class KrHindcastTrade:
    thesis: str
    ticker: str
    entry_day: date
    raw_score: float
    direction: str
    forward_return: float

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
class KrThesisReport:
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
        # WHY: layout matches calibration._calibration_from_phase1b loader so the
        # existing parser ingests this report unmodified.
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
class _ThesisAccumulator:
    thesis: str
    horizon_days: int
    trades: list[KrHindcastTrade] = field(default_factory=list)
    skip_breakdown: dict[str, int] = field(default_factory=dict)
    n_evaluated: int = 0
    n_skipped: int = 0
    n_actionable: int = 0

    def record_skip(self, reason: str) -> None:
        self.n_skipped += 1
        # Bucket by first 60 chars of reason so similar messages collapse.
        key = (reason or "unknown").strip()[:60]
        self.skip_breakdown[key] = self.skip_breakdown.get(key, 0) + 1

    def record_signal(self, *, ticker: str, day: date, raw_score: float,
                       direction: str, forward_return: float) -> None:
        self.n_actionable += 1
        self.trades.append(KrHindcastTrade(
            thesis=self.thesis, ticker=ticker, entry_day=day,
            raw_score=raw_score, direction=direction,
            forward_return=forward_return,
        ))


def _compute_auc(trades: Sequence[KrHindcastTrade]) -> float:
    if len(trades) < _MIN_BARS_FOR_AUC:
        return 0.5
    scores: list[float] = []
    labels: list[int] = []
    for t in trades:
        sign = 1.0 if t.direction == "LONG" else (-1.0 if t.direction == "SHORT" else 0.0)
        scores.append(sign * t.raw_score)
        labels.append(t.label)
    return auc_roc(scores, labels)


def _compute_sharpe(trades: Sequence[KrHindcastTrade], *, horizon_days: int) -> float:
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
    accumulator: _ThesisAccumulator,
    universe: Sequence[str],
    period_start: date,
    period_end: date,
    split_ratio: float,
) -> KrThesisReport:
    trades = sorted(accumulator.trades, key=lambda t: (t.entry_day, t.ticker))
    n = len(trades)
    split_idx = int(n * split_ratio)
    is_t, oos_t = trades[:split_idx], trades[split_idx:]
    return KrThesisReport(
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
        overall_sharpe=_compute_sharpe(trades, horizon_days=accumulator.horizon_days),
        skip_breakdown=dict(accumulator.skip_breakdown),
        notes=(
            f"split_ratio={split_ratio:.2f} is={len(is_t)} oos={len(oos_t)}",
            f"skip_rate={accumulator.n_skipped / max(1, accumulator.n_evaluated):.2%}",
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
class PhaseKrHindcastConfig:
    universe_tickers: tuple[str, ...]
    start: date
    end: date
    sample_stride_days: int = _DEFAULT_SAMPLE_STRIDE_DAYS
    split_ratio: float = _DEFAULT_SPLIT_RATIO
    horizon_fundamental: int = _DEFAULT_HORIZON_FUNDAMENTAL
    horizon_time: int = _DEFAULT_HORIZON_TIME
    horizon_reversal: int = _DEFAULT_HORIZON_REVERSAL
    horizon_pead: int = _DEFAULT_HORIZON_PEAD   # v1.6 P5
    horizon_cyclical: int = _DEFAULT_HORIZON_CYCLICAL    # v1.6.2 wave 2
    horizon_commodity: int = _DEFAULT_HORIZON_COMMODITY  # v1.6.2 wave 2
    max_concurrent: int = 5


@dataclass(slots=True)
class PhaseKrHindcastResult:
    fundamental_kr: KrThesisReport
    time_kr: KrThesisReport
    foreign_reversal: KrThesisReport
    pead_kr: KrThesisReport                       # v1.6 P5
    fundamental_kr_cyclical: KrThesisReport       # v1.6.2 wave 2
    commodity_index_kr: KrThesisReport            # v1.6.2 wave 2
    skipped_tickers: tuple[str, ...]


async def run_phase_kr_hindcast(
    *,
    config: PhaseKrHindcastConfig,
    snapshot_broker: SnapshotBroker | None = None,
    naver_client: NaverKrClient | None = None,
    yf_client: YFinanceClient | None = None,
) -> PhaseKrHindcastResult:
    broker = snapshot_broker
    yf = yf_client or YFinanceClient(snapshot_broker=broker)
    naver = naver_client or NaverKrClient()
    router = DataRouter()
    router.register_client("yfinance", yf)
    router.register_client("naver_kr", naver)
    fundamental = EFundamentalKrExpert(router=router)
    time_expert = ETimeExpert(router=router)
    # v1.6.2 wave 2: shared commodity client (per-process cache + point-in-time
    # slicing). Prefetched once per commodity needed across the whole run.
    commodity_client = CommodityClient(
        yfinance_client=yf, snapshot_broker=broker,
    )
    # WHY: REVERSAL is computed pure-functionally below from cached Naver bars
    # via score_reversal_at, so we don't need the live EForeignReversalExpert
    # wrapper here. The wrapper is the predict-time surface; the hindcast walks
    # bars deterministically.

    fund_acc = _ThesisAccumulator(
        thesis="E_FUNDAMENTAL_KR", horizon_days=config.horizon_fundamental,
    )
    time_acc = _ThesisAccumulator(
        thesis="E_TIME_KR", horizon_days=config.horizon_time,
    )
    rev_acc = _ThesisAccumulator(
        thesis="E_FOREIGN_REVERSAL", horizon_days=config.horizon_reversal,
    )
    pead_acc = _ThesisAccumulator(    # v1.6 P5
        thesis="E_PEAD_KR", horizon_days=config.horizon_pead,
    )
    cyclical_acc = _ThesisAccumulator(    # v1.6.2 wave 2
        thesis="E_FUNDAMENTAL_KR_CYCLICAL", horizon_days=config.horizon_cyclical,
    )
    commodity_acc = _ThesisAccumulator(    # v1.6.2 wave 2
        thesis="E_COMMODITY_INDEX_KR", horizon_days=config.horizon_commodity,
    )
    skipped_tickers: list[str] = []

    # v1.6.2 wave 2: prefetch commodity series ONCE for the full window so
    # per-(ticker,day) point-in-time slicing doesn't re-hit yfinance.
    earliest_as_of = config.start
    try:
        await commodity_client.prefetch(
            keys=tuple(CommodityKey),
            earliest_as_of=earliest_as_of,
        )
    except Exception as exc:
        log.warning("phase_kr.commodity_prefetch_failed", err=str(exc))

    sample_days = _sample_days(
        start=config.start, end=config.end, stride=config.sample_stride_days,
    )
    semaphore = asyncio.Semaphore(max(1, config.max_concurrent))

    async def process_ticker(code: str) -> None:
        async with semaphore:
            await _process_one_ticker(
                code=code, sample_days=sample_days,
                yf=yf, naver=naver,
                fundamental=fundamental, time_expert=time_expert,
                commodity=commodity_client,
                fund_acc=fund_acc, time_acc=time_acc, rev_acc=rev_acc,
                pead_acc=pead_acc,
                cyclical_acc=cyclical_acc,
                commodity_acc=commodity_acc,
                skipped_tickers=skipped_tickers,
                horizon_fundamental=config.horizon_fundamental,
                horizon_time=config.horizon_time,
                horizon_reversal=config.horizon_reversal,
                horizon_pead=config.horizon_pead,
                horizon_cyclical=config.horizon_cyclical,
                horizon_commodity=config.horizon_commodity,
            )

    tasks = [process_ticker(t) for t in config.universe_tickers]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=False)

    fund_report = _build_report(
        thesis="E_FUNDAMENTAL_KR", accumulator=fund_acc,
        universe=config.universe_tickers,
        period_start=config.start, period_end=config.end,
        split_ratio=config.split_ratio,
    )
    time_report = _build_report(
        thesis="E_TIME_KR", accumulator=time_acc,
        universe=config.universe_tickers,
        period_start=config.start, period_end=config.end,
        split_ratio=config.split_ratio,
    )
    rev_report = _build_report(
        thesis="E_FOREIGN_REVERSAL", accumulator=rev_acc,
        universe=config.universe_tickers,
        period_start=config.start, period_end=config.end,
        split_ratio=config.split_ratio,
    )
    pead_report = _build_report(    # v1.6 P5
        thesis="E_PEAD_KR", accumulator=pead_acc,
        universe=config.universe_tickers,
        period_start=config.start, period_end=config.end,
        split_ratio=config.split_ratio,
    )
    cyclical_report = _build_report(    # v1.6.2 wave 2
        thesis="E_FUNDAMENTAL_KR_CYCLICAL", accumulator=cyclical_acc,
        universe=config.universe_tickers,
        period_start=config.start, period_end=config.end,
        split_ratio=config.split_ratio,
    )
    commodity_report = _build_report(    # v1.6.2 wave 2
        thesis="E_COMMODITY_INDEX_KR", accumulator=commodity_acc,
        universe=config.universe_tickers,
        period_start=config.start, period_end=config.end,
        split_ratio=config.split_ratio,
    )
    return PhaseKrHindcastResult(
        fundamental_kr=fund_report,
        time_kr=time_report,
        foreign_reversal=rev_report,
        pead_kr=pead_report,
        fundamental_kr_cyclical=cyclical_report,
        commodity_index_kr=commodity_report,
        skipped_tickers=tuple(sorted(set(skipped_tickers))),
    )


async def _process_one_ticker(
    *,
    code: str,
    sample_days: Sequence[date],
    yf: YFinanceClient,
    naver: NaverKrClient,
    fundamental: EFundamentalKrExpert,
    time_expert: ETimeExpert,
    commodity: CommodityClient,
    fund_acc: _ThesisAccumulator,
    time_acc: _ThesisAccumulator,
    rev_acc: _ThesisAccumulator,
    pead_acc: _ThesisAccumulator,
    cyclical_acc: _ThesisAccumulator,
    commodity_acc: _ThesisAccumulator,
    skipped_tickers: list[str],
    horizon_fundamental: int,
    horizon_time: int,
    horizon_reversal: int,
    horizon_pead: int,
    horizon_cyclical: int,
    horizon_commodity: int,
) -> None:
    naver_bars: list[KrFlowBar] = []
    try:
        cached = naver.load_cached(code)
        if cached:
            naver_bars = cached
        else:
            naver_bars = await naver.fetch_history(code, max_pages=30)
            if naver_bars:
                naver.save_cache(code, naver_bars)
    except Exception as exc:
        log.info("phase_kr.naver_skip", code=code, err=str(exc))
        skipped_tickers.append(code)
    bars_by_date = {b.bar_date: i for i, b in enumerate(naver_bars)}

    today = datetime.now(tz=UTC).date()
    for day in sample_days:
        if day + timedelta(days=horizon_fundamental) > today:
            continue
        ts = datetime(day.year, day.month, day.day, 12, 0, tzinfo=UTC)

        await evaluate_fundamental(
            fundamental=fundamental, code=code, day=day, ts=ts, yf=yf,
            horizon_days=horizon_fundamental, accumulator=fund_acc,
        )
        await evaluate_time(
            time_expert=time_expert, code=code, day=day, ts=ts, yf=yf,
            horizon_days=horizon_time, accumulator=time_acc,
        )
        evaluate_foreign_reversal(
            naver_bars=naver_bars, bars_by_date=bars_by_date,
            code=code, day=day, horizon_days=horizon_reversal,
            accumulator=rev_acc,
        )
        # v1.6 P5: KR Post-Earnings Announcement Drift point-in-time hindcast.
        await evaluate_pead_kr(
            code=code, day=day, yf=yf,
            horizon_days=horizon_pead, accumulator=pead_acc,
        )
        # v1.6.2 wave 2: cyclical-sector + refining-momentum point-in-time
        # hindcast. Universe gates inside the evaluators decide skip vs fire.
        await evaluate_fundamental_kr_cyclical(
            fundamental=fundamental, commodity=commodity,
            code=code, day=day, ts=ts, yf=yf,
            horizon_days=horizon_cyclical, accumulator=cyclical_acc,
        )
        await evaluate_commodity_index_kr(
            commodity=commodity, code=code, day=day, yf=yf,
            horizon_days=horizon_commodity, accumulator=commodity_acc,
        )


# Helpers split out for ≤400-line cap; re-export for call-site stability.
from glostat.replay.phase_kr_report import (  # noqa: E402
    persist_phase_kr_reports,
    render_phase_kr_comparison,
)

__all__ = [
    "KrHindcastTrade",
    "KrThesisReport",
    "PhaseKrHindcastConfig",
    "PhaseKrHindcastResult",
    "persist_phase_kr_reports",
    "render_phase_kr_comparison",
    "run_phase_kr_hindcast",
]
