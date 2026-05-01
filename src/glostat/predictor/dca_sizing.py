from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from glostat.predictor.types import Prediction

# DCA sizing — N3.
# TITAN L4 W값 공식을 GLOSTAT prediction-tool framing으로 옮긴 것.
# INV-GS-101 보존: 어떤 BUY/SELL action 출력도 없음.
# 출력은 "사용자가 진입을 결정했을 경우 conviction에 비례한 추천 분율"이라는
# INFORMATION (calibration-derived) 이며, 진입을 권하는 것이 아니다.
#
# TITAN 원공식: W = 0.30·R + 0.25·T + 0.25·V + 0.20·S  (+ bonus, cap 3.5)
#   R = regime gate score      (0..3)
#   T = time/convergence       (0..2)
#   V = valuation score        (0..3)
#   S = sentiment score        (0..2)
#
# GLOSTAT 매핑 (prediction-tool framing 보존):
#   R ← 거시/regime 신호 강도 (E_MACRO_KR / E_FOMC_DRIFT 등이 active 일 때)
#   T ← time-convergence 신호 (E_TIME / E_TIME_KR / E_PEAD 같이 시간축 catalyst)
#   V ← fundamental valuation (E_FUNDAMENTAL / E_FUNDAMENTAL_KR)
#   S ← composite signal strength (edge × calibration confidence)
#
# 각 인자는 [0, MAX] 범위로 정규화 후 W 합산. W ≤ 3.5 cap.

W_CAP: Final[float] = 3.5

_W_R: Final[float] = 0.30
_W_T: Final[float] = 0.25
_W_V: Final[float] = 0.25
_W_S: Final[float] = 0.20

_R_MAX: Final[float] = 3.0
_T_MAX: Final[float] = 2.0
_V_MAX: Final[float] = 3.0
_S_MAX: Final[float] = 2.0

_REGIME_THESES: Final[tuple[str, ...]] = (
    "E_MACRO_KR", "E_FOMC_DRIFT", "E_FX_CARRY",
)
_TIME_THESES: Final[tuple[str, ...]] = (
    "E_TIME", "E_TIME_KR", "E_PEAD",
)
_VALUATION_THESES: Final[tuple[str, ...]] = (
    "E_FUNDAMENTAL", "E_FUNDAMENTAL_KR",
)

_DISCLAIMER: Final[str] = (
    "INFORMATION ONLY — sizing tier reflects prediction strength, not "
    "advice to enter or size positions. INV-GS-101 + INV-GS-104 + INV-GS-111."
)

Tier = Literal["wait", "explore", "base", "active", "aggressive"]


@dataclass(frozen=True, slots=True)
class SizingRecommendation:
    tier: Tier
    suggested_entry_pct: float
    w_value: float
    w_components: tuple[float, float, float, float]  # (R, T, V, S)
    disclaimer: str = _DISCLAIMER

    def __post_init__(self) -> None:
        if not 0.0 <= self.suggested_entry_pct <= 100.0:
            raise ValueError(
                f"suggested_entry_pct {self.suggested_entry_pct} out of [0, 100]"
            )
        if not 0.0 <= self.w_value <= W_CAP:
            raise ValueError(f"w_value {self.w_value} out of [0, {W_CAP}]")
        if len(self.w_components) != 4:
            raise ValueError(
                f"w_components must be 4-tuple (R,T,V,S), got {len(self.w_components)}"
            )


def _clamp(x: float, lo: float, hi: float) -> float:
    if math.isnan(x):
        return lo
    return max(lo, min(hi, x))


def _r_score(p: Prediction) -> float:
    # R: regime/macro fit. Sum the |raw value| of active regime-themed contributions
    # weighted by their direction agreeing with composite p_up direction.
    composite_dir = +1.0 if p.up_probability >= p.down_probability else -1.0
    score = 0.0
    for s in p.contributing_signals:
        if s.name not in _REGIME_THESES:
            continue
        if s.direction == "skip" or s.value is None:
            continue
        sign = +1.0 if s.direction == "up" else (-1.0 if s.direction == "down" else 0.0)
        agree = sign * composite_dir
        score += abs(s.value) * max(0.0, agree)
    if score == 0.0:
        # Neutral macro = R baseline 1.0 (mid-band); v0.6/v1.0의 base rate 측면에서
        # 거시 신호가 비어있어도 W가 0으로 무너지지 않도록 mild floor 부여.
        score = 1.0
    return _clamp(score, 0.0, _R_MAX)


def _t_score(p: Prediction) -> float:
    # T: time-convergence catalyst. Same structure as R but for time-themed thesis.
    composite_dir = +1.0 if p.up_probability >= p.down_probability else -1.0
    score = 0.0
    for s in p.contributing_signals:
        if s.name not in _TIME_THESES:
            continue
        if s.direction == "skip" or s.value is None:
            continue
        sign = +1.0 if s.direction == "up" else (-1.0 if s.direction == "down" else 0.0)
        agree = sign * composite_dir
        # |value| in [0, ~3] domain; scale into T_MAX=[0, 2].
        score += abs(s.value) * max(0.0, agree) * (_T_MAX / 3.0)
    return _clamp(score, 0.0, _T_MAX)


def _v_score(p: Prediction) -> float:
    # V: fundamental valuation. Scale absolute fundamental score into V_MAX=[0, 3].
    composite_dir = +1.0 if p.up_probability >= p.down_probability else -1.0
    score = 0.0
    for s in p.contributing_signals:
        if s.name not in _VALUATION_THESES:
            continue
        if s.direction == "skip" or s.value is None:
            continue
        sign = +1.0 if s.direction == "up" else (-1.0 if s.direction == "down" else 0.0)
        agree = sign * composite_dir
        score += abs(s.value) * max(0.0, agree)
    return _clamp(score, 0.0, _V_MAX)


def _s_score(p: Prediction) -> float:
    # S: composite signal strength. Map edge_over_baseline_pp into [0, 2].
    # +20pp edge → S=2.0; 0pp → S=0; -10pp → S=0 (clamped).
    edge_pp = max(0.0, p.edge_over_baseline_pp)
    score = (edge_pp / 20.0) * _S_MAX
    return _clamp(score, 0.0, _S_MAX)


def compute_w_value(prediction: Prediction) -> float:
    r = _r_score(prediction)
    t = _t_score(prediction)
    v = _v_score(prediction)
    s = _s_score(prediction)
    w_raw = _W_R * r + _W_T * t + _W_V * v + _W_S * s
    return _clamp(w_raw, 0.0, W_CAP)


def _w_components(prediction: Prediction) -> tuple[float, float, float, float]:
    return (
        _r_score(prediction),
        _t_score(prediction),
        _v_score(prediction),
        _s_score(prediction),
    )


def _tier_for_w(w: float) -> tuple[Tier, float]:
    # TITAN L4 §6 thresholds preserved verbatim.
    if w < 0.8:
        return ("wait", 0.0)
    if w < 1.2:
        return ("explore", 7.0)
    if w < 1.8:
        return ("base", 12.5)
    if w < 2.5:
        return ("active", 22.5)
    return ("aggressive", 32.5)


def w_to_sizing_recommendation(w: float) -> SizingRecommendation:
    w_clamped = _clamp(w, 0.0, W_CAP)
    tier, pct = _tier_for_w(w_clamped)
    return SizingRecommendation(
        tier=tier,
        suggested_entry_pct=pct,
        w_value=w_clamped,
        w_components=(0.0, 0.0, 0.0, 0.0),
    )


def build_sizing_recommendation(prediction: Prediction) -> SizingRecommendation:
    components = _w_components(prediction)
    w = (
        _W_R * components[0]
        + _W_T * components[1]
        + _W_V * components[2]
        + _W_S * components[3]
    )
    w_clamped = _clamp(w, 0.0, W_CAP)
    tier, pct = _tier_for_w(w_clamped)
    return SizingRecommendation(
        tier=tier,
        suggested_entry_pct=pct,
        w_value=w_clamped,
        w_components=components,
    )


__all__ = [
    "W_CAP",
    "SizingRecommendation",
    "Tier",
    "build_sizing_recommendation",
    "compute_w_value",
    "w_to_sizing_recommendation",
]
