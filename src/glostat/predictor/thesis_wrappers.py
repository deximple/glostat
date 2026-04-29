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
    KOSPI200_UNIVERSE,
    is_kospi200,
    is_kr_ticker,
    kr_canonical,
)
from glostat.predictor.types import Direction, SignalContribution

# Thesis wrappers — adapt each expert to a unified SignalContribution surface
# (value, direction, calibration metadata). Skip reasons are universe-aware so
# the user sees what was considered, not just what fired. v1.1 K1 adds KR
# routing (E_FUNDAMENTAL_KR, live E_FOREIGN_REVERSAL via Naver).

log: Final = structlog.get_logger(__name__)

_SECTOR_ETFS: Final[frozenset[str]] = frozenset(
    {"XLF", "XLE", "XLK", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC"}
)
_COMMODITY_ETFS: Final[frozenset[str]] = frozenset(
    {"USO", "UNG", "GLD", "SLV", "CPER", "URA", "CORN", "WEAT", "DBC", "GSG"}
)
_FX_CARRY_TARGETS: Final[frozenset[str]] = frozenset(
    {"XLU", "XLV", "XLF", "XLE", "SPY"}
)
_FOMC_UNIVERSE: Final[frozenset[str]] = frozenset({"SPY", *_SECTOR_ETFS})
_CRYPTO_SUFFIXES: Final[tuple[str, ...]] = (":USDT", "/USDT", "USDT")
_KOSPI200_UNIVERSE: Final[frozenset[str]] = KOSPI200_UNIVERSE


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


def _make_contribution(
    *,
    name: str,
    value: float | None,
    direction: Direction,
    cal_table: CalibrationTable,
    skip_reason: str | None = None,
    source_snapshot_ids: tuple[str, ...] = (),
) -> SignalContribution:
    cal = cal_table.get(name)
    return SignalContribution(
        name=name,
        value=value,
        direction=direction,
        calibration_auc=cal.auc,
        calibration_sharpe=cal.sharpe,
        n_samples=cal.n_samples,
        skip_reason=skip_reason,
        source_snapshot_ids=source_snapshot_ids,
    )


def _skip(name: str, reason: str, cal_table: CalibrationTable) -> SignalContribution:
    return _make_contribution(
        name=name, value=None, direction="skip",
        cal_table=cal_table, skip_reason=reason,
    )


def _is_crypto_ticker(ticker: str) -> bool:
    t = ticker.upper()
    return any(s in t for s in _CRYPTO_SUFFIXES) or t in {"BTC", "ETH", "BTCUSDT", "ETHUSDT"}


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


async def wrap_fund_flow(
    expert: Any, ticker: str, ts: datetime, cal_table: CalibrationTable
) -> SignalContribution:
    if is_kr_ticker(ticker) or _is_crypto_ticker(ticker):
        return _skip("E_FUND_FLOW", "ticker not US equity", cal_table)
    return await _wrap_expert_compute("E_FUND_FLOW", expert, ticker, ts, cal_table)


def wrap_sector_rotation_static(
    ticker: str, cal_table: CalibrationTable
) -> SignalContribution:
    # WHY: E_SECTOR_ROTATION operates only on the 11 SPDR sector ETFs; for any
    # individual stock we report skip with a clear universe reason. No live call.
    if ticker.upper() not in _SECTOR_ETFS:
        return _skip(
            "E_SECTOR_ROTATION",
            f"ticker {ticker.upper()} not in SPDR sector ETF universe",
            cal_table,
        )
    return _make_contribution(
        name="E_SECTOR_ROTATION", value=0.0, direction="neutral",
        cal_table=cal_table,
    )


def wrap_pead_static(
    ticker: str, cal_table: CalibrationTable, in_universe: bool = True
) -> SignalContribution:
    # WHY: E_PEAD requires an actual earnings event to fire. Without one we
    # honestly emit "no recent earnings event"; the composite knows skip.
    # `in_universe` lets the live caller indicate the ticker passed S&P 500
    # screening; default True keeps the wrapper testable cheaply.
    if not in_universe:
        return _skip(
            "E_PEAD",
            f"ticker {ticker.upper()} not in S&P 500 PEAD universe",
            cal_table,
        )
    return _skip(
        "E_PEAD", "no earnings event in evaluation window", cal_table
    )


def wrap_fomc_drift_static(
    ticker: str, cal_table: CalibrationTable
) -> SignalContribution:
    if ticker.upper() not in _FOMC_UNIVERSE:
        return _skip(
            "E_FOMC_DRIFT",
            f"ticker {ticker.upper()} not in FOMC drift universe (SPY + sector ETFs)",
            cal_table,
        )
    return _skip("E_FOMC_DRIFT", "no FOMC event within drift window", cal_table)


def wrap_insider_cluster_static(
    ticker: str, cal_table: CalibrationTable
) -> SignalContribution:
    # WHY: E_INSIDER_CLUSTER operates on Russell 2000 names with Form 4 activity.
    # Without a live SEC pull we cannot inspect cluster status. Report skip.
    if ticker.upper() in _SECTOR_ETFS or ticker.upper() in _COMMODITY_ETFS:
        return _skip(
            "E_INSIDER_CLUSTER",
            f"ticker {ticker.upper()} is an ETF — insider activity not applicable",
            cal_table,
        )
    return _skip(
        "E_INSIDER_CLUSTER", "no insider cluster data wired (Russell 2000 only)", cal_table
    )


def wrap_commodity_ts_static(
    ticker: str, cal_table: CalibrationTable
) -> SignalContribution:
    if ticker.upper() not in _COMMODITY_ETFS:
        return _skip(
            "E_COMMODITY_TS",
            f"ticker {ticker.upper()} not a commodity ETF",
            cal_table,
        )
    return _make_contribution(
        name="E_COMMODITY_TS", value=0.0, direction="neutral",
        cal_table=cal_table,
    )


def wrap_fx_carry_static(
    ticker: str, cal_table: CalibrationTable
) -> SignalContribution:
    if ticker.upper() not in _FX_CARRY_TARGETS:
        return _skip(
            "E_FX_CARRY",
            f"ticker {ticker.upper()} not a FX-carry target",
            cal_table,
        )
    return _make_contribution(
        name="E_FX_CARRY", value=0.0, direction="neutral",
        cal_table=cal_table,
    )


def wrap_funding_carry_static(
    ticker: str, cal_table: CalibrationTable
) -> SignalContribution:
    if not _is_crypto_ticker(ticker):
        return _skip(
            "E_FUNDING_CARRY",
            "ticker not crypto perpetual (BTC/ETH supported)",
            cal_table,
        )
    return _make_contribution(
        name="E_FUNDING_CARRY", value=0.0, direction="neutral",
        cal_table=cal_table,
    )


def wrap_foreign_reversal_static(
    ticker: str, cal_table: CalibrationTable
) -> SignalContribution:
    # WHY: kept for back-compat + tests. The orchestrator (`collect_contributions`)
    # now prefers the live wrapper `wrap_foreign_reversal_live` when an expert
    # is injected. Static path emits neutral=0 for any KR universe member.
    if not is_kr_ticker(ticker):
        return _skip(
            "E_FOREIGN_REVERSAL",
            f"ticker {ticker.upper()} not KR equity / KOSPI (6-digit code expected)",
            cal_table,
        )
    if _KOSPI200_UNIVERSE and not is_kospi200(ticker):
        return _skip(
            "E_FOREIGN_REVERSAL",
            f"ticker {kr_canonical(ticker)} not in KOSPI 200 universe",
            cal_table,
        )
    return _make_contribution(
        name="E_FOREIGN_REVERSAL", value=0.0, direction="neutral",
        cal_table=cal_table,
    )


# ── Orchestrator ───────────────────────────────────────────────────────────


WrapperFn = Callable[[Any, str, datetime, CalibrationTable], Awaitable[SignalContribution]]


async def collect_contributions(
    *,
    ticker: str,
    ts: datetime,
    cal_table: CalibrationTable,
    fundamental_expert: Any | None = None,
    time_expert: Any | None = None,
    fund_flow_expert: Any | None = None,
    fundamental_kr_expert: Any | None = None,    # v1.1 K1
    foreign_reversal_expert: Any | None = None,  # v1.1 K1
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
    return tuple(out)


__all__ = [
    "WrapperResult",
    "collect_contributions",
    "wrap_commodity_ts_static",
    "wrap_fomc_drift_static",
    "wrap_foreign_reversal_live",
    "wrap_foreign_reversal_static",
    "wrap_fund_flow",
    "wrap_fundamental",
    "wrap_fundamental_kr",
    "wrap_funding_carry_static",
    "wrap_fx_carry_static",
    "wrap_insider_cluster_static",
    "wrap_pead_static",
    "wrap_sector_rotation_static",
    "wrap_time",
]
