from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

from glostat.core.types import ExpertName, ExpertSignal
from glostat.gating.minority_premium import (
    DEFAULT_BOOST,
    apply_minority_premium,
    boosted_experts,
)

# MOET A3 — minority premium + Meta-adjudicator (PLAN_v0.4 §0.3).

_NOW: Final = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _sig(name: str, direction: str = "LONG") -> ExpertSignal:
    expert: ExpertName = name  # type: ignore[assignment]
    return ExpertSignal(
        expert_name=expert,
        ticker="AAPL",
        direction=direction,  # type: ignore[arg-type]
        net_score=1.5,
        confidence=0.6,
        archetype="continuation",
        basis=f"{name} basis",
        sources=(f"src#{name}",),
        expires_at=_NOW + timedelta(days=30),
    )


# ── Boost behavior ─────────────────────────────────────────────────────────


def test_2_long_1_short_minority_short_boosted() -> None:
    signals = [
        _sig("A", "LONG"),
        _sig("B", "LONG"),
        _sig("C", "SHORT"),  # lone dissenter — minority
    ]
    mult = apply_minority_premium(signals)
    assert mult["A"] == 1.0
    assert mult["B"] == 1.0
    assert mult["C"] == DEFAULT_BOOST


def test_all_long_no_minority() -> None:
    signals = [_sig("A", "LONG"), _sig("B", "LONG"), _sig("C", "LONG")]
    mult = apply_minority_premium(signals)
    assert all(m == 1.0 for m in mult.values())


def test_two_directions_tied_no_boost() -> None:
    # Tie → ambiguous minority → no boost (avoid double-promotion).
    signals = [_sig("A", "LONG"), _sig("B", "SHORT")]
    mult = apply_minority_premium(signals)
    assert all(m == 1.0 for m in mult.values())


def test_three_way_tie_no_boost() -> None:
    signals = [_sig("A", "LONG"), _sig("B", "SHORT"), _sig("C", "NEUTRAL")]
    mult = apply_minority_premium(signals)
    # All three count = 1 → tie → no minority promoted.
    assert all(m == 1.0 for m in mult.values())


def test_long_majority_neutral_minority_boosted() -> None:
    signals = [
        _sig("A", "LONG"),
        _sig("B", "LONG"),
        _sig("C", "LONG"),
        _sig("D", "NEUTRAL"),
    ]
    mult = apply_minority_premium(signals)
    assert mult["D"] == DEFAULT_BOOST
    assert mult["A"] == 1.0


# ── Meta-adjudicator approve_callback ──────────────────────────────────────


def test_approve_callback_can_reject() -> None:
    signals = [_sig("A", "LONG"), _sig("B", "LONG"), _sig("C", "SHORT")]
    mult = apply_minority_premium(signals, approve_callback=lambda _s: False)
    # Veto → no boost even for the minority.
    assert all(m == 1.0 for m in mult.values())


def test_approve_callback_per_signal() -> None:
    # Approve only signals named "C" — others get rejected.
    signals = [
        _sig("A", "LONG"),
        _sig("B", "SHORT"),
        _sig("C", "SHORT"),
    ]
    # SHORT count = 2, LONG count = 1 → minority is LONG.
    mult = apply_minority_premium(signals, approve_callback=lambda s: s.expert_name == "A")
    assert mult["A"] == DEFAULT_BOOST  # only minority signal A approved


def test_meta_adjudicator_default_approves_in_mvp() -> None:
    # No callback supplied → MVP default = always approve.
    signals = [_sig("A", "LONG"), _sig("B", "LONG"), _sig("C", "SHORT")]
    mult = apply_minority_premium(signals)
    assert mult["C"] == DEFAULT_BOOST


# ── Boost factor ───────────────────────────────────────────────────────────


def test_custom_boost_factor() -> None:
    signals = [_sig("A", "LONG"), _sig("B", "LONG"), _sig("C", "SHORT")]
    mult = apply_minority_premium(signals, boost=1.5)
    assert mult["C"] == 1.5


def test_default_boost_is_1_15() -> None:
    assert DEFAULT_BOOST == 1.15


# ── Edges ──────────────────────────────────────────────────────────────────


def test_empty_signals_returns_empty() -> None:
    assert apply_minority_premium([]) == {}


def test_single_signal_no_boost() -> None:
    mult = apply_minority_premium([_sig("A", "LONG")])
    # Single signal — no minority, no majority → no boost.
    assert mult["A"] == 1.0


def test_boosted_experts_helper() -> None:
    mult = {"A": 1.0, "B": 1.15, "C": 1.0, "D": 1.5}
    assert boosted_experts(mult, boost=1.15) == ("B", "D")
    assert boosted_experts({}) == ()
