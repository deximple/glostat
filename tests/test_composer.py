from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
import yaml

from glostat.core.types import ComposedSignal, ExpertName, ExpertSignal
from glostat.gating.composer import compose
from glostat.gating.network import GatingNetwork

# Composer — fuses MOET A1 + A2 + A3 into ComposedSignal.

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


def _gating() -> GatingNetwork:
    return GatingNetwork()


# ── Composition basics ─────────────────────────────────────────────────────


def test_compose_3_experts_returns_composed_signal() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG", net_score=2.0, confidence=0.7),
        _sig("E_TIME", "LONG", net_score=1.5, confidence=0.6),
        _sig("E_FUND_FLOW", "LONG", net_score=1.8, confidence=0.5),
    ]
    out = compose(signals, _gating())
    assert isinstance(out, ComposedSignal)
    assert out.direction == "LONG"
    assert len(out.per_signal_weights) == 3


def test_compose_aggregated_score_weighted_average() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG", net_score=2.0, confidence=0.7),
        _sig("E_TIME", "LONG", net_score=1.0, confidence=0.5),
    ]
    out = compose(signals, _gating())
    weights = dict(out.per_signal_weights)
    expected = (2.0 * weights["E_FUNDAMENTAL"] + 1.0 * weights["E_TIME"]) / sum(weights.values())
    assert out.aggregated_score == pytest.approx(expected, abs=1e-9)


def test_compose_aggregated_confidence_weighted_average() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG", net_score=2.0, confidence=0.8),
        _sig("E_TIME", "LONG", net_score=1.0, confidence=0.4),
    ]
    out = compose(signals, _gating())
    weights = dict(out.per_signal_weights)
    expected = (0.8 * weights["E_FUNDAMENTAL"] + 0.4 * weights["E_TIME"]) / sum(weights.values())
    assert out.aggregated_confidence == pytest.approx(expected, abs=1e-9)


# ── Disagreement ───────────────────────────────────────────────────────────


def test_compose_disagreement_calculation() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG", net_score=2.0, confidence=0.5),
        _sig("E_TIME", "SHORT", net_score=-2.0, confidence=0.5),
    ]
    out = compose(signals, _gating())
    # E_FUNDAMENTAL share is ≈0.615 → LONG wins → agreement ≈ 0.615.
    assert out.disagreement_weight == pytest.approx(0.6154, abs=1e-3)
    assert out.direction == "LONG"


def test_compose_consensus_direction_share_one() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG"),
        _sig("E_TIME", "LONG"),
        _sig("E_FUND_FLOW", "LONG"),
    ]
    out = compose(signals, _gating())
    assert out.disagreement_weight == pytest.approx(1.0, abs=1e-9)


# ── Single-signal passthrough ──────────────────────────────────────────────


def test_compose_single_signal_passthrough() -> None:
    sole = _sig("E_FUNDAMENTAL", "LONG", net_score=1.8, confidence=0.7)
    out = compose([sole], _gating())
    assert out.direction == "LONG"
    assert out.aggregated_score == pytest.approx(1.8, abs=1e-9)
    assert out.aggregated_confidence == pytest.approx(0.7, abs=1e-9)
    assert dict(out.per_signal_weights) == {"E_FUNDAMENTAL": pytest.approx(1.0)}
    assert out.applied_anti_herd is False
    assert out.applied_minority_premium == ()


# ── Anti-herd recording ────────────────────────────────────────────────────


def test_compose_records_anti_herd_applied() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG"),
        _sig("E_TIME", "LONG"),
        _sig("E_FUND_FLOW", "LONG"),
        _sig("E_NARRATIVE", "LONG"),
    ]
    out = compose(signals, _gating())
    assert out.applied_anti_herd is True


def test_compose_records_anti_herd_not_applied_at_3() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG"),
        _sig("E_TIME", "LONG"),
        _sig("E_FUND_FLOW", "LONG"),
    ]
    out = compose(signals, _gating())
    assert out.applied_anti_herd is False


# ── Minority premium recording ─────────────────────────────────────────────


def test_compose_records_minority_premium_applied() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG"),
        _sig("E_TIME", "LONG"),
        _sig("E_FUND_FLOW", "SHORT"),  # lone dissenter
    ]
    out = compose(signals, _gating())
    assert "E_FUND_FLOW" in out.applied_minority_premium


def test_compose_records_minority_premium_can_be_empty_with_unanimity() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG"),
        _sig("E_TIME", "LONG"),
        _sig("E_FUND_FLOW", "LONG"),
    ]
    out = compose(signals, _gating())
    assert out.applied_minority_premium == ()


# ── Configuration ─────────────────────────────────────────────────────────


def test_compose_uses_gating_yaml_weights(tmp_path: Path) -> None:
    cfg_path = tmp_path / "g.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "initial_ic": {"E_FUNDAMENTAL": 0.10, "E_TIME": 0.90},
        "weight_caps": {"E_FUNDAMENTAL": 1.0, "E_TIME": 1.0},
        "softmax": {"temperature": 1.0, "entropy_lambda": 0.0},
    }), encoding="utf-8")
    g = GatingNetwork(config_path=cfg_path)
    signals = [
        _sig("E_FUNDAMENTAL", "LONG", net_score=2.0, confidence=0.5),
        _sig("E_TIME", "LONG", net_score=0.0, confidence=0.5),
    ]
    out = compose(signals, g)
    weights = dict(out.per_signal_weights)
    # E_TIME IC dominates → its weight > E_FUNDAMENTAL.
    assert weights["E_TIME"] > weights["E_FUNDAMENTAL"]


# ── Edges ─────────────────────────────────────────────────────────────────


def test_compose_empty_signals_raises() -> None:
    with pytest.raises(ValueError):
        compose([], _gating())


def test_compose_anti_herd_attenuates_weights() -> None:
    # Sanity: with anti-herd ON, total weight before normalization shrinks but
    # since aggregated_score divides by total, the normalized score itself
    # should still equal weighted-average of net_scores.
    signals_3 = [
        _sig("E_FUNDAMENTAL", "LONG", net_score=2.0, confidence=0.5),
        _sig("E_TIME", "LONG", net_score=2.0, confidence=0.5),
        _sig("E_FUND_FLOW", "LONG", net_score=2.0, confidence=0.5),
    ]
    signals_4 = [*signals_3, _sig("E_NARRATIVE", "LONG", net_score=2.0, confidence=0.5)]
    out_3 = compose(signals_3, _gating())
    out_4 = compose(signals_4, _gating())
    # Both unanimous LONG → score is the same weighted mean even with anti-herd ON.
    assert out_3.applied_anti_herd is False
    assert out_4.applied_anti_herd is True
    # Score doesn't change because the discount is uniform across all signals.
    assert out_4.aggregated_score == pytest.approx(out_3.aggregated_score, abs=1e-9)
