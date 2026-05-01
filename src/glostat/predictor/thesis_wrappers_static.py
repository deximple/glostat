from __future__ import annotations

from typing import Final

from glostat.predictor.calibration import CalibrationTable
from glostat.predictor.kr_universe import (
    KOSPI200_UNIVERSE,
    is_kospi200,
    is_kr_ticker,
    kr_canonical,
)
from glostat.predictor.types import SignalContribution

# Static (non-live) thesis wrappers for theses without live experts wired.
# Split out of thesis_wrappers.py to keep that file under the 400-line cap
# (v1.4 N2 added two new live wrappers that pushed the parent over).

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


def _is_crypto_ticker(ticker: str) -> bool:
    t = ticker.upper()
    return any(s in t for s in _CRYPTO_SUFFIXES) or t in {
        "BTC", "ETH", "BTCUSDT", "ETHUSDT",
    }


def _make_contribution(
    *,
    name: str,
    value: float | None,
    direction: str,
    cal_table: CalibrationTable,
    skip_reason: str | None = None,
    source_snapshot_ids: tuple[str, ...] = (),
) -> SignalContribution:
    cal = cal_table.get(name)
    return SignalContribution(
        name=name,
        value=value,
        direction=direction,  # type: ignore[arg-type]
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


__all__ = [
    "wrap_commodity_ts_static",
    "wrap_fomc_drift_static",
    "wrap_foreign_reversal_static",
    "wrap_funding_carry_static",
    "wrap_fx_carry_static",
    "wrap_insider_cluster_static",
    "wrap_pead_static",
    "wrap_sector_rotation_static",
]
