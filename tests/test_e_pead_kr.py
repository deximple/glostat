from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.yfinance_types import OhlcvBar, OhlcvSeries
from glostat.experts.e_pead_kr import (
    EPeadKrExpert,
    _compute_drift,
    _last_expected_earnings_date,
    _score,
)

# ── Pure helpers ──────────────────────────────────────────────────────────


class TestLastExpectedEarningsDate:
    def test_late_q1(self) -> None:
        # Late March 2026 → most-recent past filing = Q4 2025 + 45 = 2026-02-14.
        d = _last_expected_earnings_date(date(2026, 3, 20))
        assert d == date(2026, 2, 14)

    def test_after_q1_filing(self) -> None:
        # Late May 2026 → most-recent = Q1 + 45 = 2026-05-15.
        d = _last_expected_earnings_date(date(2026, 5, 30))
        assert d == date(2026, 5, 15)

    def test_after_q3_filing(self) -> None:
        # Late November 2026 → Q3 + 45 = 2026-11-14.
        d = _last_expected_earnings_date(date(2026, 11, 30))
        assert d == date(2026, 11, 14)


def _bars(start: date, n: int, closes: list[float]) -> tuple[OhlcvBar, ...]:
    out: list[OhlcvBar] = []
    for i, c in enumerate(closes[:n]):
        ts = datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(days=i)
        out.append(OhlcvBar(ts=ts, open=c, high=c, low=c, close=c, volume=1))
    return tuple(out)


class TestComputeDrift:
    def test_uptrend_returns_positive_drift(self) -> None:
        # Earnings filing on Jan 1 2026; bars from Jan 1 with prices rising.
        last_e = date(2026, 1, 1)
        bars = _bars(last_e, 35, [100.0 + i for i in range(35)])
        series = OhlcvSeries(ticker="X", interval="1d", bars=bars)
        drift, t5, t30 = _compute_drift(series, last_e)
        assert drift is not None
        assert drift > 0   # bars uptrend → drift positive
        assert t5 == pytest.approx(105.0)
        assert t30 == pytest.approx(130.0)

    def test_flat_returns_zero_drift(self) -> None:
        last_e = date(2026, 1, 1)
        bars = _bars(last_e, 35, [100.0] * 35)
        series = OhlcvSeries(ticker="X", interval="1d", bars=bars)
        drift, _, _ = _compute_drift(series, last_e)
        assert drift == pytest.approx(0.0)

    def test_insufficient_data_returns_none(self) -> None:
        last_e = date(2026, 1, 1)
        # Only 3 bars — can't reach T+5.
        bars = _bars(last_e, 3, [100.0, 101.0, 102.0])
        series = OhlcvSeries(ticker="X", interval="1d", bars=bars)
        drift, _t5, _t30 = _compute_drift(series, last_e)
        assert drift is None


class TestScore:
    def test_positive_drift_long(self) -> None:
        s = _score(date(2026, 1, 1), days_since=60, drift=0.10)
        # 10% drift × gain 10 = 1.0 raw, > threshold 0.4 → LONG.
        assert s.direction == "LONG"
        assert s.net_score > 0.4

    def test_negative_drift_short(self) -> None:
        s = _score(date(2026, 1, 1), days_since=60, drift=-0.10)
        assert s.direction == "SHORT"

    def test_small_drift_neutral(self) -> None:
        s = _score(date(2026, 1, 1), days_since=60, drift=0.02)
        # 0.02 * 10 = 0.2 < 0.4 threshold → NEUTRAL.
        assert s.direction == "NEUTRAL"

    def test_clip_at_score_clip(self) -> None:
        # 50% drift × 10 = 5.0 raw, must clip to ±2.0.
        s = _score(date(2026, 1, 1), days_since=60, drift=0.50)
        assert -2.0 <= s.net_score <= 2.0


# ── Expert.compute integration with fakes ─────────────────────────────────


class _FakeYFinance:
    last_snapshot_id = "fake-snapshot"

    def __init__(self, series: OhlcvSeries | None = None,
                 fail: bool = False) -> None:
        self._series = series
        self._fail = fail

    async def get_ohlcv(self, ticker: str, *, start: Any, end: Any,
                        interval: str = "1d") -> OhlcvSeries:
        if self._fail or self._series is None:
            raise RuntimeError("fake ohlcv error")
        return self._series


class _FakeRouter:
    def __init__(self, client: Any) -> None:
        self._client = client

    def route(self, _expert: str, _method: str) -> tuple[Any, str]:
        return self._client, "get_ohlcv"


class _FakeCalendar:
    pass   # not used by EPeadKrExpert.compute (calendar passed in for symmetry)


class TestExpertCompute:
    @pytest.mark.asyncio
    async def test_skips_when_too_close_to_earnings(self) -> None:
        # When days_since < 30, we can't compute T+5..T+30 drift.
        # Use a fake `now` 10 days after the most-recent expected filing date
        # (2026-05-15 = Q1 + 45).
        ts = datetime(2026, 5, 25, tzinfo=UTC)
        expert = EPeadKrExpert(
            router=_FakeRouter(_FakeYFinance()),  # type: ignore[arg-type]
            calendar=_FakeCalendar(),  # type: ignore[arg-type]
        )
        with pytest.raises(ExpertSkipError, match="since expected earnings filing"):
            await expert.compute("096770", ts)

    @pytest.mark.asyncio
    async def test_uptrend_signal_long(self) -> None:
        # Q1 + 45 = 2026-05-15.
        last_e = date(2026, 5, 15)
        # Generate 35 bars of OHLCV from May 15 with uptrend.
        bars = _bars(last_e, 35, [100.0 + i * 1.5 for i in range(35)])
        series = OhlcvSeries(ticker="096770.KS", interval="1d", bars=bars)
        ts = datetime(2026, 6, 30, tzinfo=UTC)   # 46d after filing
        expert = EPeadKrExpert(
            router=_FakeRouter(_FakeYFinance(series)),  # type: ignore[arg-type]
            calendar=_FakeCalendar(),  # type: ignore[arg-type]
        )
        sig = await expert.compute("096770", ts)
        assert sig.direction == "LONG"
        assert sig.expert_name == "E_PEAD_KR"

    @pytest.mark.asyncio
    async def test_yfinance_failure_skips_cleanly(self) -> None:
        ts = datetime(2026, 6, 30, tzinfo=UTC)
        expert = EPeadKrExpert(
            router=_FakeRouter(_FakeYFinance(fail=True)),  # type: ignore[arg-type]
            calendar=_FakeCalendar(),  # type: ignore[arg-type]
        )
        with pytest.raises(ExpertSkipError):
            await expert.compute("096770", ts)
