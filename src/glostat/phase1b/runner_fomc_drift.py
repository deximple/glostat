from __future__ import annotations

from datetime import date
from typing import Final

import structlog

from glostat.experts.e_fomc_drift import FOMC_DATES, EFomcDriftExpert
from glostat.experts.e_sector_rotation import BENCHMARK, SECTOR_ETFS
from glostat.phase1b.hindcast_runner import HindcastConfig, build_report, make_trade_row
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseHindcastReport, PhaseTradeRow

# Thesis E5b hindcast — FOMC drift continuation on SPY + sector ETFs.
# 1 sample per (ticker, fomc_event); 16+ events across 2024-2026 × 12 tickers
# ≈ 200 samples maximum.

log: Final = structlog.get_logger(__name__)

_HORIZON_DAYS: Final[int] = 5    # short window — drift is intra-week


async def run_fomc_drift_hindcast(
    *,
    cache: PriceCache,
    start: date,
    end: date,
    horizon_days: int = _HORIZON_DAYS,
) -> PhaseHindcastReport:
    universe = (BENCHMARK, *SECTOR_ETFS)
    expert = EFomcDriftExpert(
        price_cache=cache,
        universe=universe,
        fomc_dates=FOMC_DATES,
    )
    for ticker in universe:
        await cache.get(ticker)

    fomc_in_window = expert.event_dates_in_window(start, end)
    rows: list[PhaseTradeRow] = []
    n_attempted = 0
    n_skipped = 0

    for fomc_day in fomc_in_window:
        for ticker in universe:
            n_attempted += 1
            ev = await expert.compute_event(ticker, fomc_day)
            if ev is None:
                n_skipped += 1
                continue
            sig = expert.to_signal(ev)
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
        expert="E_FOMC_DRIFT",
        universe_size=len(universe),
        n_signals_attempted=n_attempted,
        n_signals_skipped=n_skipped,
        horizon_days=horizon_days,
        notes=(
            f"fomc_events_in_window={len(fomc_in_window)}",
            f"horizon_days={horizon_days}",
            "signal=announcement_day_return continuation",
        ),
    )
    return build_report(rows, config)


__all__ = ["run_fomc_drift_hindcast"]
