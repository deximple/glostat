from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

import structlog

from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseSignal

# Phase 1C — Thesis E2: FX Carry + Risk-Off Regime Flip.
# Universe (defensive tilt targets): XLU, XLV.
# Cyclical proxies (short side / negative tilt): XLF, XLE, SPY.
# Macro inputs: ^VIX (volatility), FXY (yen ETF — JPY safe-haven flow),
#               EWZ (Brazil ETF — high-beta EM risk asset, carry unwind proxy).
#
# Trigger (regime flip to risk-off — at least 2 of 3 legs active):
#   leg_vix:  VIX 5d rolling mean >= 25
#   leg_fxy:  FXY 5d return     > +2.0%
#   leg_ewz:  EWZ 3d return     < -1.5%
#
# Action (5-10d swing):
#   risk_off=True  AND target in {XLU, XLV} → LONG
#   risk_off=True  AND target in {XLF, XLE, SPY} → SHORT
#
# Score continuous on −3..+3 driven by leg count + threshold overshoot magnitude.

log: Final = structlog.get_logger(__name__)

_VIX_TICKER: Final[str] = "^VIX"
_FXY_TICKER: Final[str] = "FXY"
_EWZ_TICKER: Final[str] = "EWZ"

DEFENSIVE_TICKERS: Final[tuple[str, ...]] = ("XLU", "XLV")
CYCLICAL_TICKERS: Final[tuple[str, ...]] = ("XLF", "XLE", "SPY")
TARGET_TICKERS: Final[tuple[str, ...]] = (*DEFENSIVE_TICKERS, *CYCLICAL_TICKERS)
MACRO_TICKERS: Final[tuple[str, ...]] = (_VIX_TICKER, _FXY_TICKER, _EWZ_TICKER)
UNIVERSE: Final[tuple[str, ...]] = (*TARGET_TICKERS, *MACRO_TICKERS)

_VIX_WINDOW: Final[int] = 5
_VIX_THRESHOLD: Final[float] = 25.0
_FXY_WINDOW: Final[int] = 5
_FXY_THRESHOLD: Final[float] = 0.02
_EWZ_WINDOW: Final[int] = 3
_EWZ_THRESHOLD: Final[float] = -0.015
_DIRECTION_THRESHOLD: Final[float] = 1.0
_SCORE_CLIP: Final[float] = 3.0
_CONFIDENCE_FULL: Final[float] = 0.7


@dataclass(frozen=True, slots=True)
class FxCarrySnapshot:
    day: date
    vix_5d_mean: float | None
    fxy_5d_return: float | None
    ewz_3d_return: float | None
    leg_vix: bool
    leg_fxy: bool
    leg_ewz: bool

    @property
    def legs_active(self) -> int:
        return int(self.leg_vix) + int(self.leg_fxy) + int(self.leg_ewz)

    @property
    def risk_off(self) -> bool:
        return self.legs_active >= 2

    @property
    def is_complete(self) -> bool:
        return None not in (
            self.vix_5d_mean, self.fxy_5d_return, self.ewz_3d_return
        )

    def overshoot_magnitude(self) -> float:
        if not self.is_complete:
            return 0.0
        ov_vix = max(0.0, ((self.vix_5d_mean or 0.0) - _VIX_THRESHOLD) / 10.0)
        ov_fxy = max(0.0, ((self.fxy_5d_return or 0.0) - _FXY_THRESHOLD) / 0.02)
        ov_ewz = max(0.0, (_EWZ_THRESHOLD - (self.ewz_3d_return or 0.0)) / 0.02)
        return ov_vix + ov_fxy + ov_ewz


class EFxCarryExpert:
    name = "E_FX_CARRY"

    def __init__(self, *, price_cache: PriceCache) -> None:
        self._cache = price_cache

    async def warm(self) -> None:
        for t in UNIVERSE:
            await self._cache.get(t)

    def snapshot_for_day(self, day: date) -> FxCarrySnapshot:
        vix5 = _trailing_mean_close(self._cache, _VIX_TICKER, day, _VIX_WINDOW)
        fxy5 = _trailing_return(self._cache, _FXY_TICKER, day, _FXY_WINDOW)
        ewz3 = _trailing_return(self._cache, _EWZ_TICKER, day, _EWZ_WINDOW)
        return FxCarrySnapshot(
            day=day,
            vix_5d_mean=vix5,
            fxy_5d_return=fxy5,
            ewz_3d_return=ewz3,
            leg_vix=(vix5 is not None and vix5 >= _VIX_THRESHOLD),
            leg_fxy=(fxy5 is not None and fxy5 > _FXY_THRESHOLD),
            leg_ewz=(ewz3 is not None and ewz3 < _EWZ_THRESHOLD),
        )

    def signal_for(
        self, ticker: str, snapshot: FxCarrySnapshot
    ) -> PhaseSignal:
        ticker_u = ticker.upper().strip()
        if ticker_u not in TARGET_TICKERS:
            return _neutral(ticker_u, snapshot.day, "outside_target_universe")
        if not snapshot.is_complete:
            return _neutral(ticker_u, snapshot.day, "incomplete_macro_inputs")
        if not snapshot.risk_off:
            return _neutral(ticker_u, snapshot.day, "regime_not_risk_off")
        if ticker_u in DEFENSIVE_TICKERS:
            sign = 1.0
            target_class = "defensive"
        else:
            sign = -1.0
            target_class = "cyclical"
        magnitude = snapshot.legs_active + snapshot.overshoot_magnitude()
        raw = sign * magnitude * 0.6
        net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
        direction = (
            "LONG" if net > _DIRECTION_THRESHOLD
            else "SHORT" if net < -_DIRECTION_THRESHOLD
            else "NEUTRAL"
        )
        return PhaseSignal(
            expert=self.name,
            ticker=ticker_u,
            day=snapshot.day,
            score=net,
            direction=direction,
            confidence=min(1.0, abs(net) / _SCORE_CLIP * _CONFIDENCE_FULL + 0.2),
            metadata=(
                ("vix_5d_mean", f"{snapshot.vix_5d_mean:.4f}"),
                ("fxy_5d_return", f"{snapshot.fxy_5d_return:.6f}"),
                ("ewz_3d_return", f"{snapshot.ewz_3d_return:.6f}"),
                ("legs_active", str(snapshot.legs_active)),
                ("risk_off", str(snapshot.risk_off)),
                ("target_class", target_class),
                ("raw_score", f"{raw:.4f}"),
            ),
        )

    @property
    def universe(self) -> tuple[str, ...]:
        return UNIVERSE


def _trailing_mean_close(
    cache: PriceCache, ticker: str, day: date, window: int
) -> float | None:
    # WHY: use raw OHLCV bars from the cache so VIX (which can hold the same
    # close two days running) doesn't get dropped. Walk the in-memory series
    # backwards from `day`, take the latest `window` distinct trading-day bars.
    series = cache._mem.get(ticker.upper())
    if series is None:
        return None
    closes: list[float] = []
    for bar in reversed(series.bars):
        bar_day = bar.ts.date() if hasattr(bar.ts, "date") else bar.ts
        if not isinstance(bar_day, date):
            continue
        if bar_day > day:
            continue
        closes.append(float(bar.close))
        if len(closes) == window:
            break
    if len(closes) < window:
        return None
    return sum(closes) / window


def _trailing_return(
    cache: PriceCache, ticker: str, day: date, window: int
) -> float | None:
    # WHY: walk the trading-day bar series so `window` is in trading days, not
    # calendar days. Avoids weekend/holiday slip that would compress a 5-trading
    # day window to a 3-bar slice on a Tuesday.
    series = cache._mem.get(ticker.upper())
    if series is None:
        return None
    bars_in_window: list[float] = []
    for bar in reversed(series.bars):
        bar_day = bar.ts.date() if hasattr(bar.ts, "date") else bar.ts
        if not isinstance(bar_day, date):
            continue
        if bar_day > day:
            continue
        bars_in_window.append(float(bar.close))
        if len(bars_in_window) >= window + 1:
            break
    if len(bars_in_window) < window + 1:
        return None
    latest = bars_in_window[0]
    older = bars_in_window[window]
    if older <= 0:
        return None
    return (latest - older) / older


def _neutral(ticker: str, day: date, reason: str) -> PhaseSignal:
    return PhaseSignal(
        expert=EFxCarryExpert.name,
        ticker=ticker,
        day=day,
        score=0.0,
        direction="NEUTRAL",
        confidence=0.0,
        metadata=(("reason", reason),),
    )


__all__ = [
    "CYCLICAL_TICKERS",
    "DEFENSIVE_TICKERS",
    "MACRO_TICKERS",
    "TARGET_TICKERS",
    "UNIVERSE",
    "EFxCarryExpert",
    "FxCarrySnapshot",
]
