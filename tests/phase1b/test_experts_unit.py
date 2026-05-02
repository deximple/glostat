from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from glostat.data.sec_edgar_form4 import Form4Transaction
from glostat.data.yfinance_types import (
    EarningsCalendar,
    EarningsEvent,
)
from glostat.experts.e_fomc_drift import FOMC_DATES, EFomcDriftExpert
from glostat.experts.e_insider_cluster import EInsiderClusterExpert
from glostat.experts.e_pead import EPeadExpert, PeadEvent
from glostat.experts.e_sector_rotation import (
    BENCHMARK,
    SECTOR_ETFS,
    ESectorRotationExpert,
)


@dataclass
class _FakeCache:
    closes: dict[str, dict[date, float]]
    fetched: set[str] = None

    def __post_init__(self):
        if self.fetched is None:
            self.fetched = set()

    async def get(self, ticker: str) -> Any:
        self.fetched.add(ticker.upper())
        return None

    def close_at_or_before(self, ticker: str, day: date) -> float | None:
        series = self.closes.get(ticker.upper(), {})
        best: float | None = None
        best_day: date | None = None
        for d, c in series.items():
            if d > day:
                continue
            if best_day is None or d > best_day:
                best_day = d
                best = c
        return best

    def forward_return(self, ticker: str, day: date, horizon_days: int = 30) -> float | None:
        c0 = self.close_at_or_before(ticker, day)
        c1 = self.close_at_or_before(ticker, day + timedelta(days=horizon_days))
        if c0 is None or c1 is None or c0 <= 0:
            return None
        return (c1 - c0) / c0


def _line(start: date, days: int, c0: float, daily_growth: float) -> dict[date, float]:
    out: dict[date, float] = {}
    p = c0
    for i in range(days):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        out[d] = p
        p *= 1.0 + daily_growth
    return out


# ── Sector Rotation ────────────────────────────────────────────────────


def test_sector_rotation_ranks_by_momentum():
    start = date(2024, 1, 1)
    closes: dict[str, dict[date, float]] = {}
    for i, etf in enumerate(SECTOR_ETFS):
        # Ascending growth — XLF flat, XLC strongest. Ensures stable ranking.
        growth = i * 0.0005
        closes[etf] = _line(start, days=400, c0=100.0, daily_growth=growth)
    closes[BENCHMARK] = _line(start, days=400, c0=400.0, daily_growth=0.0003)
    cache = _FakeCache(closes=closes)
    expert = ESectorRotationExpert(price_cache=cache)
    signals = asyncio.run(expert.compute_for_day(date(2024, 12, 16)))
    longs = [t for t, s in signals.items() if s.direction == "LONG"]
    shorts = [t for t, s in signals.items() if s.direction == "SHORT"]
    # 11 sectors → top 3 long, bottom 3 short, middle 5 neutral.
    assert len(longs) == 3
    assert len(shorts) == 3
    # Highest growth = SECTOR_ETFS[-1] should be LONG.
    assert SECTOR_ETFS[-1] in longs
    assert SECTOR_ETFS[0] in shorts


def test_sector_rotation_returns_neutral_when_data_missing():
    start = date(2024, 1, 1)
    closes: dict[str, dict[date, float]] = {}
    for etf in SECTOR_ETFS:
        # Only provide the LAST date — no anchor, momentum None
        closes[etf] = {date(2024, 12, 16): 100.0}
    cache = _FakeCache(closes=closes)
    expert = ESectorRotationExpert(price_cache=cache)
    signals = asyncio.run(expert.compute_for_day(date(2024, 12, 16)))
    # All NEUTRAL because no anchor returns
    assert all(s.direction == "NEUTRAL" for s in signals.values())


# ── PEAD ────────────────────────────────────────────────────────────────


def test_pead_signal_from_event_classifies_surprise():
    yf = object()  # not used by signal_from_event
    expert = EPeadExpert(yf_client=yf, start_date=date(2024, 1, 1), end_date=date(2024, 12, 31))
    pos = PeadEvent(
        ticker="AAPL", earnings_date=date(2024, 5, 1),
        actual_eps=2.0, estimate_eps=1.6, surprise_pct=0.25,
    )
    sig = expert.signal_from_event(pos)
    assert sig.direction == "LONG"
    assert sig.score > 0
    assert sig.day == date(2024, 5, 2)  # T+1, weekday

    neg = PeadEvent(
        ticker="AAPL", earnings_date=date(2024, 5, 1),
        actual_eps=1.0, estimate_eps=1.6, surprise_pct=-0.375,
    )
    sig = expert.signal_from_event(neg)
    assert sig.direction == "SHORT"
    assert sig.score < 0

    flat = PeadEvent(
        ticker="AAPL", earnings_date=date(2024, 5, 1),
        actual_eps=1.6, estimate_eps=1.6, surprise_pct=0.0,
    )
    sig = expert.signal_from_event(flat)
    assert sig.direction == "NEUTRAL"
    assert sig.score == 0.0


def test_pead_get_events_filters_window_and_zero_estimate():
    class _StubYF:
        async def get_earnings_calendar(self, ticker):
            return EarningsCalendar(
                ticker=ticker,
                upcoming=(
                    EarningsEvent(
                        ticker=ticker,
                        earnings_date=datetime(2024, 5, 1, 12, tzinfo=UTC),
                        eps_estimate=1.5, eps_actual=1.7, revenue_estimate=None,
                    ),
                    EarningsEvent(
                        ticker=ticker,
                        earnings_date=datetime(2025, 5, 1, 12, tzinfo=UTC),
                        eps_estimate=0.0, eps_actual=0.5, revenue_estimate=None,
                    ),
                    EarningsEvent(
                        ticker=ticker,
                        earnings_date=datetime(2027, 5, 1, 12, tzinfo=UTC),
                        eps_estimate=2.0, eps_actual=2.5, revenue_estimate=None,
                    ),
                ),
            )

    expert = EPeadExpert(
        yf_client=_StubYF(),
        start_date=date(2024, 1, 1),
        end_date=date(2026, 12, 31),
    )
    events = asyncio.run(expert.get_events("AAPL"))
    # Only 1 valid: 2025 has zero estimate, 2027 outside window.
    assert len(events) == 1
    assert events[0].earnings_date == date(2024, 5, 1)


def test_pead_entry_day_skips_weekend():
    ev = PeadEvent(
        ticker="X", earnings_date=date(2024, 5, 3),  # Friday
        actual_eps=1.0, estimate_eps=1.0, surprise_pct=0.1,
    )
    # T+1 = Saturday → entry_day = Monday
    assert ev.entry_day == date(2024, 5, 6)


# ── FOMC drift ──────────────────────────────────────────────────────────


def test_fomc_drift_classifies_reaction():
    closes: dict[str, dict[date, float]] = {
        "SPY": {date(2024, 3, 19): 500.0, date(2024, 3, 20): 510.0},
    }
    cache = _FakeCache(closes=closes)
    expert = EFomcDriftExpert(
        price_cache=cache, universe=("SPY",), fomc_dates=(date(2024, 3, 20),),
    )
    ev = asyncio.run(expert.compute_event("SPY", date(2024, 3, 20)))
    assert ev is not None
    assert ev.direction == "LONG"   # +2% > threshold
    assert ev.reaction_pct > 0
    assert ev.entry_day == date(2024, 3, 21)


def test_fomc_drift_returns_neutral_under_threshold():
    closes = {"SPY": {date(2024, 3, 19): 500.0, date(2024, 3, 20): 500.05}}
    cache = _FakeCache(closes=closes)
    expert = EFomcDriftExpert(
        price_cache=cache, universe=("SPY",), fomc_dates=(date(2024, 3, 20),),
    )
    ev = asyncio.run(expert.compute_event("SPY", date(2024, 3, 20)))
    assert ev is not None
    assert ev.direction == "NEUTRAL"


def test_fomc_drift_event_dates_in_window():
    cache = _FakeCache(closes={})
    expert = EFomcDriftExpert(price_cache=cache, universe=("SPY",))
    in_window = expert.event_dates_in_window(date(2024, 1, 1), date(2024, 12, 31))
    fomc_2024 = [d for d in FOMC_DATES if d.year == 2024]
    assert sorted(in_window) == sorted(fomc_2024)


# ── Insider cluster ─────────────────────────────────────────────────────


def test_insider_cluster_score_at_with_threshold_buyers():
    # Build expert manually-stocked transaction list — bypass async warm.
    expert = EInsiderClusterExpert(sec_client=None)
    base = dict(
        issuer_cik="x", accession="a", filed_at=date(2024, 6, 1),
        reporter_role="Director", code="P", shares=100.0, price=10.0, value_usd=1000.0,
    )
    expert._txn_cache["TEST"] = [
        Form4Transaction(transaction_date=date(2024, 6, 10), reporter_name="A", reporter_cik="1", **base),
        Form4Transaction(transaction_date=date(2024, 6, 11), reporter_name="B", reporter_cik="2", **base),
        Form4Transaction(transaction_date=date(2024, 6, 12), reporter_name="C", reporter_cik="3", **base),
    ]
    s = expert.score_at("TEST", date(2024, 6, 14))
    assert s.cluster_buyers == 3
    assert s.direction == "LONG"
    assert s.score == pytest.approx(1.5)


def test_insider_cluster_below_threshold_neutral():
    expert = EInsiderClusterExpert(sec_client=None)
    base = dict(
        issuer_cik="x", accession="a", filed_at=date(2024, 6, 1),
        reporter_role="Director", code="P", shares=100.0, price=10.0, value_usd=1000.0,
    )
    expert._txn_cache["TEST"] = [
        Form4Transaction(transaction_date=date(2024, 6, 10), reporter_name="A", reporter_cik="1", **base),
        Form4Transaction(transaction_date=date(2024, 6, 11), reporter_name="B", reporter_cik="2", **base),
    ]
    s = expert.score_at("TEST", date(2024, 6, 14))
    assert s.cluster_buyers == 2
    assert s.direction == "NEUTRAL"
    assert s.score == 0.0


def test_insider_cluster_event_dates_filters_buys_only():
    expert = EInsiderClusterExpert(sec_client=None)
    base = dict(
        issuer_cik="x", accession="a", filed_at=date(2024, 6, 1),
        reporter_role="Director", shares=100.0, price=10.0, value_usd=1000.0,
        reporter_name="A", reporter_cik="1",
    )
    expert._txn_cache["TEST"] = [
        Form4Transaction(transaction_date=date(2024, 6, 10), code="P", **base),
        Form4Transaction(transaction_date=date(2024, 6, 11), code="S", **base),
        Form4Transaction(transaction_date=date(2024, 6, 12), code="P", **base),
    ]
    days = expert.cluster_event_dates("TEST")
    assert days == [date(2024, 6, 10), date(2024, 6, 12)]


# ── v1.10.5: configurable cluster_threshold + window_days ────────────────


def test_insider_cluster_relaxed_threshold_two_fires_long():
    # v1.10.5: with threshold=2, the 2-buyer case (previously NEUTRAL at
    # threshold=3) now fires LONG. Use case: re-hindcast with relaxed gating
    # to grow n above the 50-sample activation floor.
    expert = EInsiderClusterExpert(sec_client=None, cluster_threshold=2)
    base = dict(
        issuer_cik="x", accession="a", filed_at=date(2024, 6, 1),
        reporter_role="Director", code="P", shares=100.0, price=10.0,
        value_usd=1000.0,
    )
    expert._txn_cache["TEST"] = [
        Form4Transaction(
            transaction_date=date(2024, 6, 10), reporter_name="A",
            reporter_cik="1", **base,
        ),
        Form4Transaction(
            transaction_date=date(2024, 6, 11), reporter_name="B",
            reporter_cik="2", **base,
        ),
    ]
    s = expert.score_at("TEST", date(2024, 6, 14))
    assert s.cluster_buyers == 2
    assert s.direction == "LONG"
    assert s.score == pytest.approx(1.0)


def test_insider_cluster_default_threshold_unchanged():
    # Live expert default must remain threshold=3 to preserve predict-time
    # behaviour. v1.10.5 makes threshold configurable; default semantics
    # MUST not regress.
    expert = EInsiderClusterExpert(sec_client=None)
    assert expert.cluster_threshold == 3
    assert expert.window_days == 14


def test_insider_cluster_widened_window_captures_spread_cluster():
    # v1.10.5: widening window from 14d → 30d captures clusters spread out
    # across a month — useful for thesis variants that target slower
    # accumulation patterns.
    expert = EInsiderClusterExpert(sec_client=None, window_days=30)
    base = dict(
        issuer_cik="x", accession="a", filed_at=date(2024, 6, 1),
        reporter_role="Director", code="P", shares=100.0, price=10.0,
        value_usd=1000.0,
    )
    # 3 buyers spread 20 days apart — would miss a 14d window.
    expert._txn_cache["TEST"] = [
        Form4Transaction(
            transaction_date=date(2024, 5, 25), reporter_name="A",
            reporter_cik="1", **base,
        ),
        Form4Transaction(
            transaction_date=date(2024, 6, 5), reporter_name="B",
            reporter_cik="2", **base,
        ),
        Form4Transaction(
            transaction_date=date(2024, 6, 14), reporter_name="C",
            reporter_cik="3", **base,
        ),
    ]
    s = expert.score_at("TEST", date(2024, 6, 14))
    # 30d window picks up all 3 buyers; 14d would miss the 5/25 entry.
    assert s.cluster_buyers == 3
    assert s.direction == "LONG"


def test_insider_cluster_invalid_threshold_raises():
    with pytest.raises(ValueError, match="cluster_threshold"):
        EInsiderClusterExpert(sec_client=None, cluster_threshold=0)


def test_insider_cluster_invalid_window_raises():
    with pytest.raises(ValueError, match="window_days"):
        EInsiderClusterExpert(sec_client=None, window_days=0)


def test_insider_cluster_signal_metadata_includes_threshold():
    # Threshold should round-trip through PhaseSignal metadata so the
    # downstream calibration record carries the spec parameters.
    expert = EInsiderClusterExpert(
        sec_client=None, cluster_threshold=2, window_days=21,
    )
    expert._txn_cache["TEST"] = []
    sig = expert.signal_at("TEST", date(2024, 6, 14))
    md = dict(sig.metadata)
    assert md["cluster_threshold"] == "2"
    assert md["window_days"] == "21"
