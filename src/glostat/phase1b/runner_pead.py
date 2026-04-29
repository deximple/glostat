from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import date
from typing import Final

import structlog

from glostat.data.yfinance_client import YFinanceClient
from glostat.experts.e_pead import EPeadExpert
from glostat.phase1b.hindcast_runner import HindcastConfig, build_report, make_trade_row
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseHindcastReport, PhaseTradeRow

# Thesis E5a hindcast — PEAD on S&P 500 top 50.
# 1 sample per (ticker, earnings_event); typically 8 quarters * 50 = 400 events.

log: Final = structlog.get_logger(__name__)

_HORIZON_DAYS: Final[int] = 30
_PARALLEL_LIMIT: Final[int] = 4    # be polite to yfinance.calendar endpoint


async def run_pead_hindcast(
    *,
    universe: Sequence[str],
    yf_client: YFinanceClient,
    cache: PriceCache,
    start: date,
    end: date,
    horizon_days: int = _HORIZON_DAYS,
) -> PhaseHindcastReport:
    expert = EPeadExpert(yf_client=yf_client, start_date=start, end_date=end)
    sem = asyncio.Semaphore(_PARALLEL_LIMIT)

    async def collect(ticker: str) -> tuple[str, list]:
        async with sem:
            return ticker, await expert.get_events(ticker)

    tasks = [collect(t) for t in universe]
    results = await asyncio.gather(*tasks)

    # Pre-fetch OHLCV in parallel for tickers that produced events.
    needed = {t for t, evs in results if evs}
    for t in needed:
        await cache.get(t)

    rows: list[PhaseTradeRow] = []
    n_attempted = 0
    n_skipped = 0
    n_events = 0

    for ticker, events in results:
        for ev in events:
            n_events += 1
            n_attempted += 1
            sig = expert.signal_from_event(ev)
            if sig.direction == "NEUTRAL":
                continue
            fwd = cache.forward_return(ticker, sig.day, horizon_days)
            if fwd is None:
                n_skipped += 1
                continue
            rows.append(
                make_trade_row(
                    day=sig.day,
                    ticker=ticker,
                    score=sig.score,
                    direction=sig.direction,
                    actual_fwd_return=fwd,
                )
            )

    config = HindcastConfig(
        expert="E_PEAD",
        universe_size=len(universe),
        n_signals_attempted=n_attempted,
        n_signals_skipped=n_skipped,
        horizon_days=horizon_days,
        notes=(
            f"events_total={n_events}",
            f"events_with_actionable_signal={len(rows)}",
            "entry=T+1 close approx",
            "exit=T+30 calendar",
        ),
    )
    return build_report(rows, config)


__all__ = ["run_pead_hindcast"]
