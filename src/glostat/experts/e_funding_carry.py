from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Final

# E_FUNDING_CARRY (Phase 1D Thesis E7) — perpetual-futures funding rate reversal.
# Universe: {BTC/USDT:USDT, ETH/USDT:USDT} on Binance USDM perpetuals.
# Signal:
#   8h funding rate z-scored vs 30-day rolling window (90 bars, 30d × 3 funding/d).
#   z >  1.5  → SHORT  (extreme positive funding = crowded longs, mean-revert lower)
#   z < -1.0  → LONG   (rare negative funding = institutional accumulation pays shorts)
#   z in [-0.5, +0.5] → neutral carry harvest (long perp earns funding) → tiny LONG bias
#   else → NEUTRAL
# Horizon: 8h–24h (3 funding events ≈ 1 day) for swing-aligned evaluation.

_LOOKBACK_BARS: Final[int] = 90  # 30d × 3 funding/day
_Z_HIGH: Final[float] = 1.5
_Z_LOW: Final[float] = -1.0
_Z_CARRY_BAND: Final[float] = 0.5
_HORIZON_BARS: Final[int] = 3  # 24h forward
_NET_SCORE_TO_BPS: Final[float] = 25.0  # composite|score| × 25 → expected edge bps


@dataclass(frozen=True, slots=True)
class FundingCarryScore:
    rate: float
    rolling_mean: float
    rolling_std: float
    z_score: float
    pattern: str
    direction: str        # LONG / SHORT / NEUTRAL
    net_score: float      # [-3, +3]
    confidence: float     # [0, 1]

    @property
    def is_skip(self) -> bool:
        return self.pattern == "INSUFFICIENT"


def _classify_z(z: float) -> tuple[str, str, float]:
    # Returns (pattern, direction, net_score [-3, 3])
    if z >= _Z_HIGH:
        return ("REVERSAL_SHORT", "SHORT", -min(3.0, z))
    if z <= _Z_LOW:
        return ("ACCUMULATION_LONG", "LONG", min(3.0, abs(z)))
    if abs(z) <= _Z_CARRY_BAND:
        return ("CARRY", "LONG", 0.5)  # mild long bias for neutral carry harvest
    return ("NEUTRAL", "NEUTRAL", 0.0)


def score_funding_rate(
    history: list[float],
    *,
    current_idx: int,
    lookback: int = _LOOKBACK_BARS,
) -> FundingCarryScore:
    """Compute funding carry score at history[current_idx] using lookback prior bars.

    `history` is a list of funding rates oldest-first.
    Returns INSUFFICIENT pattern when prior window has < lookback//2 bars.
    """
    if current_idx < 1:
        return FundingCarryScore(
            rate=history[current_idx] if 0 <= current_idx < len(history) else 0.0,
            rolling_mean=0.0,
            rolling_std=0.0,
            z_score=0.0,
            pattern="INSUFFICIENT",
            direction="NEUTRAL",
            net_score=0.0,
            confidence=0.0,
        )
    start = max(0, current_idx - lookback)
    window = history[start:current_idx]
    min_required = max(10, lookback // 3)
    if len(window) < min_required:
        return FundingCarryScore(
            rate=history[current_idx],
            rolling_mean=0.0,
            rolling_std=0.0,
            z_score=0.0,
            pattern="INSUFFICIENT",
            direction="NEUTRAL",
            net_score=0.0,
            confidence=0.0,
        )
    mean = statistics.fmean(window)
    std = statistics.pstdev(window) if len(window) > 1 else 0.0
    rate = history[current_idx]
    if std > 1e-9:
        z = (rate - mean) / std
    else:
        # WHY: degenerate flat window. A nonzero deviation from a constant baseline
        # is still informative — synthesize a hard pseudo-z based on relative scale
        # so calm-market regime breaks still classify correctly.
        delta = rate - mean
        if abs(delta) < max(1e-9, abs(mean) * 0.01):
            z = 0.0
        else:
            # 5σ-equivalent in the direction of the deviation
            z = 5.0 if delta > 0 else -5.0
    pattern, direction, net = _classify_z(z)
    confidence = min(1.0, abs(net) / 3.0)
    return FundingCarryScore(
        rate=rate,
        rolling_mean=mean,
        rolling_std=std,
        z_score=z,
        pattern=pattern,
        direction=direction,
        net_score=net,
        confidence=confidence,
    )


@dataclass(frozen=True, slots=True)
class FundingCarryVerdict:
    symbol: str
    bar_idx: int
    score: FundingCarryScore
    edge_bps: float
    all_in_bps: float
    cost_passed: bool
    action: str            # BUY / SELL / HOLD
    horizon_bars: int


# Binance USDM perpetual cost model:
#   taker fee 0.04% = 4bps, maker 0.02% = 2bps. Round-trip taker = 8bps.
#   Conservative all-in = 8bps + 1bp slippage = 9bps but in mid-cap-friendly form
#   we use 6bps round-trip as the spec stated (assume mostly-maker).
ALL_IN_BPS_BINANCE_PERP: Final[float] = 6.0


def build_verdict(
    symbol: str,
    bar_idx: int,
    score: FundingCarryScore,
    *,
    cost_multiplier: float = 1.5,
    all_in_bps: float = ALL_IN_BPS_BINANCE_PERP,
) -> FundingCarryVerdict:
    edge_bps = abs(score.net_score) * _NET_SCORE_TO_BPS
    cost_passed = edge_bps >= cost_multiplier * all_in_bps
    if score.direction == "NEUTRAL":
        action = "HOLD"
    elif score.direction == "LONG" and cost_passed:
        action = "BUY"
    elif score.direction == "SHORT" and cost_passed:
        action = "SELL"
    else:
        action = "HOLD"
    return FundingCarryVerdict(
        symbol=symbol,
        bar_idx=bar_idx,
        score=score,
        edge_bps=edge_bps,
        all_in_bps=all_in_bps,
        cost_passed=cost_passed,
        action=action,
        horizon_bars=_HORIZON_BARS,
    )


def realized_return(
    closes: list[float],
    *,
    entry_idx: int,
    horizon_bars: int = _HORIZON_BARS,
) -> float | None:
    target = entry_idx + horizon_bars
    if entry_idx < 0 or target >= len(closes):
        return None
    p0 = closes[entry_idx]
    p1 = closes[target]
    if p0 <= 0:
        return None
    return (p1 - p0) / p0


__all__ = [
    "ALL_IN_BPS_BINANCE_PERP",
    "FundingCarryScore",
    "FundingCarryVerdict",
    "build_verdict",
    "realized_return",
    "score_funding_rate",
]
