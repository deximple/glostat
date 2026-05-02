from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.predictor.calibration import CalibrationTable
from glostat.predictor.kr_universe import (
    is_kospi200,
    is_kr_ticker,
    kr_canonical,
)

# Thesis wrappers — adapt each expert to a unified SignalContribution surface
# (value, direction, calibration metadata). Skip reasons are universe-aware so
# the user sees what was considered, not just what fired. v1.1 K1 adds KR
# routing (E_FUNDAMENTAL_KR, live E_FOREIGN_REVERSAL via Naver).
from glostat.predictor.thesis_wrappers_static import (
    _is_crypto_ticker,
    _make_contribution,
    _skip,
    wrap_commodity_ts_static,
    wrap_fomc_drift_static,
    wrap_foreign_reversal_static,
    wrap_funding_carry_static,
    wrap_fx_carry_static,
    wrap_insider_cluster_static,
    wrap_pead_static,
    wrap_sector_rotation_static,
)
from glostat.predictor.types import Direction, SignalContribution

log: Final = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class WrapperResult:
    name: str
    contribution: SignalContribution


def _direction_from_expert_signal(sig: ExpertSignal) -> Direction:
    if sig.direction == "LONG":
        return "up"
    if sig.direction == "SHORT":
        return "down"
    return "neutral"


# Wrap an existing expert.compute(ticker, ts) → SignalContribution
async def _wrap_expert_compute(
    name: str,
    expert: Any,
    ticker: str,
    ts: datetime,
    cal_table: CalibrationTable,
) -> SignalContribution:
    try:
        sig: ExpertSignal = await expert.compute(ticker, ts)
    except ExpertSkipError as exc:
        return _skip(name, str(exc), cal_table)
    except Exception as exc:
        log.warning("predictor.expert_failed", name=name, err=str(exc))
        return _skip(name, f"expert error: {exc}", cal_table)
    return _make_contribution(
        name=name,
        value=sig.net_score,
        direction=_direction_from_expert_signal(sig),
        cal_table=cal_table,
        source_snapshot_ids=tuple(sig.sources),
    )


# ── Per-thesis wrappers ────────────────────────────────────────────────────


async def wrap_fundamental(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    if is_kr_ticker(ticker) or _is_crypto_ticker(ticker):
        return _skip("E_FUNDAMENTAL", "ticker not US equity", cal_table)
    return await _wrap_expert_compute("E_FUNDAMENTAL", expert, ticker, ts, cal_table)


async def wrap_time(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # WHY (v1.1 K1): E_TIME requires only OHLCV (Ichimoku 257-day base);
    # universe-agnostic across US + KR. Crypto perpetuals still skip — they
    # are 24/7 + funding-driven, the daily-bar Ichimoku scaffolding doesn't
    # transfer cleanly. Caller's expert must accept the ticker form (US bare
    # ticker or KR 6-digit / .KS / .KQ).
    if _is_crypto_ticker(ticker):
        return _skip("E_TIME", "ticker not equity (crypto perpetual)", cal_table)
    return await _wrap_expert_compute("E_TIME", expert, ticker, ts, cal_table)


async def wrap_fundamental_kr(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # v1.1 K1 — KR-specific E_FUNDAMENTAL (yfinance .KS/.KQ only, no SEC EDGAR).
    if not is_kr_ticker(ticker):
        return _skip("E_FUNDAMENTAL_KR", "ticker not KR equity", cal_table)
    if not is_kospi200(ticker):
        return _skip(
            "E_FUNDAMENTAL_KR",
            f"ticker {kr_canonical(ticker)} not in KOSPI 200 universe",
            cal_table,
        )
    return await _wrap_expert_compute("E_FUNDAMENTAL_KR", expert, ticker, ts, cal_table)


async def wrap_fundamental_kr_cyclical(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # v1.5 P6 — cyclical-sector override for E_FUNDAMENTAL_KR. Activates only
    # when sector_classifier_kr classifies the ticker as cyclical (정유/철강/
    # 화학/운송/건설/자동차). Otherwise emits a universe-aware skip so the
    # user sees the slot exists.
    from glostat.data.sector_classifier_kr import (  # noqa: PLC0415 — cold import
        cycle_class_of, CycleClass, sector_of,
    )
    if not is_kr_ticker(ticker):
        return _skip("E_FUNDAMENTAL_KR_CYCLICAL", "ticker not KR equity", cal_table)
    if not is_kospi200(ticker):
        return _skip(
            "E_FUNDAMENTAL_KR_CYCLICAL",
            f"ticker {kr_canonical(ticker)} not in KOSPI 200 universe",
            cal_table,
        )
    if cycle_class_of(ticker) != CycleClass.CYCLICAL:
        sector = sector_of(ticker)
        return _skip(
            "E_FUNDAMENTAL_KR_CYCLICAL",
            f"ticker {kr_canonical(ticker)} not cyclical (sector={sector.value})",
            cal_table,
        )
    return await _wrap_expert_compute(
        "E_FUNDAMENTAL_KR_CYCLICAL", expert, ticker, ts, cal_table,
    )


async def wrap_commodity_index_kr(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # v1.5 P6 — KR refining-sector commodity-momentum expert (WTI + crack
    # spread). Universe-gated to refining tickers only.
    from glostat.data.sector_classifier_kr import is_refining  # noqa: PLC0415
    if not is_kr_ticker(ticker):
        return _skip("E_COMMODITY_INDEX_KR", "ticker not KR equity", cal_table)
    if not is_refining(ticker):
        return _skip(
            "E_COMMODITY_INDEX_KR",
            f"ticker {kr_canonical(ticker)} not in KR refining universe",
            cal_table,
        )
    return await _wrap_expert_compute(
        "E_COMMODITY_INDEX_KR", expert, ticker, ts, cal_table,
    )


async def wrap_pead_kr(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # v1.6 P5 — KR Post-Earnings Announcement Drift. Universe = KOSPI 200
    # (liquidity needed for the T+5..T+30 drift to be measurable).
    if not is_kr_ticker(ticker):
        return _skip("E_PEAD_KR", "ticker not KR equity", cal_table)
    if not is_kospi200(ticker):
        return _skip(
            "E_PEAD_KR",
            f"ticker {kr_canonical(ticker)} not in KOSPI 200 universe",
            cal_table,
        )
    return await _wrap_expert_compute("E_PEAD_KR", expert, ticker, ts, cal_table)


async def wrap_foreign_reversal_live(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # v1.1 K1 — live Naver-backed E_FOREIGN_REVERSAL. Replaces the static
    # `wrap_foreign_reversal_static` when an expert instance is wired.
    if not is_kr_ticker(ticker):
        return _skip(
            "E_FOREIGN_REVERSAL",
            f"ticker {ticker.upper()} not KR equity / KOSPI (6-digit code expected)",
            cal_table,
        )
    if not is_kospi200(ticker):
        return _skip(
            "E_FOREIGN_REVERSAL",
            f"ticker {kr_canonical(ticker)} not in KOSPI 200 universe",
            cal_table,
        )
    return await _wrap_expert_compute("E_FOREIGN_REVERSAL", expert, ticker, ts, cal_table)


async def wrap_insider_kr(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # v1.2 L2 — KR insider cluster expert (DART elestock). Universe-aware skip.
    if not is_kr_ticker(ticker):
        return _skip("E_INSIDER_KR", "ticker not KR equity", cal_table)
    if not is_kospi200(ticker):
        return _skip(
            "E_INSIDER_KR",
            f"ticker {kr_canonical(ticker)} not in KOSPI 200 universe",
            cal_table,
        )
    return await _wrap_expert_compute("E_INSIDER_KR", expert, ticker, ts, cal_table)


async def wrap_macro_kr(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # v1.3 M2 — KR macro expert (ECOS BoK OpenAPI). Universe-aware: any KR
    # ticker (XKRX or XKOS), no KOSPI 200 sub-screen — macro applies broadly.
    if not is_kr_ticker(ticker):
        return _skip("E_MACRO_KR", "ticker not KR equity", cal_table)
    return await _wrap_expert_compute("E_MACRO_KR", expert, ticker, ts, cal_table)


async def wrap_short_selling_kr(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # v1.4 N2 — KR short-selling expert (KRX public AJAX). Universe = KOSPI 200
    # (where short selling is allowed and liquidity is sufficient for the signal).
    if not is_kr_ticker(ticker):
        return _skip("E_SHORT_SELLING_KR", "ticker not KR equity", cal_table)
    if not is_kospi200(ticker):
        return _skip(
            "E_SHORT_SELLING_KR",
            f"ticker {kr_canonical(ticker)} not in KOSPI 200 universe",
            cal_table,
        )
    return await _wrap_expert_compute(
        "E_SHORT_SELLING_KR", expert, ticker, ts, cal_table,
    )


async def wrap_intraday_flow_kr(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    # v1.4 N2 — KR intraday flow expert (Naver baseline + optional KIS overlay).
    # KOSPI 200 only — intraday acceleration needs liquidity.
    if not is_kr_ticker(ticker):
        return _skip("E_INTRADAY_FLOW_KR", "ticker not KR equity", cal_table)
    if not is_kospi200(ticker):
        return _skip(
            "E_INTRADAY_FLOW_KR",
            f"ticker {kr_canonical(ticker)} not in KOSPI 200 universe",
            cal_table,
        )
    return await _wrap_expert_compute(
        "E_INTRADAY_FLOW_KR", expert, ticker, ts, cal_table,
    )


async def wrap_fund_flow(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    if is_kr_ticker(ticker) or _is_crypto_ticker(ticker):
        return _skip("E_FUND_FLOW", "ticker not US equity", cal_table)
    return await _wrap_expert_compute("E_FUND_FLOW", expert, ticker, ts, cal_table)


# ── Orchestrator ───────────────────────────────────────────────────────────


WrapperFn = Callable[[Any, str, datetime, CalibrationTable], Awaitable[SignalContribution]]


async def collect_contributions(  # noqa: PLR0912, PLR0915 — orchestrator: 1 branch per thesis slot
    *,
    ticker: str,
    ts: datetime,
    cal_table: CalibrationTable,
    fundamental_expert: Any | None = None,
    time_expert: Any | None = None,
    fund_flow_expert: Any | None = None,
    fundamental_kr_expert: Any | None = None,         # v1.1 K1
    foreign_reversal_expert: Any | None = None,       # v1.1 K1
    insider_kr_expert: Any | None = None,             # v1.2 L2 (DART)
    macro_kr_expert: Any | None = None,               # v1.3 M2 (ECOS)
    short_selling_kr_expert: Any | None = None,       # v1.4 N2 (KRX)
    intraday_flow_kr_expert: Any | None = None,       # v1.4 N2 (KIS+Naver)
    fundamental_kr_cyclical_expert: Any | None = None,# v1.5 P6 (sector cycle)
    commodity_index_kr_expert: Any | None = None,     # v1.5 P6 (WTI + crack)
    pead_kr_expert: Any | None = None,                # v1.6 P5 (post-earnings drift)
) -> tuple[SignalContribution, ...]:
    # WHY: gather every thesis's contribution. Live experts run when wired;
    # static-only theses (Phase 1B/C/D) emit skip with a universe-explanation
    # reason so the user sees "what we considered" not just "what fired".
    out: list[SignalContribution] = []
    if fundamental_expert is not None:
        out.append(await wrap_fundamental(fundamental_expert, ticker, ts, cal_table))
    else:
        out.append(_skip("E_FUNDAMENTAL", "expert not wired", cal_table))
    if time_expert is not None:
        out.append(await wrap_time(time_expert, ticker, ts, cal_table))
    else:
        out.append(_skip("E_TIME", "expert not wired", cal_table))
    if fund_flow_expert is not None:
        out.append(await wrap_fund_flow(fund_flow_expert, ticker, ts, cal_table))
    else:
        out.append(_skip("E_FUND_FLOW", "expert not wired", cal_table))
    out.append(wrap_sector_rotation_static(ticker, cal_table))
    out.append(wrap_pead_static(ticker, cal_table))
    out.append(wrap_fomc_drift_static(ticker, cal_table))
    out.append(wrap_insider_cluster_static(ticker, cal_table))
    out.append(wrap_commodity_ts_static(ticker, cal_table))
    out.append(wrap_fx_carry_static(ticker, cal_table))
    out.append(wrap_funding_carry_static(ticker, cal_table))
    # v1.1 K1: live KR-aware wrappers with fallback to static behaviour.
    if fundamental_kr_expert is not None:
        out.append(await wrap_fundamental_kr(fundamental_kr_expert, ticker, ts, cal_table))
    # Skip with universe-aware reason so the user knows the slot exists.
    elif is_kr_ticker(ticker):
        out.append(_skip("E_FUNDAMENTAL_KR", "expert not wired", cal_table))
    else:
        out.append(_skip("E_FUNDAMENTAL_KR", "ticker not KR equity", cal_table))
    if foreign_reversal_expert is not None:
        out.append(
            await wrap_foreign_reversal_live(foreign_reversal_expert, ticker, ts, cal_table)
        )
    else:
        out.append(wrap_foreign_reversal_static(ticker, cal_table))
    # v1.2 L2: KR insider expert (DART elestock). Skip cleanly when DART is
    # unavailable so US tickers + non-KOSPI 200 don't trigger an API call.
    if insider_kr_expert is not None:
        out.append(await wrap_insider_kr(insider_kr_expert, ticker, ts, cal_table))
    elif is_kr_ticker(ticker):
        out.append(_skip(
            "E_INSIDER_KR",
            "DART API not configured (set GLOSTAT_DART_API_KEY)",
            cal_table,
        ))
    else:
        out.append(_skip("E_INSIDER_KR", "ticker not KR equity", cal_table))
    # v1.3 M2: KR macro expert (ECOS BoK OpenAPI). Universe = any KR ticker
    # (no KOSPI 200 sub-screen — macro applies broadly).
    if macro_kr_expert is not None:
        out.append(await wrap_macro_kr(macro_kr_expert, ticker, ts, cal_table))
    elif is_kr_ticker(ticker):
        out.append(_skip(
            "E_MACRO_KR",
            "ECOS API not configured (set GLOSTAT_ECOS_API_KEY)",
            cal_table,
        ))
    else:
        out.append(_skip("E_MACRO_KR", "ticker not KR equity", cal_table))
    # v1.4 N2: KR short-selling expert (KRX). Free public; gracefully skips
    # when KRX scrape fails or universe excludes the ticker.
    if short_selling_kr_expert is not None:
        out.append(
            await wrap_short_selling_kr(short_selling_kr_expert, ticker, ts, cal_table)
        )
    elif is_kr_ticker(ticker):
        out.append(_skip(
            "E_SHORT_SELLING_KR",
            "KRX short client not wired",
            cal_table,
        ))
    else:
        out.append(_skip("E_SHORT_SELLING_KR", "ticker not KR equity", cal_table))
    # v1.4 N2: KR intraday flow expert (Naver baseline + optional KIS overlay).
    if intraday_flow_kr_expert is not None:
        out.append(
            await wrap_intraday_flow_kr(intraday_flow_kr_expert, ticker, ts, cal_table)
        )
    elif is_kr_ticker(ticker):
        out.append(_skip(
            "E_INTRADAY_FLOW_KR",
            "Naver intraday client not wired",
            cal_table,
        ))
    else:
        out.append(_skip("E_INTRADAY_FLOW_KR", "ticker not KR equity", cal_table))
    # v1.5 P6: cyclical-sector fundamental override + commodity-momentum.
    if fundamental_kr_cyclical_expert is not None:
        out.append(await wrap_fundamental_kr_cyclical(
            fundamental_kr_cyclical_expert, ticker, ts, cal_table,
        ))
    elif is_kr_ticker(ticker):
        out.append(_skip(
            "E_FUNDAMENTAL_KR_CYCLICAL",
            "commodity client not wired (cyclical-sector override)",
            cal_table,
        ))
    else:
        out.append(_skip(
            "E_FUNDAMENTAL_KR_CYCLICAL", "ticker not KR equity", cal_table,
        ))
    if commodity_index_kr_expert is not None:
        out.append(await wrap_commodity_index_kr(
            commodity_index_kr_expert, ticker, ts, cal_table,
        ))
    elif is_kr_ticker(ticker):
        out.append(_skip(
            "E_COMMODITY_INDEX_KR",
            "commodity client not wired (refining sector only)",
            cal_table,
        ))
    else:
        out.append(_skip(
            "E_COMMODITY_INDEX_KR", "ticker not KR equity", cal_table,
        ))
    # v1.6 P5: KR Post-Earnings Announcement Drift expert.
    if pead_kr_expert is not None:
        out.append(await wrap_pead_kr(pead_kr_expert, ticker, ts, cal_table))
    elif is_kr_ticker(ticker):
        out.append(_skip(
            "E_PEAD_KR",
            "calendar client not wired (post-earnings drift)",
            cal_table,
        ))
    else:
        out.append(_skip("E_PEAD_KR", "ticker not KR equity", cal_table))
    return tuple(out)


__all__ = [
    "WrapperResult",
    "collect_contributions",
    "wrap_commodity_index_kr",
    "wrap_commodity_ts_static",
    "wrap_fomc_drift_static",
    "wrap_foreign_reversal_live",
    "wrap_foreign_reversal_static",
    "wrap_fund_flow",
    "wrap_fundamental",
    "wrap_fundamental_kr",
    "wrap_fundamental_kr_cyclical",
    "wrap_funding_carry_static",
    "wrap_fx_carry_static",
    "wrap_insider_cluster_static",
    "wrap_insider_kr",
    "wrap_intraday_flow_kr",
    "wrap_macro_kr",
    "wrap_pead_kr",
    "wrap_pead_static",
    "wrap_sector_rotation_static",
    "wrap_short_selling_kr",
    "wrap_time",
]
