from __future__ import annotations

from datetime import date

import pytest

from glostat.experts.ichimoku import (
    BASE_NUMBERS,
    WINDOW_TRADING_DAYS,
    compute_time_convergence_t,
    find_anchor_low,
    find_anchor_lows,
    is_within_window,
    trading_days_distance,
    trading_days_offset,
)

# ── Constants ──────────────────────────────────────────────────────────────


def test_base_numbers_correct() -> None:
    assert list(BASE_NUMBERS) == [65, 76, 129, 172, 200, 257]


def test_window_constant() -> None:
    # Sprint 5 PR #1 — relaxed 3 → 7 trading days for higher T ≥ 1.0 hit rate.
    assert WINDOW_TRADING_DAYS == 7


# ── trading_days_offset ────────────────────────────────────────────────────


def test_trading_days_offset_skips_weekends() -> None:
    # Mon 2026-01-05 + 5 trading days = Mon 2026-01-12.
    out = trading_days_offset(date(2026, 1, 5), 5)
    assert out == date(2026, 1, 12)


def test_trading_days_offset_zero_returns_start() -> None:
    assert trading_days_offset(date(2026, 1, 5), 0) == date(2026, 1, 5)


def test_trading_days_offset_rejects_negative() -> None:
    with pytest.raises(ValueError, match="≥ 0"):
        trading_days_offset(date(2026, 1, 5), -1)


def test_trading_days_offset_65_days() -> None:
    # 2026-01-27 + 65 trading days = 2026-04-28 (independently verified).
    assert trading_days_offset(date(2026, 1, 27), 65) == date(2026, 4, 28)


# ── trading_days_distance ──────────────────────────────────────────────────


def test_trading_days_distance_same_date() -> None:
    assert trading_days_distance(date(2026, 1, 5), date(2026, 1, 5)) == 0


def test_trading_days_distance_one_week() -> None:
    # Mon 2026-01-05 → Mon 2026-01-12 = 5 trading days.
    assert trading_days_distance(date(2026, 1, 5), date(2026, 1, 12)) == 5


# ── is_within_window ───────────────────────────────────────────────────────


def test_is_within_window_7_days_inclusive() -> None:
    today = date(2026, 4, 28)
    # Sprint 5 PR #1 — relaxed window 7. 7 bdays before = 2026-04-17 (Fri).
    assert is_within_window(today, date(2026, 4, 17)) is True
    # 8 bdays before = 2026-04-16 → outside.
    assert is_within_window(today, date(2026, 4, 16)) is False


def test_is_within_window_legacy_three_day_distance_still_inside() -> None:
    today = date(2026, 4, 28)
    assert is_within_window(today, date(2026, 4, 23)) is True
    assert is_within_window(today, date(2026, 4, 22)) is True


def test_is_within_window_zero_distance() -> None:
    today = date(2026, 4, 28)
    assert is_within_window(today, today) is True


# ── compute_time_convergence_t ─────────────────────────────────────────────


def test_compute_t_zero_matches() -> None:
    today = date(2026, 4, 28)
    # Far-future anchor — no base hits today.
    t, matched = compute_time_convergence_t(today, date(2026, 4, 27))
    assert t == 0.0
    assert matched == []


def test_compute_t_single_match() -> None:
    today = date(2026, 4, 28)
    # 2026-01-27 + 65 bdays = 2026-04-28 → exactly 1 base hit.
    t, matched = compute_time_convergence_t(today, date(2026, 1, 27))
    assert t == 1.0
    assert matched == [65]


def test_compute_t_double_match() -> None:
    today = date(2026, 4, 28)
    # Two anchors: each hits one base. Union → 2 matches.
    anchors = [date(2026, 1, 27), date(2025, 10, 29)]   # 65 + 129
    t, matched = compute_time_convergence_t(today, anchors)
    assert t == 1.5
    assert matched == [65, 129]


def test_compute_t_triple_match() -> None:
    today = date(2026, 4, 28)
    anchors = [
        date(2026, 1, 27),   # n=65
        date(2025, 10, 29),  # n=129
        date(2025, 8, 29),   # n=172
    ]
    t, matched = compute_time_convergence_t(today, anchors)
    assert t == 2.0
    assert matched == [65, 129, 172]


def test_compute_t_quadruple_capped_at_two() -> None:
    today = date(2026, 4, 28)
    anchors = [
        date(2026, 1, 27),   # n=65
        date(2025, 10, 29),  # n=129
        date(2025, 8, 29),   # n=172
        date(2025, 7, 22),   # n=200
    ]
    t, matched = compute_time_convergence_t(today, anchors)
    assert t == 2.0  # cap at 2.0 for ≥3 matches
    assert len(matched) == 4


# ── find_anchor_low ────────────────────────────────────────────────────────


def test_find_anchor_low_returns_correct_date() -> None:
    bars = [
        (date(2026, 4, 1), 200.0),
        (date(2026, 4, 2), 195.0),
        (date(2026, 4, 3), 180.0),   # the low
        (date(2026, 4, 4), 190.0),
        (date(2026, 4, 5), 205.0),
    ]
    assert find_anchor_low(bars) == date(2026, 4, 3)


def test_find_anchor_low_empty_returns_none() -> None:
    assert find_anchor_low([]) is None


def test_find_anchor_low_respects_lookback() -> None:
    bars = [
        (date(2024, 1, 1), 100.0),    # absolute low, but outside 100-day lookback
        (date(2026, 4, 25), 195.0),
        (date(2026, 4, 28), 200.0),
    ]
    out = find_anchor_low(bars, lookback_days=30)
    assert out == date(2026, 4, 25)


# ── find_anchor_lows (multi-anchor) ────────────────────────────────────────


def test_find_anchor_lows_separates_by_min_gap() -> None:
    # Two adjacent lows ≤ 21 bdays apart → only the lowest survives.
    bars = [
        (date(2026, 4, 1), 200.0),
        (date(2026, 4, 2), 150.0),
        (date(2026, 4, 3), 152.0),    # close to first low → excluded by gap
        (date(2026, 4, 28), 200.0),
    ]
    out = find_anchor_lows(bars, k=3, min_gap_bdays=21)
    assert date(2026, 4, 2) in out
    assert date(2026, 4, 3) not in out
