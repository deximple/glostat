from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Final

import pytest

from glostat.core.types import ExpertSignal, MarketMeta, SessionWindow
from glostat.verdict_builder import build_verdict

# ── Helpers ────────────────────────────────────────────────────────────────


_NOW: Final = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _xnas() -> MarketMeta:
    return MarketMeta(
        mic="XNAS",
        name="NASDAQ",
        country="US",
        currency="USD",
        tz="America/New_York",
        sessions=(SessionWindow("regular", "09:30", "16:00", "14:30", "21:00"),),
        settlement_days=1,
        fee_bps=0.6,
        tax_bps_buy=0.0,
        tax_bps_sell=0.24,
        tick_size="1c",
        holidays_calendar="us_2026.yaml",
        bigdata_mcp_coverage="HIGH",
        foreign_access="open",
    )


def _signal(
    *,
    direction: str = "LONG",
    net_score: float = 1.6,
    confidence: float = 0.6,
    sources: tuple[str, ...] = ("yfinance.info#aaa", "sec_edgar.companyfacts#bbb"),
) -> ExpertSignal:
    return ExpertSignal(
        expert_name="E_FUNDAMENTAL",
        ticker="AAPL",
        direction=direction,  # type: ignore[arg-type]
        net_score=net_score,
        confidence=confidence,
        archetype="continuation",
        basis="PER 25 z=0.4, ROE 0.20 z=0.2",
        sources=sources,
        expires_at=_NOW + timedelta(days=30),
    )


# ── Single-signal passthrough ──────────────────────────────────────────────


def test_single_signal_passthrough_long() -> None:
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal(direction="LONG", net_score=2.0, confidence=0.7)],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={"E_FUNDAMENTAL": "a" * 64},
    )
    assert v.action == "BUY"
    assert v.cost_passed is True
    assert v.disagreement_weight == 1.0


def test_single_signal_passthrough_short() -> None:
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal(direction="SHORT", net_score=-2.0, confidence=0.7)],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={"E_FUNDAMENTAL": "a" * 64},
    )
    assert v.action == "SELL"


def test_single_signal_passthrough_neutral_becomes_hold() -> None:
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal(direction="NEUTRAL", net_score=0.5, confidence=0.2)],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={"E_FUNDAMENTAL": "a" * 64},
    )
    assert v.action == "HOLD"


# ── Cost gate (INV-GS-001) ─────────────────────────────────────────────────


def test_cost_gate_demotion_when_edge_below_threshold() -> None:
    # Sprint 5 PR #1 retune (50 bps/unit): net_score 0.005 → edge_bps 0.25 →
    # all_in ≈ 1.44 → 0.25 < 1.5 × 1.44 = 2.16 → demote to HOLD.
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal(direction="LONG", net_score=0.005, confidence=0.1)],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={"E_FUNDAMENTAL": "a" * 64},
    )
    assert v.cost_passed is False
    assert v.action == "HOLD"


# ── Evidence hash determinism (INV-GS-022 cousin) ──────────────────────────


def test_evidence_hash_deterministic() -> None:
    sig = _signal()
    v1 = build_verdict(ticker="AAPL", signals=[sig], market_meta=_xnas(), ts=_NOW,
                       prompt_versions={"E_FUNDAMENTAL": "a" * 64})
    v2 = build_verdict(ticker="AAPL", signals=[sig], market_meta=_xnas(), ts=_NOW,
                       prompt_versions={"E_FUNDAMENTAL": "a" * 64})
    assert v1.evidence_hash == v2.evidence_hash


def test_evidence_hash_changes_when_sources_change() -> None:
    sig_a = _signal(sources=("yfinance.info#aaa",))
    sig_b = _signal(sources=("yfinance.info#zzz",))
    v_a = build_verdict(ticker="AAPL", signals=[sig_a], market_meta=_xnas(), ts=_NOW,
                        prompt_versions={"E_FUNDAMENTAL": "a" * 64})
    v_b = build_verdict(ticker="AAPL", signals=[sig_b], market_meta=_xnas(), ts=_NOW,
                        prompt_versions={"E_FUNDAMENTAL": "a" * 64})
    assert v_a.evidence_hash != v_b.evidence_hash


# ── Prompt versions passthrough (INV-GS-023) ───────────────────────────────


def test_prompt_versions_passthrough() -> None:
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal()],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={"E_FUNDAMENTAL": "a" * 64, "OTHER": "b" * 64},
    )
    pv = dict(v.prompt_versions)
    assert "E_FUNDAMENTAL" in pv
    assert pv["E_FUNDAMENTAL"] == "a" * 64
    assert pv["OTHER"] == "b" * 64


def test_prompt_versions_default_when_empty() -> None:
    # WHY: Verdict.__post_init__ rejects empty prompt_versions; builder must inject default.
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal()],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={},
    )
    pv = dict(v.prompt_versions)
    assert "E_FUNDAMENTAL" in pv
    assert len(pv["E_FUNDAMENTAL"]) == 64


# ── Horizon defaults & target/stop ─────────────────────────────────────────


def test_horizon_default_30d() -> None:
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal()],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={"E_FUNDAMENTAL": "a" * 64},
    )
    assert v.horizon_days == 30


def test_horizon_custom_clipped() -> None:
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal()],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={"E_FUNDAMENTAL": "a" * 64},
        horizon_days=14,
    )
    assert v.horizon_days == 14


def test_swing_horizon_target_stop_5pct() -> None:
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal(direction="LONG", net_score=2.0, confidence=0.7)],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={"E_FUNDAMENTAL": "a" * 64},
        current_price=200.0,
    )
    assert v.target_price == pytest.approx(210.0)   # +5%
    assert v.stop_price == pytest.approx(190.0)     # -5%


def test_target_stop_omitted_without_price() -> None:
    v = build_verdict(
        ticker="AAPL",
        signals=[_signal()],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={"E_FUNDAMENTAL": "a" * 64},
        current_price=None,
    )
    assert v.target_price is None
    assert v.stop_price is None


# ── Misc ──────────────────────────────────────────────────────────────────


def test_unsupported_market_rejected() -> None:
    bogus = MarketMeta(
        mic="XKRX", name="KOSPI", country="KR", currency="KRW",
        tz="Asia/Seoul",
        sessions=(SessionWindow("regular", "09:00", "15:30", "00:00", "06:30"),),
        settlement_days=2, fee_bps=0.5, tax_bps_buy=0.0, tax_bps_sell=2.5,
        tick_size="1c", holidays_calendar="kr.yaml",
        bigdata_mcp_coverage="MEDIUM", foreign_access="registered_only",
    )
    with pytest.raises(ValueError, match="XNAS/XNYS"):
        build_verdict(ticker="005930", signals=[_signal()], market_meta=bogus, ts=_NOW,
                      prompt_versions={"E_FUNDAMENTAL": "a" * 64})


def test_empty_signals_rejected() -> None:
    with pytest.raises(ValueError, match="at least one"):
        build_verdict(ticker="AAPL", signals=[], market_meta=_xnas(), ts=_NOW,
                      prompt_versions={"E_FUNDAMENTAL": "a" * 64})


# ── Sprint 1 PR #2: multi-signal aggregation ───────────────────────────────


def _time_signal(
    *,
    direction: str = "LONG",
    net_score: float = 1.5,
    confidence: float = 0.75,
    sources: tuple[str, ...] = ("yfinance.history#ohlcv", "yfinance.calendar#cal"),
) -> ExpertSignal:
    return ExpertSignal(
        expert_name="E_TIME",
        ticker="AAPL",
        direction=direction,  # type: ignore[arg-type]
        net_score=net_score,
        confidence=confidence,
        archetype="continuation",
        basis="T=2.0 (3 converge: [65, 129, 172]), earnings in 7d",
        sources=sources,
        expires_at=_NOW + timedelta(days=30),
    )


def test_multi_signal_aggregation_weighted_average() -> None:
    s1 = _signal(direction="LONG", net_score=2.0, confidence=0.8)
    s2 = _time_signal(direction="LONG", net_score=1.0, confidence=0.4)
    v = build_verdict(
        ticker="AAPL",
        signals=[s1, s2],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={},
    )
    # Sprint 1 PR #5: Gating IC-softmax weights — E_FUNDAMENTAL≈0.615, E_TIME≈0.385.
    # Aggregated score = (2.0×0.615 + 1.0×0.385) = 1.615 → edge_bps ≈ 80.77
    # (Sprint 5 PR #1 retune: NET_SCORE_TO_BPS halved 100 → 50).
    assert v.edge_bps == pytest.approx(80.77, abs=0.5)
    assert v.action == "BUY"  # LONG + cost passes


def test_multi_signal_majority_vote_direction() -> None:
    long_strong = _signal(direction="LONG", net_score=2.0, confidence=0.9)
    short_weak = _time_signal(direction="SHORT", net_score=-1.5, confidence=0.2)
    v = build_verdict(
        ticker="AAPL",
        signals=[long_strong, short_weak],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={},
    )
    # LONG carries 0.9 vs SHORT 0.2 confidence → majority LONG.
    assert v.action == "BUY"


def test_multi_signal_disagreement_calculation() -> None:
    # Sprint 1 PR #5: Gating IC weights make E_FUNDAMENTAL (≈0.615) outweigh
    # E_TIME (≈0.385) regardless of confidence → LONG wins with that share.
    long_sig = _signal(direction="LONG", net_score=2.0, confidence=0.5)
    short_sig = _time_signal(direction="SHORT", net_score=-2.0, confidence=0.5)
    v = build_verdict(
        ticker="AAPL", signals=[long_sig, short_sig], market_meta=_xnas(),
        ts=_NOW, prompt_versions={},
    )
    # disagreement_weight = winning direction share of total weight (≈0.615).
    assert v.disagreement_weight == pytest.approx(0.6154, abs=1e-3)


def test_multi_signal_consensus_agreement_one() -> None:
    s1 = _signal(direction="LONG", net_score=2.0, confidence=0.6)
    s2 = _time_signal(direction="LONG", net_score=1.5, confidence=0.7)
    v = build_verdict(
        ticker="AAPL", signals=[s1, s2], market_meta=_xnas(),
        ts=_NOW, prompt_versions={},
    )
    assert v.disagreement_weight == 1.0


def test_multi_signal_inv_gs_029_low_agreement_warn_threshold() -> None:
    # Sprint 1 PR #5: with Gating, IC weights (E_FUNDAMENTAL≈0.615) determine
    # the share, not raw confidence. LONG (E_FUNDAMENTAL) now wins with ≈0.615
    # share — still above the 0.5 warn threshold.
    long_sig = _signal(direction="LONG", net_score=1.6, confidence=0.3)
    short_sig = _time_signal(direction="SHORT", net_score=-1.6, confidence=0.7)
    v = build_verdict(
        ticker="AAPL", signals=[long_sig, short_sig], market_meta=_xnas(),
        ts=_NOW, prompt_versions={},
    )
    assert v.disagreement_weight == pytest.approx(0.6154, abs=1e-3)


def test_multi_signal_evidence_hash_includes_all_sources() -> None:
    s1 = _signal(sources=("yfinance.info#aaa",))
    s2 = _time_signal(sources=("yfinance.history#bbb",))
    v_two = build_verdict(
        ticker="AAPL", signals=[s1, s2], market_meta=_xnas(),
        ts=_NOW, prompt_versions={},
    )
    v_one = build_verdict(
        ticker="AAPL", signals=[s1], market_meta=_xnas(),
        ts=_NOW, prompt_versions={},
    )
    assert v_two.evidence_hash != v_one.evidence_hash


def test_multi_signal_default_prompt_versions_per_expert() -> None:
    s1 = _signal()
    s2 = _time_signal()
    v = build_verdict(
        ticker="AAPL", signals=[s1, s2], market_meta=_xnas(),
        ts=_NOW, prompt_versions={},
    )
    pv = dict(v.prompt_versions)
    assert "E_FUNDAMENTAL" in pv
    assert "E_TIME" in pv
    assert len(pv["E_TIME"]) == 64


def test_multi_signal_inv_gs_008_metadata_preserved() -> None:
    s_time = ExpertSignal(
        expert_name="E_TIME", ticker="AAPL", direction="LONG",
        net_score=1.5, confidence=0.75, archetype="continuation",
        basis="T=2.0", sources=("yfinance.history#xx",),
        expires_at=_NOW + timedelta(days=30),
        metadata=(("bonus_eligible_T", "True"),),
    )
    v = build_verdict(
        ticker="AAPL", signals=[s_time], market_meta=_xnas(),
        ts=_NOW, prompt_versions={},
    )
    # The metadata is preserved on the contributing signal for downstream
    # bonus application (Sprint 2 PR #4 when E_VALUATION lands).
    contrib = v.contributing_signals[0]
    md = dict(contrib.metadata)
    assert md["bonus_eligible_T"] == "True"


def test_zero_confidence_signals_yield_zero_conviction_hold() -> None:
    # Sprint 1 PR #5: under Gating, IC-softmax weights drive aggregation;
    # confidence rolls into aggregated_confidence (→ conviction_w). With
    # both signals at 0.0 confidence, conviction_w → 0 even when IC weights
    # carry the LONG vs SHORT spread, so the verdict still de-escalates.
    s1 = _signal(direction="LONG", net_score=1.0, confidence=0.0)
    s2 = _time_signal(direction="SHORT", net_score=-1.0, confidence=0.0)
    v = build_verdict(
        ticker="AAPL", signals=[s1, s2], market_meta=_xnas(),
        ts=_NOW, prompt_versions={},
    )
    assert v.conviction_w == 0.0
    assert v.suggested_size_pct == 0.0
