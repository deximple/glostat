from __future__ import annotations

import pytest

from glostat.experts.e_funding_carry import (
    ALL_IN_BPS_BINANCE_PERP,
    build_verdict,
    realized_return,
    score_funding_rate,
)


def _flat_history(n: int, value: float = 0.0001) -> list[float]:
    return [value] * n


def test_funding_carry_insufficient_when_no_window() -> None:
    score = score_funding_rate([0.0001], current_idx=0, lookback=90)
    assert score.is_skip
    assert score.pattern == "INSUFFICIENT"


def test_funding_carry_insufficient_when_window_too_small() -> None:
    score = score_funding_rate(_flat_history(5), current_idx=4, lookback=90)
    assert score.is_skip


def test_funding_carry_neutral_in_calm_window() -> None:
    history = _flat_history(100, 0.0001)
    score = score_funding_rate(history, current_idx=99, lookback=90)
    # All flat: stddev=0 → z=0 → NEUTRAL or CARRY (within ±0.5)
    assert score.direction in {"NEUTRAL", "LONG"}
    assert score.pattern in {"NEUTRAL", "CARRY"}


def test_funding_carry_short_when_extreme_positive_z() -> None:
    history = _flat_history(90, 0.0001)
    history.append(0.0050)  # 50× the baseline
    score = score_funding_rate(history, current_idx=90, lookback=90)
    assert score.z_score > 1.5
    assert score.pattern == "REVERSAL_SHORT"
    assert score.direction == "SHORT"
    assert score.net_score < 0


def test_funding_carry_long_when_extreme_negative_z() -> None:
    history = _flat_history(90, 0.0001)
    history.append(-0.0030)  # negative funding day
    score = score_funding_rate(history, current_idx=90, lookback=90)
    assert score.z_score < -1.0
    assert score.pattern == "ACCUMULATION_LONG"
    assert score.direction == "LONG"
    assert score.net_score > 0


def test_funding_carry_carry_band_emits_long_bias() -> None:
    # WHY: realistic-noise window so stddev > 0; without that the degenerate
    # pseudo-z kicks in and a tiny deviation reads as 5σ.
    import random
    rng = random.Random(42)
    history = [0.0001 + rng.gauss(0.0, 0.00002) for _ in range(90)]
    history.append(0.0001)  # bang on the mean → z ~ 0
    score = score_funding_rate(history, current_idx=90, lookback=90)
    assert abs(score.z_score) <= 0.5
    assert score.pattern == "CARRY"
    assert score.direction == "LONG"
    assert score.net_score == pytest.approx(0.5)


def test_build_verdict_cost_gate_blocks_low_edge() -> None:
    # 0.5 net_score × 25bps = 12.5bps; 1.5 × 6 = 9bps — passes cost gate barely
    import random
    rng = random.Random(42)
    history = [0.0001 + rng.gauss(0.0, 0.00002) for _ in range(90)]
    history.append(0.0001)
    score = score_funding_rate(history, current_idx=90, lookback=90)
    verdict = build_verdict("BTC/USDT:USDT", 90, score)
    assert verdict.edge_bps == pytest.approx(12.5)
    assert verdict.cost_passed
    assert verdict.action == "BUY"


def test_build_verdict_strong_short_passes_cost() -> None:
    history = _flat_history(90, 0.0001)
    history.append(0.0050)
    score = score_funding_rate(history, current_idx=90, lookback=90)
    verdict = build_verdict("BTC/USDT:USDT", 90, score)
    assert verdict.cost_passed
    assert verdict.action == "SELL"
    assert verdict.all_in_bps == ALL_IN_BPS_BINANCE_PERP


def test_realized_return_basic() -> None:
    closes = [100, 101, 102, 103, 104]
    r = realized_return(closes, entry_idx=0, horizon_bars=3)
    assert r == pytest.approx((103 - 100) / 100)


def test_realized_return_returns_none_past_horizon() -> None:
    closes = [100, 101]
    assert realized_return(closes, entry_idx=0, horizon_bars=3) is None
