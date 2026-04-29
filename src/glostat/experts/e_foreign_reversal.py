from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from glostat.data.naver_kr_client import KrFlowBar

# E_FOREIGN_REVERSAL (Phase 1D Thesis E9) — port of TITAN B4 REVERSAL_BUY pattern.
# Universe: KOSPI 200 (top liquidity).
# Pattern: 외국인 (foreign) 4 consecutive trading days of NET SELL,
#          followed by Day D+1 first NET BUY → LONG.
# Confirmation: 기관 (institutional) also NET BUY same day → confidence × 1.3.
# Validated by TITAN news_engine.py: 60.3% hit rate on 58 events (2025.06–2026.03).

_REQUIRED_PRIOR_SELL_DAYS: Final[int] = 4
_PATTERN_NET_SCORE: Final[float] = 2.0  # base LONG score for REVERSAL_BUY
_CONFIRM_BOOST: Final[float] = 1.3      # × confidence when 기관 also buying
_NET_SCORE_TO_BPS: Final[float] = 60.0  # composite score → expected bps
_HORIZON_DAYS: Final[int] = 7           # short-swing horizon for reversal pattern


@dataclass(frozen=True, slots=True)
class ForeignReversalScore:
    code: str
    bar_idx: int
    pattern: str           # REVERSAL_BUY / NEUTRAL / INSUFFICIENT
    direction: str         # LONG / NEUTRAL
    consec_sell_days: int
    organ_confirms: bool
    net_score: float
    confidence: float

    @property
    def is_skip(self) -> bool:
        return self.pattern == "INSUFFICIENT"


def score_reversal_at(
    bars: list[KrFlowBar],
    *,
    current_idx: int,
    required_prior: int = _REQUIRED_PRIOR_SELL_DAYS,
) -> ForeignReversalScore:
    """Score TITAN B4 REVERSAL_BUY at bars[current_idx].

    Pattern triggers when:
      - bars[current_idx].foreign_net > 0 (today is buy day)
      - bars[current_idx-1..current_idx-required_prior].foreign_net < 0 (consecutive sell)
    Returns INSUFFICIENT pattern when prior history is shorter than required.
    """
    if current_idx < 0 or current_idx >= len(bars):
        return _insufficient(code="", bar_idx=current_idx)
    bar = bars[current_idx]
    if current_idx < required_prior:
        return _insufficient(code=bar.code, bar_idx=current_idx)
    today_buy = bar.foreign_net > 0
    if not today_buy:
        # Not a buy day → no reversal trigger; emit NEUTRAL (not skip)
        return ForeignReversalScore(
            code=bar.code,
            bar_idx=current_idx,
            pattern="NEUTRAL",
            direction="NEUTRAL",
            consec_sell_days=0,
            organ_confirms=False,
            net_score=0.0,
            confidence=0.0,
        )
    consec_sells = 0
    for prior_offset in range(1, required_prior + 1):
        prior_idx = current_idx - prior_offset
        if prior_idx < 0:
            break
        if bars[prior_idx].foreign_net < 0:
            consec_sells += 1
        else:
            break
    if consec_sells < required_prior:
        return ForeignReversalScore(
            code=bar.code,
            bar_idx=current_idx,
            pattern="NEUTRAL",
            direction="NEUTRAL",
            consec_sell_days=consec_sells,
            organ_confirms=False,
            net_score=0.0,
            confidence=0.0,
        )
    # REVERSAL_BUY confirmed
    organ_confirms = bar.organ_net > 0
    base_conf = min(0.9, 0.3 + consec_sells * 0.1)
    confidence = min(1.0, base_conf * (_CONFIRM_BOOST if organ_confirms else 1.0))
    net_score = _PATTERN_NET_SCORE * confidence
    return ForeignReversalScore(
        code=bar.code,
        bar_idx=current_idx,
        pattern="REVERSAL_BUY",
        direction="LONG",
        consec_sell_days=consec_sells,
        organ_confirms=organ_confirms,
        net_score=net_score,
        confidence=confidence,
    )


def _insufficient(code: str, bar_idx: int) -> ForeignReversalScore:
    return ForeignReversalScore(
        code=code, bar_idx=bar_idx, pattern="INSUFFICIENT",
        direction="NEUTRAL", consec_sell_days=0, organ_confirms=False,
        net_score=0.0, confidence=0.0,
    )


@dataclass(frozen=True, slots=True)
class ForeignReversalVerdict:
    code: str
    bar_idx: int
    score: ForeignReversalScore
    edge_bps: float
    all_in_bps: float
    cost_passed: bool
    action: str            # BUY / HOLD
    horizon_days: int


# KR all-in cost: KRX broker fee (~0.015%) + KR transaction tax 0.20% sell + spread 2-3bps.
# Round-trip ≈ 1.5bps fee + 20bps tax + 3bps spread ≈ 24.5bps. Use 22bps per spec.
ALL_IN_BPS_KR: Final[float] = 22.0


def build_verdict(
    score: ForeignReversalScore,
    *,
    cost_multiplier: float = 1.5,
    all_in_bps: float = ALL_IN_BPS_KR,
) -> ForeignReversalVerdict:
    edge_bps = abs(score.net_score) * _NET_SCORE_TO_BPS
    cost_passed = edge_bps >= cost_multiplier * all_in_bps
    if score.direction == "LONG" and cost_passed:
        action = "BUY"
    else:
        action = "HOLD"
    return ForeignReversalVerdict(
        code=score.code,
        bar_idx=score.bar_idx,
        score=score,
        edge_bps=edge_bps,
        all_in_bps=all_in_bps,
        cost_passed=cost_passed,
        action=action,
        horizon_days=_HORIZON_DAYS,
    )


def realized_return(
    bars: list[KrFlowBar],
    *,
    entry_idx: int,
    horizon_days: int = _HORIZON_DAYS,
) -> float | None:
    target_idx = entry_idx + horizon_days
    if entry_idx < 0 or target_idx >= len(bars):
        return None
    p0 = bars[entry_idx].close_price
    p1 = bars[target_idx].close_price
    if p0 <= 0:
        return None
    return (p1 - p0) / p0


__all__ = [
    "ALL_IN_BPS_KR",
    "ForeignReversalScore",
    "ForeignReversalVerdict",
    "build_verdict",
    "realized_return",
    "score_reversal_at",
]
