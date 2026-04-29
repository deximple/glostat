from __future__ import annotations

from collections.abc import Iterable
from typing import Final

from glostat.core.types import ComposedSignal, ExpertSignal
from glostat.gating.anti_herd import (
    DEFAULT_DISCOUNT,
    DEFAULT_THRESHOLD,
    anti_herd_triggered,
    apply_anti_herd_discount,
)
from glostat.gating.minority_premium import (
    DEFAULT_BOOST,
    ApproveFn,
    apply_minority_premium,
    boosted_experts,
)
from glostat.gating.network import GatingNetwork

# Composer — single entrypoint that fuses MOET A1 (gating weights) + A2
# (anti-herd) + A3 (minority premium) into a ComposedSignal. Pure function;
# verdict_builder consumes the result and applies INV-GS-001 cost gate after.

_EPS: Final[float] = 1e-9


def compose(
    signals: Iterable[ExpertSignal],
    gating: GatingNetwork,
    *,
    anti_herd_threshold: int = DEFAULT_THRESHOLD,
    anti_herd_discount: float = DEFAULT_DISCOUNT,
    minority_boost: float = DEFAULT_BOOST,
    approve_callback: ApproveFn | None = None,
) -> ComposedSignal:
    sig_list = tuple(signals)
    if not sig_list:
        raise ValueError("compose requires at least one ExpertSignal")

    # 1. Base weights from IC-softmax + entropy + caps.
    base = gating.derive_weights([s.expert_name for s in sig_list])
    base = _fallback_uniform(base, sig_list)

    # 2. Anti-herd discount (INV-GS-005).
    ah_mult = apply_anti_herd_discount(
        sig_list, threshold=anti_herd_threshold, discount=anti_herd_discount
    )
    ah_applied = anti_herd_triggered(sig_list, threshold=anti_herd_threshold)

    # 3. Minority premium (MOET A3).
    mp_mult = apply_minority_premium(
        sig_list, boost=minority_boost, approve_callback=approve_callback
    )
    boosted = boosted_experts(mp_mult, boost=minority_boost)

    # 4. Final per-signal weight = base × ah × mp.
    final_w: dict[str, float] = {
        s.expert_name: base.get(s.expert_name, 0.0)
        * ah_mult.get(s.expert_name, 1.0)
        * mp_mult.get(s.expert_name, 1.0)
        for s in sig_list
    }
    total_w = sum(final_w.values())

    # 5. Aggregations.
    if total_w <= _EPS:
        # WHY: all weights collapsed (e.g. zero-IC experts) → NEUTRAL consensus.
        return ComposedSignal(
            aggregated_score=0.0,
            aggregated_confidence=0.0,
            direction="NEUTRAL",
            disagreement_weight=1.0,
            per_signal_weights=tuple(sorted(final_w.items())),
            applied_anti_herd=ah_applied,
            applied_minority_premium=boosted,
            source_signals=sig_list,
        )

    agg_score = sum(s.net_score * final_w[s.expert_name] for s in sig_list) / total_w
    agg_conf = sum(s.confidence * final_w[s.expert_name] for s in sig_list) / total_w
    direction, agreement = _vote_direction(sig_list, final_w)

    return ComposedSignal(
        aggregated_score=agg_score,
        aggregated_confidence=agg_conf,
        direction=direction,
        disagreement_weight=agreement,
        per_signal_weights=tuple(sorted(final_w.items())),
        applied_anti_herd=ah_applied,
        applied_minority_premium=boosted,
        source_signals=sig_list,
    )


def _fallback_uniform(
    base: dict[str, float], signals: tuple[ExpertSignal, ...]
) -> dict[str, float]:
    # WHY: defensive fallback when an Expert is missing from gating.yaml — give
    # it equal weight rather than dropping it silently. This keeps backward-
    # compat with future Experts shipped before configs/gating.yaml updates.
    if base:
        return base
    n = len(signals)
    return {s.expert_name: 1.0 / n for s in signals}


def _vote_direction(
    signals: tuple[ExpertSignal, ...], weights: dict[str, float]
) -> tuple[str, float]:
    # WHY: weighted vote per direction; ties broken by NEUTRAL. Returns
    # (direction, agreement_share) where agreement_share ∈ [0, 1] is the
    # winning direction's share of total weight (1.0 = consensus).
    direction_w: dict[str, float] = {}
    for s in signals:
        w = weights.get(s.expert_name, 0.0)
        direction_w[s.direction] = direction_w.get(s.direction, 0.0) + w
    total = sum(direction_w.values())
    if total <= _EPS:
        return ("NEUTRAL", 1.0)
    top_dir, top_w = max(direction_w.items(), key=lambda kv: kv[1])
    return (str(top_dir), top_w / total)


__all__ = ["compose"]
