from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from glostat.core.types import ExpertName, ExpertSignal, MarketMeta, SessionWindow
from glostat.gating import GatingNetwork, compose
from glostat.verdict_builder import build_verdict

# Sprint 1 PR #5 — INV enforcement on the gating layer.

_NOW: Final = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _xnas() -> MarketMeta:
    return MarketMeta(
        mic="XNAS", name="NASDAQ", country="US", currency="USD",
        tz="America/New_York",
        sessions=(SessionWindow("regular", "09:30", "16:00", "14:30", "21:00"),),
        settlement_days=1, fee_bps=0.6, tax_bps_buy=0.0, tax_bps_sell=0.24,
        tick_size="1c", holidays_calendar="us_2026.yaml",
        bigdata_mcp_coverage="HIGH", foreign_access="open",
    )


def _sig(
    name: str,
    direction: str = "LONG",
    net_score: float = 1.5,
    confidence: float = 0.6,
) -> ExpertSignal:
    expert: ExpertName = name  # type: ignore[assignment]
    return ExpertSignal(
        expert_name=expert, ticker="AAPL",
        direction=direction,  # type: ignore[arg-type]
        net_score=net_score, confidence=confidence,
        archetype="continuation", basis=f"{name}",
        sources=(f"src#{name}",),
        expires_at=_NOW + timedelta(days=30),
    )


# ── INV-GS-005: anti-herd at 4 experts ─────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_005_anti_herd_4_experts() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG"),
        _sig("E_TIME", "LONG"),
        _sig("E_FUND_FLOW", "LONG"),
        _sig("E_NARRATIVE", "LONG"),
    ]
    out = compose(signals, GatingNetwork())
    assert out.applied_anti_herd is True


@pytest.mark.invariant
def test_inv_gs_005_visible_in_verdict_next_trigger() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG", net_score=2.0, confidence=0.7),
        _sig("E_TIME", "LONG", net_score=2.0, confidence=0.7),
        _sig("E_FUND_FLOW", "LONG", net_score=2.0, confidence=0.7),
        _sig("E_NARRATIVE", "LONG", net_score=2.0, confidence=0.7),
    ]
    v = build_verdict(
        ticker="AAPL", signals=signals, market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    assert "anti_herd=ON" in v.next_trigger


# ── INV-GS-001: cost gate after compose ────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_001_cost_gate_after_compose() -> None:
    # Composed score so small that edge_bps < 1.5 × all_in_bps → action HOLD.
    signals = [
        _sig("E_FUNDAMENTAL", "LONG", net_score=0.005, confidence=0.05),
    ]
    v = build_verdict(
        ticker="AAPL", signals=signals, market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    assert v.cost_passed is False
    assert v.action == "HOLD"


@pytest.mark.invariant
def test_inv_gs_001_cost_passes_with_strong_signal() -> None:
    signals = [
        _sig("E_FUNDAMENTAL", "LONG", net_score=2.5, confidence=0.7),
    ]
    v = build_verdict(
        ticker="AAPL", signals=signals, market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    # edge_bps = 250 vs all_in ≈ 1.44 → easily passes 1.5× cost gate.
    assert v.cost_passed is True
    assert v.action == "BUY"


# ── INV-GS-003: E_NARRATIVE cap ≤ 15% (deferred config validates) ──────────


@pytest.mark.invariant
def test_inv_gs_003_e_narrative_cap_in_config() -> None:
    g = GatingNetwork()
    # E_NARRATIVE is deferred → derive_weights excludes it in MVP.
    # The cap config still records the 0.15 ceiling for Phase 2 wiring.
    assert g.cap_for("E_NARRATIVE") == pytest.approx(0.15, abs=1e-9)
    assert "E_NARRATIVE" in g.deferred_experts


@pytest.mark.invariant
def test_inv_gs_003_e_narrative_excluded_from_weights() -> None:
    g = GatingNetwork()
    w = g.derive_weights(["E_FUNDAMENTAL", "E_NARRATIVE", "E_TIME"])
    assert "E_NARRATIVE" not in w


# ── INV-GS-029: disagreement_weight semantics consistent ───────────────────


@pytest.mark.invariant
def test_disagreement_weight_semantics_consistent() -> None:
    # types.py docstring: 1.0 = consensus, 0.0 = total split.
    consensus = compose(
        [_sig("E_FUNDAMENTAL", "LONG"), _sig("E_TIME", "LONG")],
        GatingNetwork(),
    )
    assert consensus.disagreement_weight == pytest.approx(1.0, abs=1e-9)

    split = compose(
        [_sig("E_FUNDAMENTAL", "LONG"), _sig("E_TIME", "SHORT")],
        GatingNetwork(),
    )
    # Not 0.0 because IC gives weighted majority, but should be < 1.0.
    assert split.disagreement_weight < 1.0


@pytest.mark.invariant
def test_disagreement_weight_within_unit_interval() -> None:
    for direction_b in ("LONG", "SHORT", "NEUTRAL"):
        out = compose(
            [_sig("E_FUNDAMENTAL", "LONG"), _sig("E_TIME", direction_b)],
            GatingNetwork(),
        )
        assert 0.0 <= out.disagreement_weight <= 1.0


# ── Backward compat: single-signal still passes through cleanly ────────────


@pytest.mark.invariant
def test_single_signal_passthrough_through_builder() -> None:
    sole = _sig("E_FUNDAMENTAL", "LONG", net_score=2.0, confidence=0.7)
    v = build_verdict(
        ticker="AAPL", signals=[sole], market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    # No anti-herd, no minority premium with single signal.
    assert "anti_herd=ON" not in v.next_trigger
    assert v.disagreement_weight == pytest.approx(1.0, abs=1e-9)
