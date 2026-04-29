from __future__ import annotations

from datetime import date
from typing import Final

import structlog

from glostat.data.cftc_client import CftcClient
from glostat.experts.e_commodity_ts import (
    ETF_TO_COT_CONTRACT,
    UNIVERSE,
    ECommodityTsExpert,
)
from glostat.phase1b.hindcast_runner import (
    HindcastConfig,
    build_report,
    make_trade_row,
    trading_days_between,
)
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseHindcastReport, PhaseTradeRow

# Phase 1C — Thesis E8 hindcast runner.
# Step weekly (5 trading days) instead of daily — COT releases are weekly so
# daily resampling adds no information and just multiplies the signal count.
# Each step: per-ticker score from price+COT; if directional & cost-gated,
# record trade row with 30-day forward return.

log: Final = structlog.get_logger(__name__)

_HORIZON_DAYS: Final[int] = 30
_REBAL_STEP_DAYS: Final[int] = 5


async def run_commodity_ts_hindcast(
    *,
    cache: PriceCache,
    cftc_client: CftcClient | None,
    start: date,
    end: date,
    horizon_days: int = _HORIZON_DAYS,
    rebal_step_days: int = _REBAL_STEP_DAYS,
) -> PhaseHindcastReport:
    expert = ECommodityTsExpert(
        price_cache=cache, cftc_client=cftc_client
    )
    await expert.warm()
    if cftc_client is not None:
        await expert.warm_cot(start, end)

    days = trading_days_between(start, end, step_days=rebal_step_days)

    rows: list[PhaseTradeRow] = []
    n_attempted = 0
    n_skipped = 0
    n_with_cot = 0

    for d in days:
        for ticker in UNIVERSE:
            n_attempted += 1
            sig = await expert.signal_for(ticker, d)
            if sig.direction == "NEUTRAL":
                continue
            cot_md = next(
                (v for k, v in sig.metadata if k == "cot_rank"), "n/a"
            )
            if cot_md != "n/a":
                n_with_cot += 1
            fwd = cache.forward_return(ticker, d, horizon_days)
            if fwd is None:
                n_skipped += 1
                continue
            rows.append(
                make_trade_row(
                    day=d,
                    ticker=ticker,
                    score=sig.score,
                    direction=sig.direction,
                    actual_fwd_return=fwd,
                )
            )

    config = HindcastConfig(
        expert="E_COMMODITY_TS",
        universe_size=len(UNIVERSE),
        n_signals_attempted=n_attempted,
        n_signals_skipped=n_skipped,
        horizon_days=horizon_days,
        notes=(
            f"trading_steps_in_window={len(days)}",
            f"signals_with_cot_rank={n_with_cot}",
            f"horizon_days={horizon_days}",
            f"rebal_step_days={rebal_step_days}",
            "ts: 90d return + price/200dMA",
            "cot: commercial_net_pct 5y rolling rank thresholds 0.85/0.15",
            f"contracts={','.join(c for c in ETF_TO_COT_CONTRACT.values() if c)}",
            "URA: TS-only (no COT contract)",
        ),
    )
    return build_report(rows, config)


__all__ = ["run_commodity_ts_hindcast"]
