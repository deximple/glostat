from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Final

import structlog

from glostat.data.cftc_client import (
    CftcClient,
    CotRecord,
    commercial_net_percentile,
)
from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseSignal

# Phase 1C — Thesis E8: Commodity TS Momentum + COT Extremes.
# Universe (10 commodity ETFs): USO, UNG, GLD, SLV, CPER, URA, CORN, WEAT, DBC, GSG.
# Signal A — TS momentum:  90d return + price/200d MA above/below 1.0
# Signal B — COT extreme:  commercial_net_percentile rank (5y rolling).
#                          rank > 0.85 = extreme commercial LONG (bullish)
#                          rank < 0.15 = extreme commercial SHORT (bearish)
# Combined:
#   sign agreement (TS == COT) → conviction × 2.0
#   COT only (TS=0)            → ±0.6
#   TS only (no COT or rank in middle band) → ±1.2
#   disagreement               → ±0.3 (weak signal)
#
# URA has no COT contract → degrades gracefully to TS-only.

log: Final = structlog.get_logger(__name__)

ETF_TO_COT_CONTRACT: Final[dict[str, str]] = {
    "USO":  "WTI_CRUDE",
    "UNG":  "NAT_GAS",
    "GLD":  "GOLD",
    "SLV":  "SILVER",
    "CPER": "COPPER",
    "URA":  "",
    "CORN": "CORN",
    "WEAT": "WHEAT",
    "DBC":  "WTI_CRUDE",
    "GSG":  "WTI_CRUDE",
}

UNIVERSE: Final[tuple[str, ...]] = tuple(ETF_TO_COT_CONTRACT.keys())

_MOMENTUM_WINDOW: Final[int] = 90
_MA_WINDOW: Final[int] = 200
_COT_HIGH_RANK: Final[float] = 0.85
_COT_LOW_RANK: Final[float] = 0.15
_DIRECTION_THRESHOLD: Final[float] = 1.0
_SCORE_CLIP: Final[float] = 3.0
_CONFIDENCE_BASE: Final[float] = 0.55


@dataclass(frozen=True, slots=True)
class CommodityTsSnapshot:
    ticker: str
    day: date
    momentum_90d: float | None
    price_over_ma200: float | None
    ts_signal: float                # -1.0, 0.0, +1.0
    cot_rank: float | None
    cot_signal: float               # -1.0, 0.0, +1.0
    sign_agree: bool
    raw_score: float
    net_score: float

    @property
    def direction(self) -> str:
        if self.net_score > _DIRECTION_THRESHOLD:
            return "LONG"
        if self.net_score < -_DIRECTION_THRESHOLD:
            return "SHORT"
        return "NEUTRAL"


class ECommodityTsExpert:
    name = "E_COMMODITY_TS"

    def __init__(
        self,
        *,
        price_cache: PriceCache,
        cftc_client: CftcClient | None = None,
    ) -> None:
        self._cache = price_cache
        self._cftc = cftc_client
        self._cot_cache: dict[tuple[str, int, int], tuple[CotRecord, ...]] = {}

    async def warm(self) -> None:
        for t in UNIVERSE:
            await self._cache.get(t)

    async def warm_cot(self, start: date, end: date) -> None:
        if self._cftc is None:
            return
        contracts = {c for c in ETF_TO_COT_CONTRACT.values() if c}
        if not contracts:
            return
        years_back = 6
        cot_start = date(start.year - years_back, 1, 1)
        await self._cftc.fetch_range(cot_start, end)

    async def signal_for(self, ticker: str, day: date) -> PhaseSignal:
        ticker_u = ticker.upper().strip()
        if ticker_u not in UNIVERSE:
            return _neutral(ticker_u, day, "outside_commodity_universe")
        snap = self._snapshot(ticker_u, day)
        cot_rank = await self._cot_rank(ticker_u, day)
        snap = self._refine_with_cot(snap, cot_rank)
        if snap.momentum_90d is None or snap.price_over_ma200 is None:
            return _neutral(ticker_u, day, "insufficient_price_history")
        return PhaseSignal(
            expert=self.name,
            ticker=ticker_u,
            day=snap.day,
            score=snap.net_score,
            direction=snap.direction,
            confidence=min(1.0, abs(snap.net_score) / _SCORE_CLIP * _CONFIDENCE_BASE + 0.2),
            metadata=(
                ("momentum_90d", f"{snap.momentum_90d:.6f}"),
                ("price_over_ma200", f"{snap.price_over_ma200:.6f}"),
                ("ts_signal", f"{snap.ts_signal:+.0f}"),
                ("cot_rank", _fmt_opt(snap.cot_rank)),
                ("cot_signal", f"{snap.cot_signal:+.0f}"),
                ("sign_agree", str(snap.sign_agree)),
                ("raw_score", f"{snap.raw_score:.4f}"),
            ),
        )

    def _snapshot(self, ticker: str, day: date) -> CommodityTsSnapshot:
        bars = _trading_day_closes(self._cache, ticker, day)
        if len(bars) < _MA_WINDOW + 1:
            return CommodityTsSnapshot(
                ticker=ticker, day=day,
                momentum_90d=None, price_over_ma200=None,
                ts_signal=0.0, cot_rank=None, cot_signal=0.0,
                sign_agree=False, raw_score=0.0, net_score=0.0,
            )
        latest = bars[0]
        older = bars[_MOMENTUM_WINDOW] if len(bars) > _MOMENTUM_WINDOW else bars[-1]
        if older <= 0:
            return CommodityTsSnapshot(
                ticker=ticker, day=day,
                momentum_90d=None, price_over_ma200=None,
                ts_signal=0.0, cot_rank=None, cot_signal=0.0,
                sign_agree=False, raw_score=0.0, net_score=0.0,
            )
        mom = (latest - older) / older
        ma = sum(bars[:_MA_WINDOW]) / _MA_WINDOW
        ratio = (latest / ma) if ma > 0 else None
        if ratio is None:
            return CommodityTsSnapshot(
                ticker=ticker, day=day,
                momentum_90d=mom, price_over_ma200=None,
                ts_signal=0.0, cot_rank=None, cot_signal=0.0,
                sign_agree=False, raw_score=0.0, net_score=0.0,
            )
        if mom > 0 and ratio > 1.0:
            ts = 1.0
        elif mom < 0 and ratio < 1.0:
            ts = -1.0
        else:
            ts = 0.0
        return CommodityTsSnapshot(
            ticker=ticker, day=day,
            momentum_90d=mom, price_over_ma200=ratio,
            ts_signal=ts, cot_rank=None, cot_signal=0.0,
            sign_agree=False, raw_score=0.0, net_score=0.0,
        )

    def _refine_with_cot(
        self, snap: CommodityTsSnapshot, cot_rank: float | None
    ) -> CommodityTsSnapshot:
        if cot_rank is None:
            cot_signal = 0.0
        elif cot_rank >= _COT_HIGH_RANK:
            cot_signal = 1.0
        elif cot_rank <= _COT_LOW_RANK:
            cot_signal = -1.0
        else:
            cot_signal = 0.0
        ts = snap.ts_signal
        if cot_signal == 0.0 and ts == 0.0:
            raw = 0.0
            agree = False
        elif cot_signal == 0.0:
            raw = ts * 1.2
            agree = False
        elif ts == 0.0:
            raw = cot_signal * 0.6
            agree = False
        elif ts == cot_signal:
            raw = ts * 2.0
            agree = True
        else:
            raw = (ts + cot_signal) * 0.3
            agree = False
        net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
        return CommodityTsSnapshot(
            ticker=snap.ticker, day=snap.day,
            momentum_90d=snap.momentum_90d,
            price_over_ma200=snap.price_over_ma200,
            ts_signal=ts,
            cot_rank=cot_rank,
            cot_signal=cot_signal,
            sign_agree=agree,
            raw_score=raw,
            net_score=net,
        )

    async def _cot_rank(self, ticker: str, day: date) -> float | None:
        contract = ETF_TO_COT_CONTRACT.get(ticker, "")
        if not contract or self._cftc is None:
            return None
        years_back = 6
        start = date(day.year - years_back, 1, 1)
        cache_key = (contract, start.year, day.year)
        if cache_key not in self._cot_cache:
            try:
                self._cot_cache[cache_key] = await self._cftc.fetch_range(start, day)
            except Exception as exc:
                log.warning(
                    "e_commodity_ts.cot_failed",
                    ticker=ticker, contract=contract, err=str(exc),
                )
                return None
        recs = self._cot_cache[cache_key]
        return commercial_net_percentile(
            recs, contract=contract, as_of=day, lookback_years=5
        )

    @property
    def universe(self) -> tuple[str, ...]:
        return UNIVERSE


def _trading_day_closes(
    cache: PriceCache, ticker: str, day: date
) -> list[float]:
    # WHY: walk the cached OHLCV bars newest→oldest filtering to bars on/before
    # `day`. Returns closes in newest-first order so callers can index 0/N.
    series = cache._mem.get(ticker.upper())
    if series is None:
        return []
    out: list[float] = []
    for bar in reversed(series.bars):
        bar_day = bar.ts.date() if hasattr(bar.ts, "date") else bar.ts
        if not isinstance(bar_day, date):
            continue
        if bar_day > day:
            continue
        out.append(float(bar.close))
    return out


def _fmt_opt(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def _neutral(ticker: str, day: date, reason: str) -> PhaseSignal:
    return PhaseSignal(
        expert=ECommodityTsExpert.name,
        ticker=ticker,
        day=day,
        score=0.0,
        direction="NEUTRAL",
        confidence=0.0,
        metadata=(("reason", reason),),
    )


__all__ = [
    "ETF_TO_COT_CONTRACT",
    "UNIVERSE",
    "CommodityTsSnapshot",
    "ECommodityTsExpert",
]
