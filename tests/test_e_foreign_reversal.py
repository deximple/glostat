from __future__ import annotations

from datetime import date, timedelta

import pytest

from glostat.data.naver_kr_client import KrFlowBar
from glostat.experts.e_foreign_reversal import (
    ALL_IN_BPS_KR,
    build_verdict,
    realized_return,
    score_reversal_at,
)


def _bar(
    code: str, d: date, *, foreign: float, organ: float = 0.0, close: float = 100.0
) -> KrFlowBar:
    return KrFlowBar(
        code=code, bar_date=d, close_price=close,
        organ_net=organ, foreign_net=foreign,
        foreign_holdings=0, foreign_hold_pct=0.0,
    )


def _seq(code: str, foreigns: list[float], organs: list[float] | None = None) -> list[KrFlowBar]:
    if organs is None:
        organs = [0.0] * len(foreigns)
    base = date(2026, 1, 1)
    return [
        _bar(code, base + timedelta(days=i), foreign=f, organ=o, close=100 + i)
        for i, (f, o) in enumerate(zip(foreigns, organs, strict=True))
    ]


def test_reversal_insufficient_when_history_too_short() -> None:
    bars = _seq("005930", [-1, -1, 1])
    score = score_reversal_at(bars, current_idx=2, required_prior=4)
    assert score.is_skip
    assert score.pattern == "INSUFFICIENT"


def test_reversal_neutral_when_today_is_sell() -> None:
    bars = _seq("005930", [-1, -1, -1, -1, -1])
    score = score_reversal_at(bars, current_idx=4, required_prior=4)
    assert score.pattern == "NEUTRAL"
    assert score.direction == "NEUTRAL"


def test_reversal_neutral_when_consec_sells_short() -> None:
    bars = _seq("005930", [-1, -1, +1, -1, -1, +1])
    score = score_reversal_at(bars, current_idx=5, required_prior=4)
    # Only 2 prior consec sells; not enough
    assert score.pattern == "NEUTRAL"
    assert score.consec_sell_days == 2


def test_reversal_buy_triggers_after_4_consec_sells() -> None:
    bars = _seq("005930", [-1, -1, -1, -1, -1, +1])
    score = score_reversal_at(bars, current_idx=5, required_prior=4)
    assert score.pattern == "REVERSAL_BUY"
    assert score.direction == "LONG"
    assert score.consec_sell_days == 4
    assert score.net_score > 0
    assert not score.organ_confirms


def test_reversal_buy_organ_confirmation_boosts_confidence() -> None:
    foreigns = [-1, -1, -1, -1, -1, +1]
    organs_no = [0, 0, 0, 0, 0, 0]
    organs_yes = [0, 0, 0, 0, 0, +1]
    no = score_reversal_at(_seq("X", foreigns, organs_no), current_idx=5, required_prior=4)
    yes = score_reversal_at(_seq("X", foreigns, organs_yes), current_idx=5, required_prior=4)
    assert yes.organ_confirms is True
    assert no.organ_confirms is False
    assert yes.confidence > no.confidence
    assert yes.net_score > no.net_score


def test_build_verdict_cost_gate_for_reversal() -> None:
    bars = _seq("005930", [-1, -1, -1, -1, -1, +1], organs=[0, 0, 0, 0, 0, +1])
    score = score_reversal_at(bars, current_idx=5, required_prior=4)
    verdict = build_verdict(score)
    # net_score ≈ 2.0 × 0.91 ≈ 1.82, × 60bps = 109bps; 1.5 × 22 = 33 → passes
    assert verdict.cost_passed
    assert verdict.action == "BUY"
    assert verdict.all_in_bps == ALL_IN_BPS_KR


def test_realized_return_uses_close_prices() -> None:
    bars = _seq("005930", [+1] * 10)  # closes 100..109
    r = realized_return(bars, entry_idx=0, horizon_days=7)
    assert r == pytest.approx((107 - 100) / 100)


def test_realized_return_returns_none_past_horizon() -> None:
    bars = _seq("005930", [+1] * 5)
    assert realized_return(bars, entry_idx=0, horizon_days=7) is None
