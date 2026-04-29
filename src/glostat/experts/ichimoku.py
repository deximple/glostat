from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Final

import pandas as pd

# TITAN B2 일목 기본수치 — Ichimoku base numbers in TRADING days (not calendar).
# Convention: a base number n hits today if today == anchor + n trading days, ±window.
# Source: PR #2 description; reflects original Ichimoku "kihon-suchi" theory.
# Multi-anchor model: TITAN counts convergence as the UNION of base hits across the
# K most recent significant lows (typical K=3). A single-anchor projection can only
# ever hit 1 base because BASE diffs exceed the 3-trading-day window.

BASE_NUMBERS: Final[tuple[int, ...]] = (65, 76, 129, 172, 200, 257)
# Sprint 5 PR #1 — anchor convergence window relaxed 3 → 7 trading days.
# Rationale: PR #3 live run measured E_TIME skip rate 57-61% with the strict
# ±3 window; relaxing to ±7 lifts the T ≥ 1.0 hit rate without diluting
# the directional signal because compute_time_convergence_t still rewards
# multi-anchor unions over single hits.
WINDOW_TRADING_DAYS: Final[int] = 7
ANCHOR_K: Final[int] = 3                       # max anchors considered for T
ANCHOR_MIN_GAP_BDAYS: Final[int] = 21          # min spacing between anchors (~1 month)

# T value buckets — see PR #2 §3:
# 0 matches → 0.0; 1 → 1.0; 2 → 1.5; ≥3 → 2.0.
_T_TABLE: Final[dict[int, float]] = {0: 0.0, 1: 1.0, 2: 1.5}
_T_MAX: Final[float] = 2.0


def trading_days_offset(start: date, n: int) -> date:
    # WHY: pandas bdate_range skips weekends; MVP ignores US market holidays.
    # For E_TIME we accept the small drift (≤9 holidays/yr) — INV-GS-026 will
    # surface if it bites accuracy.
    if n < 0:
        raise ValueError(f"trading_days_offset requires n ≥ 0 (got {n})")
    if n == 0:
        return start
    rng = pd.bdate_range(start, periods=n + 1)
    out: date = rng[n].date()
    return out


def trading_days_distance(a: date, b: date) -> int:
    # WHY: count business days between a and b — distance in trading days.
    lo, hi = min(a, b), max(a, b)
    rng = pd.bdate_range(lo, hi)
    return max(0, len(rng) - 1)


def is_within_window(today: date, target: date, window: int = WINDOW_TRADING_DAYS) -> bool:
    return trading_days_distance(today, target) <= window


def _as_anchor_list(anchors: date | Sequence[date]) -> list[date]:
    # WHY: datetime is a subclass of date; treat single date or datetime as one anchor.
    if isinstance(anchors, (date, datetime)):
        return [anchors if not isinstance(anchors, datetime) else anchors.date()]
    return [a if not isinstance(a, datetime) else a.date() for a in anchors]


def compute_time_convergence_t(
    today: date,
    anchors: date | Sequence[date],
    *,
    window: int = WINDOW_TRADING_DAYS,
) -> tuple[float, list[int]]:
    # Returns (T, matched_base_numbers). T peaks at 2.0 for triple+ convergence.
    # Accepts a single anchor (back-compat) or a sequence of recent significant lows.
    anchor_list = _as_anchor_list(anchors)
    matches: set[int] = set()
    for a in anchor_list:
        for n in BASE_NUMBERS:
            target = trading_days_offset(a, n)
            if is_within_window(today, target, window):
                matches.add(n)
    matched_sorted = sorted(matches)
    t = _T_TABLE.get(len(matched_sorted), _T_MAX)
    return (t, matched_sorted)


def find_anchor_low(
    bars_with_dates: Sequence[tuple[date, float]],
    *,
    lookback_days: int = 257,
) -> date | None:
    # WHY: anchor for time convergence is the recent significant low.
    # Lookback restricts to the last `lookback_days` calendar days.
    if not bars_with_dates:
        return None
    latest_date = bars_with_dates[-1][0]
    cutoff = latest_date - timedelta(days=lookback_days)
    in_window = [(d, c) for d, c in bars_with_dates if d >= cutoff]
    if not in_window:
        in_window = list(bars_with_dates)
    return min(in_window, key=lambda dc: dc[1])[0]


def find_anchor_lows(
    bars_with_dates: Sequence[tuple[date, float]],
    *,
    lookback_days: int = 257,
    k: int = ANCHOR_K,
    min_gap_bdays: int = ANCHOR_MIN_GAP_BDAYS,
) -> list[date]:
    # WHY: TITAN convergence model uses K most recent local lows separated by
    # min_gap_bdays so a single grinding low doesn't dominate.
    if not bars_with_dates:
        return []
    latest_date = bars_with_dates[-1][0]
    cutoff = latest_date - timedelta(days=lookback_days)
    in_window = [(d, c) for d, c in bars_with_dates if d >= cutoff]
    if not in_window:
        in_window = list(bars_with_dates)
    # Greedy: take global min, then exclude ±min_gap_bdays band, repeat.
    remaining = list(in_window)
    chosen: list[date] = []
    while remaining and len(chosen) < k:
        best = min(remaining, key=lambda dc: dc[1])
        anchor = best[0]
        chosen.append(anchor)
        remaining = [
            (d, c) for d, c in remaining
            if trading_days_distance(d, anchor) > min_gap_bdays
        ]
    return sorted(chosen)


__all__ = [
    "ANCHOR_K",
    "ANCHOR_MIN_GAP_BDAYS",
    "BASE_NUMBERS",
    "WINDOW_TRADING_DAYS",
    "compute_time_convergence_t",
    "find_anchor_low",
    "find_anchor_lows",
    "is_within_window",
    "trading_days_distance",
    "trading_days_offset",
]
