from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from glostat.core.types import ExpertName, ExpertSignal
from glostat.gating.anti_herd import (
    DEFAULT_DISCOUNT,
    DEFAULT_THRESHOLD,
    anti_herd_triggered,
    apply_anti_herd_discount,
    majority_direction_count,
)

# MOET A2 — anti-herd discount (INV-GS-005).

_NOW: Final = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _sig(
    name: str,
    direction: str = "LONG",
    net_score: float = 1.5,
    confidence: float = 0.6,
) -> ExpertSignal:
    expert: ExpertName = name  # type: ignore[assignment]
    return ExpertSignal(
        expert_name=expert,
        ticker="AAPL",
        direction=direction,  # type: ignore[arg-type]
        net_score=net_score,
        confidence=confidence,
        archetype="continuation",
        basis=f"{name} basis",
        sources=(f"src#{name}",),
        expires_at=_NOW + timedelta(days=30),
    )


# ── 3 LONG (under threshold) ───────────────────────────────────────────────


def test_3_long_no_discount() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG"),
        _sig("E_TIME", "LONG"),
        _sig("E_FUND_FLOW", "LONG"),
    ]
    mult = apply_anti_herd_discount(signals, threshold=4)
    assert all(m == 1.0 for m in mult.values())
    assert anti_herd_triggered(signals, threshold=4) is False


# ── 4 LONG (≥ threshold → discount fires) ──────────────────────────────────


def test_4_long_triggers_discount() -> None:
    # Synthetic 4-expert agreement — names are not in our 3-expert MVP whitelist
    # but ExpertSignal accepts any string post-construction.
    signals = [
        _sig("E_FUNDAMENTAL", "LONG"),
        _sig("E_TIME", "LONG"),
        _sig("E_FUND_FLOW", "LONG"),
        _sig("E_NARRATIVE", "LONG"),  # 4th LONG → trigger
    ]
    mult = apply_anti_herd_discount(signals, threshold=4)
    assert all(m == DEFAULT_DISCOUNT for m in mult.values())
    assert anti_herd_triggered(signals, threshold=4) is True


def test_4_long_uses_custom_discount() -> None:
    signals = [_sig(f"X{i}", "LONG") for i in range(4)]
    mult = apply_anti_herd_discount(signals, threshold=4, discount=0.5)
    assert all(m == 0.5 for m in mult.values())


# ── Mixed directions ───────────────────────────────────────────────────────


def test_3_long_1_short_no_discount() -> None:
    signals = [
        _sig("A", "LONG"),
        _sig("B", "LONG"),
        _sig("C", "LONG"),
        _sig("D", "SHORT"),  # max LONG count = 3, threshold = 4
    ]
    mult = apply_anti_herd_discount(signals, threshold=4)
    assert all(m == 1.0 for m in mult.values())


def test_4_long_1_short_triggers_discount() -> None:
    signals = [
        _sig("A", "LONG"),
        _sig("B", "LONG"),
        _sig("C", "LONG"),
        _sig("D", "LONG"),   # 4 LONG
        _sig("E", "SHORT"),
    ]
    mult = apply_anti_herd_discount(signals, threshold=4)
    # Anti-herd applies UNIFORMLY: every signal multiplier becomes 0.80, including dissenters.
    assert mult["A"] == DEFAULT_DISCOUNT
    assert mult["E"] == DEFAULT_DISCOUNT


def test_4_short_triggers_discount() -> None:
    signals = [_sig(f"X{i}", "SHORT") for i in range(4)]
    assert anti_herd_triggered(signals, threshold=4) is True


def test_4_neutral_triggers_discount() -> None:
    # NEUTRAL-as-direction also counts toward herd.
    signals = [_sig(f"X{i}", "NEUTRAL") for i in range(4)]
    assert anti_herd_triggered(signals, threshold=4) is True


# ── Edges ──────────────────────────────────────────────────────────────────


def test_empty_signals_returns_empty() -> None:
    assert apply_anti_herd_discount([]) == {}
    assert anti_herd_triggered([]) is False


def test_single_signal_no_discount() -> None:
    mult = apply_anti_herd_discount([_sig("A", "LONG")], threshold=4)
    assert mult == {"A": 1.0}


def test_threshold_can_be_lowered() -> None:
    # Lower threshold to 3 → 3 LONG should trigger.
    signals = [_sig(f"X{i}", "LONG") for i in range(3)]
    mult = apply_anti_herd_discount(signals, threshold=3)
    assert all(m == DEFAULT_DISCOUNT for m in mult.values())


def test_default_threshold_is_4() -> None:
    assert DEFAULT_THRESHOLD == 4
    assert DEFAULT_DISCOUNT == 0.80


def test_majority_direction_count() -> None:
    signals = [
        _sig("A", "LONG"),
        _sig("B", "LONG"),
        _sig("C", "SHORT"),
    ]
    direction, count = majority_direction_count(signals)
    assert direction == "LONG"
    assert count == 2


def test_majority_direction_empty() -> None:
    assert majority_direction_count([]) == ("NEUTRAL", 0)


# ── INV-GS-005 enforcement marker ──────────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_005_enforce_marker() -> None:
    # The PR contract: when 4+ experts agree, the discount fires AND it can be
    # observed externally (verdict_builder records the flag in next_trigger).
    signals = [_sig(f"X{i}", "LONG") for i in range(4)]
    mult = apply_anti_herd_discount(signals)
    assert all(m == 0.80 for m in mult.values()), "INV-GS-005 must apply 0.80×"
