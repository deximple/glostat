from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import is_kr_ticker, normalize_kr_ticker
from glostat.data.krx_short_client import (
    KrxShortBalanceBar,
    KrxShortClient,
    KrxShortError,
    KrxShortVolumeBar,
)

# v1.4 N2 — KR short-selling expert. Inspired by TITAN E5++ (short_selling.py)
# but trimmed to free public KRX data only (no Toss reverse-engineering).
#
# Direction signals:
#   - balance increase (3-day rolling) above 80th percentile → bearish pressure
#   - balance decrease + price up → SHORT_SQUEEZE candidate (bullish)
#   - balance decrease alone → SHORT_COVER (mildly bullish)
#   - high short ratio (>10% volume) + price up → squeeze risk
#
# Universe: any KOSPI 200 ticker (broad coverage of KOSPI/KOSDAQ short-eligible
# names). The wrapper tightens to KOSPI 200 for parity with other KR experts.
#
# Skip cleanly when KRX scraper fails so the predictor can fall through to a
# graceful "no short data" reason instead of crashing.

log: Final = structlog.get_logger(__name__)

_LOOKBACK_DAYS: Final[int] = 30
_ROLLING_WINDOW: Final[int] = 3
_PCTILE_HI: Final[float] = 0.80
_HIGH_SHORT_RATIO_PCT: Final[float] = 10.0
_SCORE_PER_COMPONENT: Final[float] = 0.6
_SCORE_CLIP: Final[float] = 3.0
_DIRECTION_THRESHOLD: Final[float] = 0.6
_HORIZON_DAYS: Final[int] = 14


@dataclass(frozen=True, slots=True)
class ShortSellingScore:
    code: str
    latest_balance_qty: float
    balance_3d_delta: float
    short_ratio_pct: float
    price_trend: str           # UP / DOWN / FLAT
    raw_score: float
    net_score: float
    direction: str             # LONG / SHORT / NEUTRAL
    signal: str                # SHORT_COVER / SHORT_SQUEEZE_RISK / SHORT_PRESSURE / NEUTRAL

    @property
    def confidence(self) -> float:
        return min(1.0, abs(self.net_score) / _SCORE_CLIP)


def score_short_selling(
    *,
    balance_bars: Sequence[KrxShortBalanceBar],
    volume_bars: Sequence[KrxShortVolumeBar],
    price_change_pct: float,
    code: str,
) -> ShortSellingScore:
    if not balance_bars and not volume_bars:
        return _neutral(code)
    bal_sorted = sorted(balance_bars, key=lambda b: b.bar_date)
    vol_sorted = sorted(volume_bars, key=lambda b: b.bar_date)
    latest_balance = bal_sorted[-1].short_balance_qty if bal_sorted else 0.0
    balance_3d_delta = _rolling_balance_delta(bal_sorted, window=_ROLLING_WINDOW)
    short_ratio = (
        vol_sorted[-1].short_ratio_pct if vol_sorted else 0.0
    )
    price_trend = _classify_price(price_change_pct)
    raw = 0.0
    signal = "NEUTRAL"
    # 1) balance decrease (covering) → mildly bullish
    if balance_3d_delta < 0:
        coverage_strength = min(
            1.0, abs(balance_3d_delta) / max(latest_balance, 1.0) * 10.0,
        )
        raw += _SCORE_PER_COMPONENT * coverage_strength
        signal = "SHORT_COVER"
    # 2) balance decrease + price up → squeeze risk (strongly bullish)
    if balance_3d_delta < 0 and price_trend == "UP":
        raw += _SCORE_PER_COMPONENT
        signal = "SHORT_SQUEEZE_RISK"
    # 3) balance increase (rolling 80th percentile proxy) → bearish
    if bal_sorted and balance_3d_delta > 0:
        history = [b.short_balance_qty for b in bal_sorted[:-1]]
        if history and _is_above_percentile(latest_balance, history, _PCTILE_HI):
            raw -= _SCORE_PER_COMPONENT
            signal = "SHORT_PRESSURE" if signal == "NEUTRAL" else signal
    # 4) high short ratio → bearish unless price strong up (squeeze risk)
    if short_ratio >= _HIGH_SHORT_RATIO_PCT:
        if price_trend == "UP":
            raw += _SCORE_PER_COMPONENT * 0.5
            if signal == "NEUTRAL":
                signal = "SHORT_SQUEEZE_RISK"
        else:
            raw -= _SCORE_PER_COMPONENT * 0.5
            if signal == "NEUTRAL":
                signal = "SHORT_PRESSURE"
    net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
    direction = (
        "LONG" if net > _DIRECTION_THRESHOLD
        else "SHORT" if net < -_DIRECTION_THRESHOLD
        else "NEUTRAL"
    )
    return ShortSellingScore(
        code=code, latest_balance_qty=latest_balance,
        balance_3d_delta=balance_3d_delta, short_ratio_pct=short_ratio,
        price_trend=price_trend, raw_score=raw, net_score=net,
        direction=direction, signal=signal,
    )


def _rolling_balance_delta(
    bars: Sequence[KrxShortBalanceBar], *, window: int,
) -> float:
    if len(bars) < window + 1:
        return 0.0
    return bars[-1].short_balance_qty - bars[-1 - window].short_balance_qty


def _classify_price(pct: float) -> str:
    if pct > 0.5:
        return "UP"
    if pct < -0.5:
        return "DOWN"
    return "FLAT"


def _is_above_percentile(
    value: float, history: list[float], percentile: float,
) -> bool:
    if not history:
        return False
    s = sorted(history)
    idx = int(len(s) * percentile)
    if idx >= len(s):
        idx = len(s) - 1
    return value > s[idx]


def _neutral(code: str) -> ShortSellingScore:
    return ShortSellingScore(
        code=code, latest_balance_qty=0.0, balance_3d_delta=0.0,
        short_ratio_pct=0.0, price_trend="FLAT", raw_score=0.0, net_score=0.0,
        direction="NEUTRAL", signal="NEUTRAL",
    )


class EShortSellingKrExpert:
    """KR short-selling expert — KRX-backed.

    Skip behaviour: KRX HTTP failures, missing-data days, and non-KOSPI 200
    tickers all raise ExpertSkipError so the composite surface gets a clean
    universe-aware reason.
    """

    name = "E_SHORT_SELLING_KR"

    def __init__(
        self,
        *,
        krx_client: KrxShortClient | None = None,
        kospi200: frozenset[str] | None = None,
        price_change_lookback_days: int = 5,
    ) -> None:
        self._krx = krx_client
        self._kospi200 = kospi200 or frozenset()
        self._price_lookback = price_change_lookback_days

    @classmethod
    def from_env(
        cls, *, kospi200: frozenset[str] | None = None,
    ) -> EShortSellingKrExpert | None:
        # KRX has no API key — always available, but we keep the from_env shape
        # for parity with the other KR experts.
        client = KrxShortClient()
        return cls(krx_client=client, kospi200=kospi200)

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        if self._krx is None:
            raise ExpertSkipError(
                "E_SHORT_SELLING_KR: KRX client not configured"
            )
        if not is_kr_ticker(ticker):
            raise ExpertSkipError(
                f"E_SHORT_SELLING_KR: ticker {ticker!r} not KR equity"
            )
        code = normalize_kr_ticker(ticker)
        if self._kospi200 and code not in self._kospi200:
            raise ExpertSkipError(
                f"E_SHORT_SELLING_KR: {code} not in KOSPI 200 universe"
            )
        as_of = ts.date()
        try:
            balances = await self._krx.get_short_balance(
                code, days_back=_LOOKBACK_DAYS, end=as_of,
            )
            volumes = await self._krx.get_short_volume(
                code, days_back=_LOOKBACK_DAYS, end=as_of,
            )
        except KrxShortError as exc:
            raise ExpertSkipError(
                f"E_SHORT_SELLING_KR: KRX fetch failed for {code}: {exc}"
            ) from exc
        if not balances and not volumes:
            raise ExpertSkipError(
                f"E_SHORT_SELLING_KR: no KRX short data for {code} "
                "(may be excluded from short-selling)"
            )
        # Price change from latest volume bars (use total_volume bar count as a
        # cheap proxy — actual price would need yfinance fetch). For a stable
        # signal the predictor passes price_change_pct=0.0 so the price-trend
        # contribution is only tagged FLAT; the squeeze branch only fires when
        # caller wires in the price change explicitly.
        price_change_pct = 0.0
        score = score_short_selling(
            balance_bars=balances, volume_bars=volumes,
            price_change_pct=price_change_pct, code=code,
        )
        snap_id = self._krx.last_snapshot_id or "krx.short"
        return _build_signal(code=code, ts=ts, score=score, snap_id=snap_id)


def _build_signal(
    *, code: str, ts: datetime, score: ShortSellingScore, snap_id: str,
) -> ExpertSignal:
    basis = (
        f"KRX short — signal={score.signal} balance={int(score.latest_balance_qty):,} "
        f"Δ3d={int(score.balance_3d_delta):+,} ratio={score.short_ratio_pct:.2f}%"
    )
    metadata: tuple[tuple[str, str], ...] = tuple(
        sorted({
            "signal": score.signal,
            "latest_balance_qty": f"{score.latest_balance_qty:.0f}",
            "balance_3d_delta": f"{score.balance_3d_delta:.0f}",
            "short_ratio_pct": f"{score.short_ratio_pct:.4f}",
            "price_trend": score.price_trend,
            "raw_score": f"{score.raw_score:.4f}",
            "net_score": f"{score.net_score:.4f}",
            "code": code,
        }.items())
    )
    return ExpertSignal(
        expert_name="E_SHORT_SELLING_KR",  # type: ignore[arg-type]
        ticker=code,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="continuation",
        basis=basis,
        sources=(snap_id,),
        expires_at=ts + timedelta(days=_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "EShortSellingKrExpert",
    "ShortSellingScore",
    "score_short_selling",
]
