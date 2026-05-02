from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from glostat.data.yfinance_types import OhlcvBar, OhlcvSeries
from glostat.replay.phase_kr_eval import evaluate_pead_kr
from glostat.replay.phase_kr_hindcast import _ThesisAccumulator

# v1.6 P5 — point-in-time PEAD hindcast evaluator tests.


# ── Fakes ─────────────────────────────────────────────────────────────────


def _bars(start: date, n: int, closes: list[float]) -> tuple[OhlcvBar, ...]:
    out: list[OhlcvBar] = []
    for i, c in enumerate(closes[:n]):
        ts = datetime(start.year, start.month, start.day, tzinfo=UTC) + timedelta(days=i)
        out.append(OhlcvBar(ts=ts, open=c, high=c, low=c, close=c, volume=1))
    return tuple(out)


class _FakeYFinance:
    last_snapshot_id = "fake-pead-snap"

    def __init__(
        self, *, by_window: dict[tuple[date, date], OhlcvSeries] | None = None,
        fail: bool = False,
    ) -> None:
        self._by_window = by_window or {}
        self._fail = fail

    async def get_ohlcv(
        self, ticker: str, *, start: Any, end: Any, interval: str = "1d",
    ) -> OhlcvSeries:
        if self._fail:
            raise RuntimeError("fake yf fail")
        # Return the first registered series, or empty.
        for series in self._by_window.values():
            return series
        return OhlcvSeries(ticker=ticker, interval=interval, bars=())


# ── Tests ─────────────────────────────────────────────────────────────────


class TestEvaluatePeadKr:
    @pytest.mark.asyncio
    async def test_skips_too_close_to_earnings(self) -> None:
        # day = 2026-05-25; last_e = Q1+45 = 2026-05-15 → days_since = 10 < 30.
        acc = _ThesisAccumulator(thesis="E_PEAD_KR", horizon_days=30)
        await evaluate_pead_kr(
            code="096770", day=date(2026, 5, 25),
            yf=_FakeYFinance(),  # type: ignore[arg-type]
            horizon_days=30, accumulator=acc,
        )
        assert acc.n_evaluated == 1
        assert acc.n_skipped == 1
        assert acc.n_actionable == 0
        # Skip reason mentions earnings proximity.
        assert any("too_close" in k for k in acc.skip_breakdown)

    @pytest.mark.asyncio
    async def test_skips_no_drift_window_data(self) -> None:
        # day far enough from earnings, but yf returns empty.
        acc = _ThesisAccumulator(thesis="E_PEAD_KR", horizon_days=30)
        await evaluate_pead_kr(
            code="096770", day=date(2026, 6, 30),
            yf=_FakeYFinance(),  # type: ignore[arg-type]
            horizon_days=30, accumulator=acc,
        )
        assert acc.n_skipped == 1
        assert acc.n_actionable == 0

    @pytest.mark.asyncio
    async def test_uptrend_records_long_signal(self) -> None:
        # last_e = 2026-05-15. day = 2026-06-30. Uptrend bars from May → June.
        last_e = date(2026, 5, 15)
        # 60 bars from May 1 with steady uptrend (drift target T+5 to T+30).
        bars = _bars(date(2026, 5, 1), 70, [100.0 + i for i in range(70)])
        series = OhlcvSeries(ticker="096770.KS", interval="1d", bars=bars)
        acc = _ThesisAccumulator(thesis="E_PEAD_KR", horizon_days=30)
        await evaluate_pead_kr(
            code="096770", day=date(2026, 6, 30),
            yf=_FakeYFinance(  # type: ignore[arg-type]
                by_window={(last_e, date(2026, 6, 30)): series},
            ),
            horizon_days=30, accumulator=acc,
        )
        # Should have recorded a signal: positive drift → LONG.
        assert acc.n_actionable >= 1
        if acc.trades:
            assert acc.trades[0].direction == "LONG"
            assert acc.trades[0].raw_score > 0

    @pytest.mark.asyncio
    async def test_yf_fail_skips_cleanly(self) -> None:
        acc = _ThesisAccumulator(thesis="E_PEAD_KR", horizon_days=30)
        await evaluate_pead_kr(
            code="096770", day=date(2026, 6, 30),
            yf=_FakeYFinance(fail=True),  # type: ignore[arg-type]
            horizon_days=30, accumulator=acc,
        )
        assert acc.n_skipped == 1
