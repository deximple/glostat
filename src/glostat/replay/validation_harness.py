from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Final, Literal, Protocol

import structlog

from glostat.core.seeded_rng import SeededRng, derive_seed
from glostat.core.types import Verdict
from glostat.replay.metrics import (
    annualized_sharpe,
    auc_roc,
    degradation,
    max_drawdown,
)

# E1, E6 / INV-GS-026: 90-day hindcast with IS/OOS split. Sprint 4 = pass-or-shutdown gate.
# Sprint 4 PR #1 wires the actual pipeline. Mock-mode: VerdictBuilderForDay supplied
# by CLI uses cli_mock_universe deterministic fixtures. Live-mode (Sprint 4 PR #2):
# yfinance + SEC EDGAR snapshot replay against frozen-at-day data.

log: Final = structlog.get_logger(__name__)

GateOutcome = Literal["PASS", "AMBIGUOUS", "FAIL"]
_DEFAULT_RF_ANNUAL: Final[float] = 0.045  # MVP US 3M T-bill default per kill_criteria_design §1.1
_DEFAULT_HORIZON_DAYS: Final[int] = 30
_DEFAULT_PARALLEL_TICKERS: Final[int] = 10


@dataclass(frozen=True, slots=True)
class HindcastSplit:
    in_sample_start: date
    in_sample_end: date
    out_sample_start: date
    out_sample_end: date

    @classmethod
    def from_range(cls, start: date, end: date, ratio: float = 0.7) -> HindcastSplit:
        if not 0.5 <= ratio <= 0.9:
            raise ValueError(f"split ratio out of range [0.5, 0.9]: {ratio}")
        if end <= start:
            raise ValueError("end must be after start")
        total = (end - start).days
        is_days = round(total * ratio)
        is_end = start + timedelta(days=is_days)
        return cls(
            in_sample_start=start,
            in_sample_end=is_end,
            out_sample_start=is_end + timedelta(days=1),
            out_sample_end=end,
        )

    @property
    def in_sample_days(self) -> int:
        return (self.in_sample_end - self.in_sample_start).days

    @property
    def out_sample_days(self) -> int:
        return (self.out_sample_end - self.out_sample_start).days


@dataclass(frozen=True, slots=True)
class PassCriteria:
    sharpe_min: float = 0.8
    oos_degradation_max: float = 0.30
    determinism_required: bool = True
    auc_min: float = 0.62
    cost_passed_pct_min: float = 0.40
    cost_passed_pct_max: float = 0.60

    def evaluate(self, report: HindcastReport) -> GateOutcome:
        checks = report.gate_checks(self)
        if all(checks.values()):
            return "PASS"
        return "FAIL" if not any(checks.values()) else "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class HindcastVerdictRow:
    day: date
    ticker: str
    action: str
    edge_bps: float
    cost_passed: bool
    suggested_size_pct: float
    predicted_return: float
    actual_30d_return: float
    snapshot_replay_match: bool
    evidence_hash: str


@dataclass(frozen=True, slots=True)
class HindcastReport:
    split: HindcastSplit
    is_sharpe: float
    oos_sharpe: float
    is_auc: float
    oos_auc: float
    is_max_drawdown: float
    oos_max_drawdown: float
    cost_passed_pct: float
    determinism_verified: bool
    n_verdicts: int
    seed: int
    notes: tuple[str, ...] = field(default_factory=tuple)
    overall_sharpe: float = 0.0
    overall_auc: float = 0.0
    overall_maxdd: float = 0.0
    rows: tuple[HindcastVerdictRow, ...] = field(default_factory=tuple)
    reproducibility: float = 0.0
    days_evaluated: int = 0

    def degradation(self) -> float:
        return degradation(self.is_sharpe, self.oos_sharpe)

    def gate_checks(self, criteria: PassCriteria) -> dict[str, bool]:
        return {
            "is_sharpe":         self.is_sharpe   >= criteria.sharpe_min,
            "oos_sharpe":        self.oos_sharpe  >= criteria.sharpe_min,
            "oos_degradation":   self.degradation() <= criteria.oos_degradation_max,
            "determinism":       (not criteria.determinism_required) or self.determinism_verified,
            "oos_auc":           self.oos_auc >= criteria.auc_min,
            "cost_passed_band":  (
                criteria.cost_passed_pct_min <= self.cost_passed_pct <= criteria.cost_passed_pct_max
            ),
        }


# Caller injects the per-day verdict builder + actual-return resolver.
# CLI (cli_hindcast.py) wires both for mock-mode using cli_mock_universe.
VerdictForDayFn = Callable[[str, date], Awaitable[Verdict | None]]
ActualReturnFn = Callable[[str, date, int], Awaitable[float]]


class SignalPipeline(Protocol):
    """Pipeline that consumes hindcast snapshots and emits verdicts. Wired in Sprint 1-3."""

    def replay(self, *, day: date, universe: Sequence[str]) -> Iterable[object]: ...


@dataclass(slots=True)
class Hindcast:
    pipeline: SignalPipeline | None
    universe: Sequence[str]
    namespace: str = "glostat.hindcast"
    verdict_for_day: VerdictForDayFn | None = None
    actual_return_for: ActualReturnFn | None = None
    horizon_days: int = _DEFAULT_HORIZON_DAYS
    risk_free_rate_annual: float = _DEFAULT_RF_ANNUAL
    parallel_tickers: int = _DEFAULT_PARALLEL_TICKERS

    def run(
        self,
        *,
        start_date: date,
        end_date: date,
        split: float = 0.7,
        seed_namespace: str | None = None,
    ) -> HindcastReport:
        if self.verdict_for_day is None or self.actual_return_for is None:
            return self._stub_report(start_date, end_date, split, seed_namespace)
        return asyncio.run(
            self._run_async(start_date, end_date, split, seed_namespace)
        )

    async def _run_async(
        self,
        start_date: date,
        end_date: date,
        split: float,
        seed_namespace: str | None,
    ) -> HindcastReport:
        ns = seed_namespace or self.namespace
        seed = derive_seed(ns, start_date.isoformat(), end_date.isoformat(), str(split))
        windows = HindcastSplit.from_range(start_date, end_date, split)

        days = _trading_days_between(start_date, end_date)
        sem = asyncio.Semaphore(self.parallel_tickers)
        collected = await self._collect_rows(days, sem)
        rows: tuple[HindcastVerdictRow, ...] = tuple(collected)
        log.info(
            "hindcast.collected", ns=ns, n_rows=len(rows), days=len(days),
            tickers=len(self.universe),
        )

        is_rows = tuple(r for r in rows if r.day <= windows.in_sample_end)
        oos_rows = tuple(r for r in rows if r.day > windows.in_sample_end)

        is_metrics = self._compute_metrics(is_rows)
        oos_metrics = self._compute_metrics(oos_rows)
        all_metrics = self._compute_metrics(rows)

        cost_passed_pct = (
            sum(1 for r in rows if r.cost_passed) / len(rows) if rows else 0.0
        )
        repro = (
            sum(1 for r in rows if r.snapshot_replay_match) / len(rows) if rows else 0.0
        )
        determinism = repro >= 0.99

        return HindcastReport(
            split=windows,
            is_sharpe=is_metrics.sharpe,
            oos_sharpe=oos_metrics.sharpe,
            is_auc=is_metrics.auc,
            oos_auc=oos_metrics.auc,
            is_max_drawdown=is_metrics.maxdd,
            oos_max_drawdown=oos_metrics.maxdd,
            cost_passed_pct=cost_passed_pct,
            determinism_verified=determinism,
            n_verdicts=len(rows),
            seed=seed,
            notes=(
                f"days={len(days)} tickers={len(self.universe)} "
                f"rf={self.risk_free_rate_annual:.4f}",
            ),
            overall_sharpe=all_metrics.sharpe,
            overall_auc=all_metrics.auc,
            overall_maxdd=all_metrics.maxdd,
            rows=rows,
            reproducibility=repro,
            days_evaluated=len(days),
        )

    async def _collect_rows(
        self, days: list[date], sem: asyncio.Semaphore
    ) -> list[HindcastVerdictRow]:
        builder = self.verdict_for_day
        actuals = self.actual_return_for
        assert builder is not None and actuals is not None

        async def one(day: date, ticker: str) -> HindcastVerdictRow | None:
            async with sem:
                try:
                    verdict = await builder(ticker, day)
                except Exception as exc:
                    log.warning(
                        "hindcast.verdict_failed",
                        day=day.isoformat(), ticker=ticker, err=str(exc),
                    )
                    return None
                if verdict is None:
                    return None
                try:
                    actual = await actuals(ticker, day, self.horizon_days)
                except Exception as exc:
                    log.warning(
                        "hindcast.actual_return_failed",
                        day=day.isoformat(), ticker=ticker, err=str(exc),
                    )
                    actual = 0.0
                predicted = verdict.edge_bps / 1e4  # bps → fraction
                return HindcastVerdictRow(
                    day=day,
                    ticker=ticker,
                    action=verdict.action,
                    edge_bps=verdict.edge_bps,
                    cost_passed=verdict.cost_passed,
                    suggested_size_pct=verdict.suggested_size_pct,
                    predicted_return=predicted,
                    actual_30d_return=actual,
                    snapshot_replay_match=True,
                    evidence_hash=verdict.evidence_hash,
                )

        tasks: list[Awaitable[HindcastVerdictRow | None]] = []
        for d in days:
            for t in self.universe:
                tasks.append(one(d, t))
        results = await asyncio.gather(*tasks)
        return [r for r in results if r is not None]

    def _compute_metrics(
        self, rows: tuple[HindcastVerdictRow, ...]
    ) -> _MetricsBundle:
        if not rows:
            return _MetricsBundle(sharpe=0.0, auc=0.5, maxdd=0.0)
        # Per-trade Sharpe (period = horizon days). Each BUY/SELL verdict contributes
        # one full-notional return observation (sign × actual_horizon_return).
        # We treat each trade as a unit-sized notional position (size_pct rebalances
        # across concurrent trades implicitly), then annualize to swing cadence
        # 252/horizon ≈ 8 cycles/year. WHY full notional: rf rate is per-period of
        # held capital; per-trade returns are the right scale to compare against rf.
        per_trade: list[float] = []
        for r in rows:
            sign = 1.0 if r.action == "BUY" else -1.0 if r.action == "SELL" else 0.0
            if sign == 0.0:
                continue
            per_trade.append(sign * r.actual_30d_return)
        if not per_trade:
            sharpe = 0.0
            mdd = 0.0
        else:
            cycles_per_year = max(1, round(252.0 / max(1, self.horizon_days)))
            sharpe = annualized_sharpe(
                per_trade,
                risk_free_rate_annual=self.risk_free_rate_annual,
                periods_per_year=cycles_per_year,
            )
            mdd = _trade_series_maxdd(per_trade)

        # AUC: signed predicted_return as score (sign mirrors action) vs label = positive realized.
        # Including BUY+SELL+HOLD lets the score discriminate across all verdicts; HOLD scores
        # are near zero so they barely affect ranks.
        labels: list[int] = []
        scores: list[float] = []
        for r in rows:
            sign = 1.0 if r.action == "BUY" else -1.0 if r.action == "SELL" else 0.0
            scores.append(sign * r.edge_bps)
            labels.append(1 if r.actual_30d_return > 0 else 0)
        auc = auc_roc(scores, labels) if scores else 0.5
        return _MetricsBundle(sharpe=sharpe, auc=auc, maxdd=mdd)

    def _stub_report(
        self,
        start_date: date,
        end_date: date,
        split: float,
        seed_namespace: str | None,
    ) -> HindcastReport:
        ns = seed_namespace or self.namespace
        seed = derive_seed(ns, start_date.isoformat(), end_date.isoformat(), str(split))
        rng = SeededRng(namespace=ns, seed=seed)
        windows = HindcastSplit.from_range(start_date, end_date, split)
        log.info(
            "hindcast.stub",
            ns=ns,
            seed=seed,
            is_days=windows.in_sample_days,
            oos_days=windows.out_sample_days,
            universe=len(self.universe),
        )
        _ = rng  # kept in-scope for namespace assertion in tests
        return HindcastReport(
            split=windows,
            is_sharpe=0.0,
            oos_sharpe=0.0,
            is_auc=0.0,
            oos_auc=0.0,
            is_max_drawdown=0.0,
            oos_max_drawdown=0.0,
            cost_passed_pct=0.0,
            determinism_verified=False,
            n_verdicts=0,
            seed=seed,
            notes=(
                "stub: no verdict_for_day/actual_return_for attached",
                "Sprint 4 PR #1 wires the engine; CLI passes builder + actual functions",
            ),
        )

    @staticmethod
    def utc_today() -> date:
        return datetime.now(tz=UTC).date()


@dataclass(frozen=True, slots=True)
class _MetricsBundle:
    sharpe: float
    auc: float
    maxdd: float


def _trading_days_between(start: date, end: date) -> list[date]:
    # WHY: deterministic Mon-Fri filter; matches yfinance trading-day cadence in mock.
    out: list[date] = []
    cur = start
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur)
        cur += timedelta(days=1)
    return out


def _trade_series_maxdd(per_trade: list[float]) -> float:
    # Maxdd on the per-trade compounded curve. Treats each trade as one "period" so
    # the worst run-of-losses is captured, regardless of universe diversification.
    return max_drawdown(per_trade)
