from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

import structlog

from glostat.core.types import MarketMeta, Verdict
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.universe import Universe
from glostat.replay.live_hindcast import (
    LiveActualReturnFetcher,
    LiveHindcastVerdictBuilder,
    SecEdgarUserAgentError,
    make_live_components,
    summarize_network,
)
from glostat.replay.validation_harness import Hindcast, HindcastReport

# Sprint 4 PR #2 — live hindcast plumbing extracted from cli_hindcast.py to keep
# both files under the 400-line house rule. Owns:
#   - progress reporter (stderr every N verdicts)
#   - live verdict + actual fetcher wiring
#   - SecEdgarClient lifecycle (aclose at run completion)

log: Final = structlog.get_logger(__name__)

_PROGRESS_EVERY: Final[int] = 10


class ProgressReporter:
    def __init__(self, *, total: int, label: str) -> None:
        self._total = max(1, total)
        self._label = label
        self._done = 0
        self._start = time.monotonic()

    def step(self) -> None:
        self._done += 1
        if self._done % _PROGRESS_EVERY != 0 and self._done != self._total:
            return
        elapsed = time.monotonic() - self._start
        rate = self._done / max(1e-9, elapsed)
        remaining = max(0.0, (self._total - self._done) / max(1e-9, rate))
        print(
            f"[glostat] {self._label} {self._done}/{self._total} "
            f"(elapsed {elapsed:0.1f}s, eta {remaining:0.1f}s)",
            file=sys.stderr,
        )


def trading_days_count(start: date, end: date) -> int:
    cur = start
    count = 0
    while cur <= end:
        if cur.weekday() < 5:
            count += 1
        cur += timedelta(days=1)
    return count


def wrap_with_progress(
    builder: LiveHindcastVerdictBuilder,
    progress: ProgressReporter,
) -> Any:
    async def verdict_for_day(ticker: str, day: date) -> Verdict | None:
        try:
            return await builder(ticker, day)
        finally:
            progress.step()

    return verdict_for_day


def wrap_actual(fetcher: LiveActualReturnFetcher) -> Any:
    async def actual_for(ticker: str, day: date, horizon: int) -> float:
        result = await fetcher(ticker, day, horizon)
        # WHY: harness expects float; None → 0.0 so the row is recorded but
        # contributes nothing to per-trade Sharpe (the harness already excludes
        # action=HOLD from per-trade returns). The dropped count surfaces in the
        # network summary so the report quantifies the missing-future-data gap.
        return 0.0 if result is None else result

    return actual_for


def run_live_hindcast(
    *,
    market_meta: MarketMeta,
    horizon_days: int,
    tickers: Sequence[str],
    start_date: date,
    end_date: date,
    split: float,
    parallel_tickers: int,
    snapshot_root: Path,
    actual_cache: Path,
    universe: Universe | None = None,
    sector_stats_cache: Path | None = None,
) -> dict[str, Any]:
    broker = SnapshotBroker(root=snapshot_root)
    aborted_reason: str | None = None
    report: HindcastReport | None = None
    summary: dict[str, Any] = {}
    builder: LiveHindcastVerdictBuilder | None = None
    fetcher: LiveActualReturnFetcher | None = None
    sec_client: Any = None
    try:
        try:
            builder, fetcher, sec_client = make_live_components(
                market_meta=market_meta,
                horizon_days=horizon_days,
                snapshot_broker=broker,
                actual_cache_path=actual_cache,
                universe=universe,
                sector_stats_cache_path=sector_stats_cache,
            )
        except Exception as exc:
            aborted_reason = f"live setup failed: {exc}"
            return {"report": None, "summary": {}, "aborted_reason": aborted_reason}
        progress = ProgressReporter(
            total=len(tickers) * trading_days_count(start_date, end_date),
            label="verdict",
        )
        verdict_for_day = wrap_with_progress(builder, progress)
        actual_for = wrap_actual(fetcher)
        hc = Hindcast(
            pipeline=None,
            universe=tickers,
            verdict_for_day=verdict_for_day,
            actual_return_for=actual_for,
            horizon_days=horizon_days,
            parallel_tickers=max(1, int(parallel_tickers)),
        )
        try:
            report = _run_with_close(hc, sec_client, start_date, end_date, split)
        except SecEdgarUserAgentError as exc:
            aborted_reason = str(exc)
        except Exception as exc:
            aborted_reason = f"hindcast.run unexpected: {exc}"
    finally:
        if fetcher is not None:
            try:
                fetcher.persist()
            except Exception as exc:
                log.warning("live_actual.persist_failed", err=str(exc))
        broker.close()

    if builder is not None and fetcher is not None:
        summary = summarize_network(builder, fetcher)
    return {"report": report, "summary": summary, "aborted_reason": aborted_reason}


def _run_with_close(
    hc: Hindcast,
    sec_client: Any,
    start_date: date,
    end_date: date,
    split: float,
) -> HindcastReport:
    # WHY: hc.run owns its own asyncio loop; aclose must run inside that loop or
    # the underlying httpx client warns. Detour through one coroutine so both
    # run + close share a single event loop.
    async def _runner() -> HindcastReport:
        try:
            return await hc._run_async(
                start_date=start_date, end_date=end_date,
                split=split, seed_namespace=None,
            )
        finally:
            try:
                await sec_client.aclose()
            except Exception as exc:
                log.warning("live_sec.aclose_failed", err=str(exc))

    return asyncio.run(_runner())


__all__ = [
    "ProgressReporter",
    "run_live_hindcast",
    "trading_days_count",
    "wrap_actual",
    "wrap_with_progress",
]
