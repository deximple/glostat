from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from glostat.predictor.calibration import ThesisCalibration

# 5-component confidence model — N4.
# TITAN chart_pattern.py _compute_confidence(...) inspired, adapted to our
# per-thesis ThesisCalibration shape. Composite is a geometric mean of the 5
# components so a single weak component can pull the overall confidence down
# (TITAN's arithmetic 0.25/0.20/0.20/0.20/0.15 weighting was chosen for
# heuristic balance; geometric mean is the user-requested behaviour here and
# more closely mirrors a "weakest-link" intuition).
#
# Component inventory (each ∈ [0, 1]):
#   1. sample_quality       = log(min(n, 1000)) / log(1000)
#                             diminishing returns above n=1000.
#   2. effective_size_factor= sqrt(n / (n + 50))
#                             Bayesian shrinkage anchor at n_prior=50.
#   3. score_stability      = 1 - (std(rolling_aucs) / mean(aucs))
#                             clamped to [0, 1]; 0 when mean is ~0.
#   4. return_consistency   = 1 - |IS_sharpe - OOS_sharpe| / max(|IS_sharpe|, 0.1)
#                             clamped to [0, 1].
#   5. recency_quality      = exp(-days_since_last_calibration / 90)
#                             half-life ~ 62 days.
#
# composite = (c1 · c2 · c3 · c4 · c5) ** (1 / 5)  (geometric mean)

_FLOOR: Final[float] = 1e-6
_RECENCY_HALF_LIFE_DAYS: Final[float] = 90.0
_BAYES_PRIOR_N: Final[float] = 50.0
_SAMPLE_LOG_DENOM: Final[float] = math.log(1000.0)


@dataclass(frozen=True, slots=True)
class ConfidenceV2:
    sample_quality: float
    effective_size_factor: float
    score_stability: float
    return_consistency: float
    recency_quality: float
    composite_confidence: float

    def __post_init__(self) -> None:
        for name in (
            "sample_quality", "effective_size_factor", "score_stability",
            "return_consistency", "recency_quality", "composite_confidence",
        ):
            v = getattr(self, name)
            if not 0.0 <= v <= 1.0:
                raise ValueError(f"{name} {v} out of [0, 1]")


def _clamp01(x: float) -> float:
    if math.isnan(x):
        return 0.0
    return max(0.0, min(1.0, x))


def _sample_quality(n: int) -> float:
    if n <= 0:
        return 0.0
    capped = min(n, 1000)
    return _clamp01(math.log(capped) / _SAMPLE_LOG_DENOM) if capped > 1 else 0.0


def _effective_size_factor(n: int) -> float:
    if n <= 0:
        return 0.0
    ratio = n / (n + _BAYES_PRIOR_N)
    return _clamp01(math.sqrt(ratio))


def _score_stability(rolling_aucs: Sequence[float]) -> float:
    if not rolling_aucs:
        return 0.0
    mean = sum(rolling_aucs) / len(rolling_aucs)
    if abs(mean) < _FLOOR:
        return 0.0
    if len(rolling_aucs) == 1:
        # Single AUC point — assume perfect stability (no variance to measure).
        return 1.0
    var = sum((a - mean) ** 2 for a in rolling_aucs) / (len(rolling_aucs) - 1)
    std = math.sqrt(var)
    return _clamp01(1.0 - std / abs(mean))


def _return_consistency(is_sharpe: float, oos_sharpe: float) -> float:
    denom = max(abs(is_sharpe), 0.1)
    diff = abs(is_sharpe - oos_sharpe)
    return _clamp01(1.0 - diff / denom)


def _recency_quality(days_since_last_calibration: float) -> float:
    if days_since_last_calibration < 0:
        days_since_last_calibration = 0.0
    return _clamp01(math.exp(-days_since_last_calibration / _RECENCY_HALF_LIFE_DAYS))


def _geometric_mean(values: Sequence[float]) -> float:
    # If any component is ~0, geometric mean → 0; floor avoids math.log(0).
    log_sum = 0.0
    for v in values:
        log_sum += math.log(max(v, _FLOOR))
    return _clamp01(math.exp(log_sum / len(values)))


def compute_confidence_v2(
    *,
    n_samples: int,
    is_sharpe: float,
    oos_sharpe: float,
    days_since_last_calibration: float,
    rolling_aucs: Sequence[float] | None = None,
) -> ConfidenceV2:
    rolling = tuple(rolling_aucs) if rolling_aucs is not None else ()
    sq = _sample_quality(n_samples)
    esf = _effective_size_factor(n_samples)
    ss = _score_stability(rolling)
    rc = _return_consistency(is_sharpe, oos_sharpe)
    rq = _recency_quality(days_since_last_calibration)
    composite = _geometric_mean((sq, esf, ss, rc, rq))
    return ConfidenceV2(
        sample_quality=sq,
        effective_size_factor=esf,
        score_stability=ss,
        return_consistency=rc,
        recency_quality=rq,
        composite_confidence=composite,
    )


def confidence_v2_from_calibration(
    cal: ThesisCalibration,
    *,
    days_since_last_calibration: float | None = None,
    rolling_aucs: Sequence[float] | None = None,
    is_sharpe: float | None = None,
    oos_sharpe: float | None = None,
) -> ConfidenceV2:
    # Defaults derived from ThesisCalibration when caller omits explicit fields.
    # WHY: ThesisCalibration carries `oos_degradation` (1.0 = total OOS collapse)
    # and `sharpe`; we synthesize is_sharpe / oos_sharpe to keep the consistency
    # component meaningful without changing the storage shape.
    sharpe = cal.sharpe
    derived_is = is_sharpe if is_sharpe is not None else sharpe
    if oos_sharpe is not None:
        derived_oos = oos_sharpe
    else:
        # oos_degradation ∈ [0, ~5]; map back to an OOS sharpe proxy.
        derived_oos = sharpe * max(0.0, 1.0 - cal.oos_degradation)
    if days_since_last_calibration is None:
        from datetime import date  # noqa: PLC0415 — cold path
        today = date.today()
        days_since_last_calibration = max(0.0, float((today - cal.period_end).days))
    rolling = (
        tuple(rolling_aucs) if rolling_aucs is not None
        else (cal.auc,)
    )
    return compute_confidence_v2(
        n_samples=cal.n_samples,
        is_sharpe=derived_is,
        oos_sharpe=derived_oos,
        days_since_last_calibration=days_since_last_calibration,
        rolling_aucs=rolling,
    )


__all__ = [
    "ConfidenceV2",
    "compute_confidence_v2",
    "confidence_v2_from_calibration",
]
