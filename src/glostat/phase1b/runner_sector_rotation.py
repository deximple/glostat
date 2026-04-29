from __future__ import annotations

from datetime import date, timedelta
from typing import Final

import structlog

from glostat.experts.e_sector_rotation import (
    BENCHMARK,
    SECTOR_ETFS,
    ESectorRotationExpert,
)
from glostat.phase1b.hindcast_runner import (
    HindcastConfig,
    build_report,
    make_trade_row,
)
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseHindcastReport, PhaseTradeRow

# Thesis E1 hindcast — sector rotation long-short.
# Step the rebalance every REBAL_DAYS (no daily churn).

log: Final = structlog.get_logger(__name__)

_REBAL_DAYS: Final[int] = 21         # ~monthly rebalance
_HORIZON_DAYS: Final[int] = 30


async def run_sector_rotation_hindcast(
    *,
    cache: PriceCache,
    start: date,
    end: date,
    rebalance_days: int = _REBAL_DAYS,
    horizon_days: int = _HORIZON_DAYS,
) -> PhaseHindcastReport:
    expert = ESectorRotationExpert(price_cache=cache)
    # Pre-fetch all ETFs once
    universe = (*SECTOR_ETFS, BENCHMARK)
    for ticker in universe:
        await cache.get(ticker)

    rebal_dates = _rebal_dates(start, end, rebalance_days)
    rows: list[PhaseTradeRow] = []
    n_attempted = 0
    n_skipped = 0

    for rebal_day in rebal_dates:
        signals = await expert.compute_for_day(rebal_day)
        for ticker, sig in signals.items():
            if ticker == BENCHMARK:
                continue
            n_attempted += 1
            if sig.direction == "NEUTRAL":
                continue
            fwd_return = cache.forward_return(ticker, rebal_day, horizon_days)
            if fwd_return is None:
                n_skipped += 1
                continue
            rows.append(
                make_trade_row(
                    day=rebal_day,
                    ticker=ticker,
                    score=sig.score,
                    direction=sig.direction,
                    actual_fwd_return=fwd_return,
                )
            )

    config = HindcastConfig(
        expert="E_SECTOR_ROTATION",
        universe_size=len(SECTOR_ETFS),
        n_signals_attempted=n_attempted,
        n_signals_skipped=n_skipped,
        horizon_days=horizon_days,
        notes=(
            f"rebalance_days={rebalance_days}",
            f"horizon_days={horizon_days}",
            f"rebal_dates={len(rebal_dates)}",
            f"long_short_top{3}_bot{3}_of_{len(SECTOR_ETFS)}",
        ),
    )
    return build_report(rows, config)


def _rebal_dates(start: date, end: date, step_days: int) -> list[date]:
    out: list[date] = []
    cur = start
    while cur <= end:
        # Snap to next weekday
        snap = cur
        while snap.weekday() >= 5:
            snap += timedelta(days=1)
        if snap > end:
            break
        out.append(snap)
        cur += timedelta(days=step_days)
    return out


__all__ = ["run_sector_rotation_hindcast"]
