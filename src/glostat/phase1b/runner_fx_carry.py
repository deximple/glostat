from __future__ import annotations

from datetime import date
from typing import Final

import structlog

from glostat.experts.e_fx_carry import (
    TARGET_TICKERS,
    UNIVERSE,
    EFxCarryExpert,
)
from glostat.phase1b.hindcast_runner import (
    HindcastConfig,
    build_report,
    make_trade_row,
    trading_days_between,
)
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseHindcastReport, PhaseTradeRow

# Phase 1C — Thesis E2 hindcast runner.
# Walk every trading day in the window; build the macro snapshot once per day;
# emit a per-target signal; if cost-gated → record trade row with 7-day forward
# return. Direction-neutral days do not record rows (zero alpha measurement).

log: Final = structlog.get_logger(__name__)

_HORIZON_DAYS: Final[int] = 7
_REBAL_STEP: Final[int] = 1


async def run_fx_carry_hindcast(
    *,
    cache: PriceCache,
    start: date,
    end: date,
    horizon_days: int = _HORIZON_DAYS,
) -> PhaseHindcastReport:
    expert = EFxCarryExpert(price_cache=cache)
    await expert.warm()

    days = trading_days_between(start, end, step_days=_REBAL_STEP)

    rows: list[PhaseTradeRow] = []
    n_attempted = 0
    n_skipped = 0
    n_risk_off = 0

    for d in days:
        snapshot = expert.snapshot_for_day(d)
        if snapshot.risk_off:
            n_risk_off += 1
        if not snapshot.is_complete:
            n_skipped += 1
            continue
        for target in TARGET_TICKERS:
            n_attempted += 1
            sig = expert.signal_for(target, snapshot)
            if sig.direction == "NEUTRAL":
                continue
            fwd = cache.forward_return(target, d, horizon_days)
            if fwd is None:
                n_skipped += 1
                continue
            rows.append(
                make_trade_row(
                    day=d,
                    ticker=target,
                    score=sig.score,
                    direction=sig.direction,
                    actual_fwd_return=fwd,
                )
            )

    config = HindcastConfig(
        expert="E_FX_CARRY",
        universe_size=len(TARGET_TICKERS),
        n_signals_attempted=n_attempted,
        n_signals_skipped=n_skipped,
        horizon_days=horizon_days,
        notes=(
            f"trading_days_in_window={len(days)}",
            f"days_with_risk_off={n_risk_off}",
            f"horizon_days={horizon_days}",
            "leg_thresholds: VIX5d>=25, FXY5d>+2%, EWZ3d<-1.5%",
            "trigger: at_least_2_of_3 legs",
            f"macro_universe={','.join(UNIVERSE)}",
        ),
    )
    return build_report(rows, config)


__all__ = ["run_fx_carry_hindcast"]
