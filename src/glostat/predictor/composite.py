from __future__ import annotations

import functools
import hashlib
import math
import subprocess
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from typing import Final

import structlog

from glostat.predictor.calibration import (
    CalibrationTable,
    ThesisCalibration,
    is_active,
    load_calibration,
)
from glostat.predictor.confidence_v2 import (
    ConfidenceV2,
    confidence_v2_from_calibration,
)
from glostat.predictor.dca_sizing import build_sizing_recommendation
from glostat.predictor.types import (
    Direction,
    Horizon,
    Prediction,
    SignalContribution,
    default_disclaimer,
    prediction_sha256,
)

# Composite predictor — fuses per-thesis SignalContributions into a single
# probability + expected return + confidence interval.
# Math summary (full derivation in docs/CALIBRATION.md):
#   1. weight_raw(s) = max(0, 1 - 4 * brier(c))   # brier∈[0,0.25] → weight∈[0,1]
#      v1.4 (INV-GS-112): weight = weight_raw × confidence_v2.composite (geo mean)
#   2. directional bias: +1 if AUC>0.5, -1 if AUC<0.5 (flip), 0 at exactly 0.5
#   3. p_up = base_rate · (1 - α) + mass · α, α = total_w / (total_w + 1)
#   4. expected_return_bps from signed value × _SCORE_TO_BPS × weight
#   5. sigma_bps = stdev × sqrt(active_count)
#   6. v1.4 (INV-GS-111): SizingRecommendation attached as INFORMATION-only.

log: Final = structlog.get_logger(__name__)

_DEFAULT_BASE_RATE_UP: Final[float] = 0.50  # symmetric prior; refined per-horizon below
_HORIZON_BASE_RATES: Final[dict[Horizon, float]] = {
    "intraday":  0.50,
    "swing_5d":  0.51,
    "swing_30d": 0.52,
    "long_3y":   0.62,
}
_HORIZON_DAYS: Final[dict[Horizon, int]] = {
    "intraday": 1,
    "swing_5d": 5,
    "swing_30d": 30,
    "long_3y": 1095,
}

# Tunable — kept in module scope so review/tests can override.
_SCORE_TO_BPS: Final[float] = 50.0
_RETURN_DOWN_SCALE: Final[float] = 1.0
_PROB_TOL: Final[float] = 1e-9
_MIN_NEUTRAL_FLOOR: Final[float] = 0.05
_MAX_MASS_BLEND_ALPHA: Final[float] = 0.90


def _brier_to_weight(brier: float) -> float:
    # WHY: brier ∈ [0, 0.25]; map linearly to a [0, 1] weight. brier=0 (perfect)
    # → weight=1; brier=0.25 (random, max uncertainty) → weight=0.
    raw = 1.0 - 4.0 * brier
    return max(0.0, min(1.0, raw))


def _weight_for(cal: ThesisCalibration) -> float:
    if not is_active(cal):
        return 0.0
    return _brier_to_weight(cal.brier_score)


def _weight_for_v2(cal: ThesisCalibration, conf: ConfidenceV2) -> float:
    # v1.4 N4 (INV-GS-112): final weight = brier_weight × confidence_v2 composite.
    # Geometric mean confidence acts as a damping multiplier — high-n stable
    # thesis keeps near-full weight; n=0 / stale / unstable thesis collapses to 0.
    base = _weight_for(cal)
    if base <= 0.0:
        return 0.0
    return base * conf.composite_confidence


def _flip_direction(d: Direction) -> Direction:
    if d == "up":
        return "down"
    if d == "down":
        return "up"
    return d  # "neutral" / "skip" unchanged


def _effective_direction(
    s: SignalContribution, bias: int
) -> Direction:
    if s.direction == "skip":
        return "skip"
    if bias < 0:
        return _flip_direction(s.direction)
    return s.direction


def _compute_masses(
    contributions: Iterable[SignalContribution],
    cal_table: CalibrationTable,
) -> tuple[float, float, float, float, list[tuple[SignalContribution, float, int]]]:
    # Returns (mass_up, mass_down, mass_neutral, total_weight, [(s, weight, bias), ...])
    # v1.4 N4 (INV-GS-112): weight = brier_weight × confidence_v2.composite_confidence.
    mass_up = 0.0
    mass_down = 0.0
    mass_neutral = 0.0
    total = 0.0
    rows: list[tuple[SignalContribution, float, int]] = []
    for s in contributions:
        if s.direction == "skip":
            rows.append((s, 0.0, 0))
            continue
        cal = cal_table.get(s.name)
        conf = confidence_v2_from_calibration(cal)
        weight = _weight_for_v2(cal, conf)
        bias = cal.directional_bias
        eff = _effective_direction(s, bias)
        if eff == "up":
            mass_up += weight
        elif eff == "down":
            mass_down += weight
        else:
            mass_neutral += weight
        total += weight
        rows.append((s, weight, bias))
    return (mass_up, mass_down, mass_neutral, total, rows)


def _attach_confidence_v2(
    contributions: Iterable[SignalContribution],
    cal_table: CalibrationTable,
) -> tuple[SignalContribution, ...]:
    # WHY: SignalContribution is frozen — rebuild each entry with confidence_v2
    # populated so downstream consumers (CLI, JSON, audit) can see the breakdown.
    out: list[SignalContribution] = []
    for s in contributions:
        cal = cal_table.get(s.name)
        conf = confidence_v2_from_calibration(cal)
        out.append(SignalContribution(
            name=s.name,
            value=s.value,
            direction=s.direction,
            calibration_auc=s.calibration_auc,
            calibration_sharpe=s.calibration_sharpe,
            n_samples=s.n_samples,
            skip_reason=s.skip_reason,
            source_snapshot_ids=s.source_snapshot_ids,
            confidence_v2=conf,
        ))
    return tuple(out)


def _blend_with_baseline(
    mass: float,
    total_weight: float,
    base_rate: float,
) -> float:
    if total_weight <= _PROB_TOL:
        return base_rate
    alpha = min(_MAX_MASS_BLEND_ALPHA, total_weight / (total_weight + 1.0))
    return (1.0 - alpha) * base_rate + alpha * mass


def _expected_return_and_sigma(
    rows: list[tuple[SignalContribution, float, int]],
    cal_table: CalibrationTable,
) -> tuple[float, float]:
    contribs_bps: list[float] = []
    total_weight = 0.0
    for s, weight, bias in rows:
        if s.direction == "skip" or s.value is None or weight <= _PROB_TOL:
            continue
        cal = cal_table.get(s.name)
        # Sharpe acts as a confidence multiplier; clip to a moderate band so a
        # +0.78 micro-sample doesn't dominate. Sign of bias flips under-random.
        sharpe_term = max(-1.0, min(1.0, cal.sharpe))
        signed_value = s.value * (1.0 if bias >= 0 else -1.0)
        contribution = signed_value * _SCORE_TO_BPS * weight * (0.5 + 0.5 * abs(sharpe_term))
        contribs_bps.append(contribution)
        total_weight += weight
    if total_weight <= _PROB_TOL:
        return (0.0, 0.0)
    expected = sum(contribs_bps) / total_weight * _RETURN_DOWN_SCALE
    if len(contribs_bps) <= 1:
        sigma = abs(expected) * 0.5 + 5.0
    else:
        mean = expected
        variance = sum((c - mean) ** 2 for c in contribs_bps) / max(1, len(contribs_bps) - 1)
        sigma = math.sqrt(variance) * math.sqrt(len(contribs_bps))
    return (expected, sigma)


def _evidence_hash(contributions: Iterable[SignalContribution]) -> str:
    leaves: list[str] = []
    for s in contributions:
        leaves.extend(s.source_snapshot_ids)
        leaves.append(f"{s.name}|{s.direction}|{s.value if s.value is not None else 'skip'}")
    if not leaves:
        return hashlib.sha256(b"empty-prediction").hexdigest()
    leaves.sort()
    h = hashlib.sha256()
    for leaf in leaves:
        h.update(leaf.encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


@functools.cache
def _git_commit() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        )
        return out.strip()[:40] or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def _prompt_versions(contributions: Iterable[SignalContribution]) -> tuple[tuple[str, str], ...]:
    versions: dict[str, str] = {}
    for s in contributions:
        versions[s.name] = hashlib.sha256(
            f"{s.name}@predictor-formulaic-v1".encode()
        ).hexdigest()
    return tuple(sorted(versions.items()))


def _next_triggers(
    contributions: Iterable[SignalContribution], horizon: Horizon
) -> tuple[str, ...]:
    triggers: list[str] = []
    h_days = _HORIZON_DAYS[horizon]
    triggers.append(f"horizon expires in ~{h_days} days")
    for s in contributions:
        if s.direction == "skip":
            continue
        if s.name == "E_PEAD":
            triggers.append("Next earnings release → PEAD re-evaluation")
        elif s.name == "E_FOMC_DRIFT":
            triggers.append("Next FOMC announcement → drift signal refresh")
        elif s.name == "E_FUNDAMENTAL":
            triggers.append("Next 10-Q filing → fundamental re-score")
    # Dedup while preserving order.
    seen: set[str] = set()
    out: list[str] = []
    for t in triggers:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return tuple(out)


def _ensure_neutral_floor(
    p_up: float, p_down: float, p_neutral: float
) -> tuple[float, float, float]:
    # WHY: avoid a degenerate (1.0, 0.0, 0.0) print when one mass dominates.
    # Reserve a small floor for sideways outcome to keep the user honest.
    floor = _MIN_NEUTRAL_FLOOR
    if p_neutral >= floor:
        return _normalize_three(p_up, p_down, p_neutral)
    deficit = floor - p_neutral
    # Steal proportionally from the larger of up/down.
    total_directional = p_up + p_down
    if total_directional <= _PROB_TOL:
        return _normalize_three(0.5, 0.5, 0.0)
    p_up_new = p_up - deficit * (p_up / total_directional)
    p_down_new = p_down - deficit * (p_down / total_directional)
    return _normalize_three(p_up_new, p_down_new, floor)


def _normalize_three(p_up: float, p_down: float, p_neutral: float) -> tuple[float, float, float]:
    parts = [max(0.0, p_up), max(0.0, p_down), max(0.0, p_neutral)]
    total = sum(parts)
    if total <= _PROB_TOL:
        return (1.0 / 3, 1.0 / 3, 1.0 / 3)
    return (parts[0] / total, parts[1] / total, parts[2] / total)


def predict(
    *,
    ticker: str,
    horizon: Horizon,
    contributions: tuple[SignalContribution, ...],
    cal_table: CalibrationTable | None = None,
    issued_at: datetime | None = None,
    base_rate_up: float | None = None,
    market: str = "XNAS",
) -> Prediction:
    table = cal_table or load_calibration()
    ts = issued_at or datetime.now(tz=UTC)
    base = base_rate_up if base_rate_up is not None else _HORIZON_BASE_RATES.get(
        horizon, _DEFAULT_BASE_RATE_UP
    )
    if not contributions:
        raise ValueError("predict requires at least one SignalContribution")
    mass_up, mass_down, mass_neutral, total_w, rows = _compute_masses(contributions, table)
    if total_w <= _PROB_TOL:
        # Fall back to base rate prior — nothing weighted enough to shift it.
        p_up = base
        p_down = (1.0 - base) * 0.5
        p_neutral = (1.0 - base) * 0.5
    else:
        norm_up = mass_up / total_w
        norm_down = mass_down / total_w
        norm_neutral = mass_neutral / total_w
        p_up = _blend_with_baseline(norm_up, total_w, base)
        # WHY: baseline for "down" assumes symmetric residual once "up" is taken.
        residual = 1.0 - base
        baseline_down = residual * 0.7
        baseline_neutral = residual * 0.3
        p_down = _blend_with_baseline(norm_down, total_w, baseline_down)
        p_neutral = _blend_with_baseline(norm_neutral, total_w, baseline_neutral)
        p_up, p_down, p_neutral = _normalize_three(p_up, p_down, p_neutral)
    p_up, p_down, p_neutral = _ensure_neutral_floor(p_up, p_down, p_neutral)
    expected_bps, sigma_bps = _expected_return_and_sigma(rows, table)
    edge_pp = (p_up - base) * 100.0
    # v1.4 N4: enrich contributions with per-thesis confidence_v2 breakdown.
    enriched = _attach_confidence_v2(contributions, table)
    pred = Prediction(
        ticker=ticker.upper(),
        horizon=horizon,
        issued_at=ts,
        up_probability=p_up,
        down_probability=p_down,
        sideways_probability=p_neutral,
        expected_return_bps=expected_bps,
        confidence_interval_bps=(expected_bps - sigma_bps, expected_bps + sigma_bps),
        base_rate_up=base,
        edge_over_baseline_pp=edge_pp,
        contributing_signals=enriched,
        next_triggers=_next_triggers(contributions, horizon),
        evidence_hash=_evidence_hash(contributions),
        prompt_versions=_prompt_versions(contributions),
        disclaimer=default_disclaimer(),
        calibration_period=_calibration_period_from_table(table),
        git_commit=_git_commit(),
        market=market,
    )
    # v1.4 N3 (INV-GS-111): attach DCA sizing as INFORMATION-only metadata.
    return replace(pred, dca_sizing=build_sizing_recommendation(pred))


def _calibration_period_from_table(table: CalibrationTable) -> tuple[date, date]:
    # WHY: every cached entry holds a (start, end). Use the widest window
    # across active entries; fall back to the canonical default when empty.
    if not table.entries:
        return (date(2024, 1, 1), date(2026, 3, 31))
    start = min(c.period_start for c in table.entries.values())
    end = max(c.period_end for c in table.entries.values())
    return (start, end)


def horizon_to_days(h: Horizon) -> int:
    return _HORIZON_DAYS[h]


def horizon_to_timedelta(h: Horizon) -> timedelta:
    return timedelta(days=_HORIZON_DAYS[h])


__all__ = [
    "horizon_to_days",
    "horizon_to_timedelta",
    "predict",
    "prediction_sha256",
]
