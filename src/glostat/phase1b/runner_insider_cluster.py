from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Final

import structlog

from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.experts.e_insider_cluster import EInsiderClusterExpert
from glostat.phase1b.hindcast_runner import HindcastConfig, build_report, make_trade_row
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseHindcastReport, PhaseTradeRow

# Thesis E6 hindcast — Russell 2000 insider cluster + low-coverage.
# Triggered events: each unique date with >= 3 insider buyers in trailing 14d.
# Universe filter: tickers with >= 1 Form 4 buy in the lookback window.

log: Final = structlog.get_logger(__name__)

_HORIZON_DAYS: Final[int] = 30
# SEC throttle = 10 req/sec, enforced at client level. Per-ticker form4 fetcher
# uses parallel=3, so total in-flight ≈ _PARALLEL_WARM * 3 → keep modest.
_PARALLEL_WARM: Final[int] = 2


async def run_insider_cluster_hindcast(
    *,
    universe_with_cik: Sequence[tuple[str, str]],
    sec_client: SecEdgarClient,
    cache: PriceCache,
    start: date,
    end: date,
    horizon_days: int = _HORIZON_DAYS,
    cluster_threshold: int = 3,
    window_days: int = 14,
) -> PhaseHindcastReport:
    # v1.10.5 — accept cluster_threshold + window_days so re-hindcast can
    # relax gating (default 3 buyers in 14d → optionally 2 buyers in 14d
    # or 3 in 21d) to grow n from 11 above the 50-sample activation floor.
    expert = EInsiderClusterExpert(
        sec_client=sec_client,
        cluster_threshold=cluster_threshold,
        window_days=window_days,
    )
    sem = asyncio.Semaphore(_PARALLEL_WARM)

    async def warm(ticker: str, cik: str) -> tuple[str, int]:
        async with sem:
            n = await expert.warm_cache(ticker, cik, days_back=860)
            return ticker, n

    warmed = await asyncio.gather(*(warm(t, c) for t, c in universe_with_cik))
    n_with_data = sum(1 for _, n in warmed if n > 0)
    log.info("insider.warmed", n_tickers=len(warmed), with_data=n_with_data)

    # Enumerate candidate signal dates: each ticker's distinct buy dates.
    rows: list[PhaseTradeRow] = []
    n_attempted = 0
    n_skipped = 0

    for ticker, _ in universe_with_cik:
        candidate_days = expert.cluster_event_dates(ticker)
        for day in candidate_days:
            if day < start or day > end:
                continue
            n_attempted += 1
            sig = expert.signal_at(ticker, day)
            if sig.direction == "NEUTRAL":
                continue
            await cache.get(ticker)
            fwd = cache.forward_return(ticker, day, horizon_days)
            if fwd is None:
                n_skipped += 1
                continue
            rows.append(
                make_trade_row(
                    day=day,
                    ticker=ticker,
                    score=sig.score,
                    direction=sig.direction,
                    actual_fwd_return=fwd,
                )
            )

    config = HindcastConfig(
        expert="E_INSIDER_CLUSTER",
        universe_size=len(universe_with_cik),
        n_signals_attempted=n_attempted,
        n_signals_skipped=n_skipped,
        horizon_days=horizon_days,
        notes=(
            f"tickers_with_form4_data={n_with_data}",
            f"horizon_days={horizon_days}",
            f"cluster_threshold={cluster_threshold} insiders in trailing {window_days}d",
        ),
    )
    return build_report(rows, config)


__all__ = ["run_insider_cluster_hindcast"]
