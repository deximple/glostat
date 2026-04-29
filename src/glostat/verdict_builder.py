from __future__ import annotations

import functools
import hashlib
import subprocess
import warnings
from collections.abc import Mapping
from datetime import datetime
from typing import Final, Literal

import structlog

from glostat.core.types import Action, ComposedSignal, ExpertSignal, MarketMeta, Verdict
from glostat.gating import GatingNetwork, compose

# v1.0 DEPRECATION: verdict_builder is the legacy decision-engine path
# (BUY/HOLD/SELL action). v1.0 reframes GLOSTAT as a prediction tool — see
# `glostat.predictor.composite.predict()` for the supported `Prediction` API
# (probability + evidence). This module remains importable for backward
# compatibility and continues to power `glostat verdict` (deprecated CLI).
# Deprecation surfaces at the CLI command boundary, not on import, to avoid
# polluting the test suite with import-time warnings.
_VERDICT_BUILDER_DEPRECATED: Final[str] = (
    "glostat.verdict_builder is deprecated; use glostat.predictor.composite.predict "
    "instead for the v1.0 Prediction surface."
)


_DEPRECATION_EMITTED: list[bool] = [False]


def _emit_deprecation_once() -> None:
    if _DEPRECATION_EMITTED[0]:
        return
    _DEPRECATION_EMITTED[0] = True
    warnings.warn(_VERDICT_BUILDER_DEPRECATED, DeprecationWarning, stacklevel=3)

# Verdict assembly from one or more ExpertSignals.
# Sprint 1 PR #1: single-Expert passthrough.
# Sprint 1 PR #2: multi-Expert composition (confidence-weighted) + agreement_weight
#   semantics matching INV-GS-029 ("disagreement_weight" in Verdict, but stored as
#   AGREEMENT — 1.0 = consensus, 0.0 = total split). UX warns when < 0.5.
# Sprint 1 PR #5: replace simple weighted-avg with Gating composer (MOET A1+A2+A3).
#   INV-GS-005 anti-herd visible in Verdict.next_trigger / metadata; INV-GS-001
#   cost-gate now applies AFTER the composer derives aggregated_score → edge_bps.

log: Final = structlog.get_logger(__name__)

_SWING_HORIZON_DAYS: Final[int] = 30
_TARGET_PCT: Final[float] = 0.05
_STOP_PCT: Final[float] = 0.05
_CONVICTION_TO_W: Final[float] = 2.5
_W_TO_SIZE_PCT: Final[float] = 3.0
_MAX_SIZE_PCT: Final[float] = 10.0
_COST_GATE_RATIO: Final[float] = 1.5
# Sprint 5 PR #1 — halved 100 → 50 bps per unit of composed score. PR #3 live
# run measured cost_passed_pct=87-91% (gate effectively silent); halving the
# score → bps conversion brings the gate into the [40%, 80%] band the kill
# criteria expect. Physical interpretation: 1.0 unit of composed score equals
# 50 bps of expected edge (down from 100 — over-stated for the megacap
# universe where tax + fee already runs <2 bps round trip).
_NET_SCORE_TO_BPS: Final[float] = 50.0
_DEFAULT_USER_PROFILE: Final[str] = "personal_use_default"

# Process-wide singleton — config is read-only, no point reloading per call.
# Single-element list avoids `global` (PLW0603) while staying frozen-by-convention.
_DEFAULT_GATING: list[GatingNetwork] = []


def _get_default_gating() -> GatingNetwork:
    if not _DEFAULT_GATING:
        _DEFAULT_GATING.append(GatingNetwork())
    return _DEFAULT_GATING[0]


def build_verdict(
    *,
    ticker: str,
    signals: list[ExpertSignal],
    market_meta: MarketMeta,
    ts: datetime,
    prompt_versions: Mapping[str, str],
    current_price: float | None = None,
    next_trigger: str | None = None,
    horizon_days: int = _SWING_HORIZON_DAYS,
    user_profile_id: str = _DEFAULT_USER_PROFILE,
    gating: GatingNetwork | None = None,
) -> Verdict:
    _emit_deprecation_once()
    if not signals:
        raise ValueError("build_verdict requires at least one ExpertSignal")
    if market_meta.mic not in {"XNAS", "XNYS"}:
        raise ValueError(
            f"Sprint 1 PR #1: only XNAS/XNYS supported (got {market_meta.mic})"
        )

    composed = compose(signals, gating or _get_default_gating())

    all_in_bps = market_meta.all_in_bps("buy") + market_meta.all_in_bps("sell")
    edge_bps = abs(composed.aggregated_score) * _NET_SCORE_TO_BPS
    cost_passed = edge_bps >= _COST_GATE_RATIO * all_in_bps

    action = _action_from_direction(composed.direction, cost_passed)
    conviction_w = min(3.5, max(0.0, composed.aggregated_confidence * _CONVICTION_TO_W))
    # INV-GS-008 hook: if any signal flags bonus_eligible_T AND a future V signal
    # also clears its threshold, apply ×1.2 to conviction. Sprint 1 PR #2 stores
    # the eligibility flag only — bonus application waits for E_VALUATION.
    # TODO(Sprint 2 PR #4): add V check + apply 1.2× when both pass.
    suggested_size = max(0.0, min(_MAX_SIZE_PCT, conviction_w * _W_TO_SIZE_PCT))

    if current_price is not None and current_price > 0:
        target_price = current_price * (1.0 + _TARGET_PCT * _direction_sign(composed.direction))
        stop_price = current_price * (1.0 - _STOP_PCT * _direction_sign(composed.direction))
    else:
        target_price, stop_price = None, None

    expected_pnl = edge_bps
    final_next_trigger = next_trigger or (
        f"{horizon_days}d swing window expires {_iso_date_only(ts)}"
        + _gating_suffix(composed)
    )
    evidence_hash = _evidence_hash(signals)
    git_commit = _git_commit_hash()
    user_hash = hashlib.sha256(user_profile_id.encode("utf-8")).hexdigest()

    pv: tuple[tuple[str, str], ...] = (
        tuple(sorted(prompt_versions.items()))
        if prompt_versions
        else _default_prompt_versions(signals)
    )

    market_lit: Literal["XNAS", "XNYS"] = market_meta.mic  # type: ignore[assignment]

    return Verdict(
        ticker=ticker,
        action=action,
        conviction_w=conviction_w,
        target_price=target_price,
        stop_price=stop_price,
        suggested_size_pct=suggested_size,
        horizon_days=horizon_days,
        edge_bps=edge_bps,
        all_in_bps=all_in_bps,
        cost_passed=cost_passed,
        expected_pnl_bps=expected_pnl,
        disagreement_weight=composed.disagreement_weight,
        contributing_signals=tuple(signals),
        next_trigger=final_next_trigger,
        evidence_hash=evidence_hash,
        prompt_versions=pv,
        git_commit=git_commit,
        user_profile_hash=user_hash,
        issued_at=ts,
        market=market_lit,
    )


def _action_from_direction(direction: str, cost_passed: bool) -> Action:
    if direction == "LONG" and cost_passed:
        return "BUY"
    if direction == "SHORT" and cost_passed:
        return "SELL"
    return "HOLD"


def _direction_sign(direction: str) -> float:
    if direction == "LONG":
        return 1.0
    if direction == "SHORT":
        return -1.0
    return 0.0


def _gating_suffix(composed: ComposedSignal) -> str:
    parts: list[str] = []
    if composed.applied_anti_herd:
        parts.append("anti_herd=ON")
    if composed.applied_minority_premium:
        parts.append("minority_premium=" + ",".join(composed.applied_minority_premium))
    return f" [{'; '.join(parts)}]" if parts else ""


def _evidence_hash(signals: list[ExpertSignal]) -> str:
    # WHY: deterministic Merkle leaf over the source snapshot ids — same inputs
    # produce same hash, enabling INV-GS-022 replay verification.
    leaves: list[str] = []
    for sig in signals:
        for src in sig.sources:
            leaves.append(src)
    if not leaves:
        return hashlib.sha256(b"empty").hexdigest()
    leaves.sort()
    h = hashlib.sha256()
    for leaf in leaves:
        h.update(leaf.encode("utf-8"))
        h.update(b"\x1e")
    return h.hexdigest()


@functools.cache
def _git_commit_hash() -> str:
    # WHY: cache once per process — git HEAD doesn't change mid-run and the subprocess
    # call dominates hindcast latency at 50 tickers × 60+ days (~3200 verdicts).
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()[:40] or "unknown"
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def _default_prompt_versions(signals: list[ExpertSignal]) -> tuple[tuple[str, str], ...]:
    # WHY: experts without an LLM call still need a registered placeholder per
    # INV-GS-023. Generate one per contributing expert so multi-Expert verdicts
    # still satisfy the invariant.
    versions: dict[str, str] = {}
    for sig in signals:
        versions[sig.expert_name] = hashlib.sha256(
            f"{sig.expert_name}@no-llm-formulaic-v1".encode()
        ).hexdigest()
    return tuple(sorted(versions.items()))


def _iso_date_only(ts: datetime) -> str:
    return ts.date().isoformat()


def compose_signals(
    signals: list[ExpertSignal], gating: GatingNetwork | None = None
) -> ComposedSignal:
    # Convenience re-export so CLI / screen callers can render the breakdown
    # without re-running the full builder.
    return compose(signals, gating or _get_default_gating())


__all__ = ["build_verdict", "compose_signals"]
