from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from glostat.data.vkospi_resolvers import (
    KospiSmallCapResolver,
    YFinanceReturnResolver,
)
from glostat.data.yfinance_types import (
    Fundamentals,
    OhlcvBar,
    OhlcvSeries,
)


def _bars(*pairs: tuple[date, float]) -> tuple[OhlcvBar, ...]:
    out: list[OhlcvBar] = []
    for d, c in pairs:
        ts = datetime(d.year, d.month, d.day, tzinfo=UTC)
        out.append(OhlcvBar(
            ts=ts, open=c, high=c, low=c, close=c, volume=1,
        ))
    return tuple(out)


class _FakeYFinance:
    last_snapshot_id = "fake-snap"

    def __init__(
        self, *,
        ohlcv_per_ticker: dict[str, tuple[OhlcvBar, ...]] | None = None,
        fundamentals_per_ticker: dict[str, Fundamentals] | None = None,
        fail_ohlcv_for: tuple[str, ...] = (),
        fail_fundamentals_for: tuple[str, ...] = (),
    ) -> None:
        self._ohlcv = ohlcv_per_ticker or {}
        self._fundamentals = fundamentals_per_ticker or {}
        self._fail_ohlcv = set(fail_ohlcv_for)
        self._fail_fundamentals = set(fail_fundamentals_for)

    async def get_ohlcv(
        self, ticker: str, *, start: Any, end: Any, interval: str = "1d",
    ) -> OhlcvSeries:
        if ticker in self._fail_ohlcv:
            raise RuntimeError(f"yfinance failure for {ticker}")
        bars = self._ohlcv.get(ticker, ())
        return OhlcvSeries(ticker=ticker, interval=interval, bars=bars)

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        if ticker in self._fail_fundamentals:
            raise RuntimeError(f"yfinance fundamentals failure for {ticker}")
        if ticker in self._fundamentals:
            return self._fundamentals[ticker]
        return _make_fundamentals(ticker, market_cap=None)


def _make_fundamentals(ticker: str, *, market_cap: float | None) -> Fundamentals:
    return Fundamentals(
        ticker=ticker,
        pe_ratio=None, forward_pe=None, eps=None, forward_eps=None,
        roe=None, market_cap=market_cap, dividend_yield=None, beta=None,
        fifty_two_week_high=None, fifty_two_week_low=None,
    )


# ── YFinanceReturnResolver ───────────────────────────────────────────────


class TestYFinanceReturnResolver:
    @pytest.mark.asyncio
    async def test_simple_two_day_return(self) -> None:
        bars = _bars(
            (date(2026, 5, 1), 100.0),
            (date(2026, 5, 2), 110.0),
        )
        yf = _FakeYFinance(ohlcv_per_ticker={"005930.KS": bars})
        resolver = YFinanceReturnResolver(yf_client=yf)  # type: ignore[arg-type]
        r = await resolver.get_recent_daily_return("005930", date(2026, 5, 2))
        assert r == pytest.approx(0.10, abs=1e-9)

    @pytest.mark.asyncio
    async def test_negative_return(self) -> None:
        bars = _bars(
            (date(2026, 5, 1), 100.0),
            (date(2026, 5, 2), 88.0),
        )
        yf = _FakeYFinance(ohlcv_per_ticker={"005930.KS": bars})
        resolver = YFinanceReturnResolver(yf_client=yf)  # type: ignore[arg-type]
        r = await resolver.get_recent_daily_return("005930", date(2026, 5, 2))
        assert r == pytest.approx(-0.12, abs=1e-9)

    @pytest.mark.asyncio
    async def test_falls_back_to_most_recent_on_or_before_as_of(self) -> None:
        # as_of = Sat → use Friday as latest, Thu as prior.
        bars = _bars(
            (date(2026, 4, 30), 100.0),  # Thu
            (date(2026, 5, 1), 105.0),   # Fri
        )
        yf = _FakeYFinance(ohlcv_per_ticker={"005930.KS": bars})
        resolver = YFinanceReturnResolver(yf_client=yf)  # type: ignore[arg-type]
        r = await resolver.get_recent_daily_return("005930", date(2026, 5, 2))
        assert r == pytest.approx(0.05, abs=1e-9)

    @pytest.mark.asyncio
    async def test_no_data_returns_none(self) -> None:
        yf = _FakeYFinance()
        resolver = YFinanceReturnResolver(yf_client=yf)  # type: ignore[arg-type]
        r = await resolver.get_recent_daily_return("005930", date(2026, 5, 2))
        assert r is None

    @pytest.mark.asyncio
    async def test_only_one_bar_returns_none(self) -> None:
        bars = _bars((date(2026, 5, 2), 100.0))
        yf = _FakeYFinance(ohlcv_per_ticker={"005930.KS": bars})
        resolver = YFinanceReturnResolver(yf_client=yf)  # type: ignore[arg-type]
        r = await resolver.get_recent_daily_return("005930", date(2026, 5, 2))
        assert r is None

    @pytest.mark.asyncio
    async def test_yfinance_failure_returns_none(self) -> None:
        yf = _FakeYFinance(fail_ohlcv_for=("005930.KS",))
        resolver = YFinanceReturnResolver(yf_client=yf)  # type: ignore[arg-type]
        r = await resolver.get_recent_daily_return("005930", date(2026, 5, 2))
        assert r is None

    @pytest.mark.asyncio
    async def test_caches_result(self) -> None:
        call_count = 0

        class _CountingYf(_FakeYFinance):
            async def get_ohlcv(self, ticker: str, **kw: Any) -> OhlcvSeries:
                nonlocal call_count
                call_count += 1
                return await super().get_ohlcv(ticker, **kw)

        bars = _bars(
            (date(2026, 5, 1), 100.0),
            (date(2026, 5, 2), 110.0),
        )
        yf = _CountingYf(ohlcv_per_ticker={"005930.KS": bars})
        resolver = YFinanceReturnResolver(yf_client=yf)  # type: ignore[arg-type]
        await resolver.get_recent_daily_return("005930", date(2026, 5, 2))
        await resolver.get_recent_daily_return("005930", date(2026, 5, 2))
        assert call_count == 1

    def test_invalid_lookback_rejected(self) -> None:
        yf = _FakeYFinance()
        with pytest.raises(ValueError, match="lookback_days"):
            YFinanceReturnResolver(yf_client=yf, lookback_days=1)  # type: ignore[arg-type]


# ── KospiSmallCapResolver ────────────────────────────────────────────────


class TestKospiSmallCapResolver:
    @pytest.mark.asyncio
    async def test_below_threshold_is_small(self) -> None:
        # Threshold default 5T KRW; ticker market_cap = 1T → small.
        yf = _FakeYFinance(fundamentals_per_ticker={
            "005930.KS": _make_fundamentals("005930.KS", market_cap=1e12),
        })
        resolver = KospiSmallCapResolver(yf_client=yf)  # type: ignore[arg-type]
        result = await resolver.is_small_cap("005930", date(2026, 5, 2))
        assert result is True

    @pytest.mark.asyncio
    async def test_above_threshold_is_large(self) -> None:
        yf = _FakeYFinance(fundamentals_per_ticker={
            "005930.KS": _make_fundamentals("005930.KS", market_cap=400e12),
        })
        resolver = KospiSmallCapResolver(yf_client=yf)  # type: ignore[arg-type]
        result = await resolver.is_small_cap("005930", date(2026, 5, 2))
        assert result is False

    @pytest.mark.asyncio
    async def test_missing_market_cap_treated_as_large(self) -> None:
        yf = _FakeYFinance(fundamentals_per_ticker={
            "005930.KS": _make_fundamentals("005930.KS", market_cap=None),
        })
        resolver = KospiSmallCapResolver(yf_client=yf)  # type: ignore[arg-type]
        # Graceful: missing data → large (no multiplier applied).
        result = await resolver.is_small_cap("005930", date(2026, 5, 2))
        assert result is False

    @pytest.mark.asyncio
    async def test_yfinance_failure_treated_as_large(self) -> None:
        yf = _FakeYFinance(fail_fundamentals_for=("005930.KS",))
        resolver = KospiSmallCapResolver(yf_client=yf)  # type: ignore[arg-type]
        result = await resolver.is_small_cap("005930", date(2026, 5, 2))
        assert result is False

    @pytest.mark.asyncio
    async def test_custom_threshold(self) -> None:
        yf = _FakeYFinance(fundamentals_per_ticker={
            "005930.KS": _make_fundamentals("005930.KS", market_cap=10e12),
        })
        # 20T threshold → 10T market cap is now small.
        resolver = KospiSmallCapResolver(
            yf_client=yf, threshold_krw=20e12,  # type: ignore[arg-type]
        )
        assert resolver.threshold_krw == 20e12
        result = await resolver.is_small_cap("005930", date(2026, 5, 2))
        assert result is True

    @pytest.mark.asyncio
    async def test_caches_per_code(self) -> None:
        call_count = 0

        class _CountingYf(_FakeYFinance):
            async def get_fundamentals(self, ticker: str) -> Fundamentals:
                nonlocal call_count
                call_count += 1
                return await super().get_fundamentals(ticker)

        yf = _CountingYf(fundamentals_per_ticker={
            "005930.KS": _make_fundamentals("005930.KS", market_cap=400e12),
        })
        resolver = KospiSmallCapResolver(yf_client=yf)  # type: ignore[arg-type]
        await resolver.is_small_cap("005930", date(2026, 5, 2))
        await resolver.is_small_cap("005930", date(2026, 6, 1))
        # Same code, two different days → 1 fetch.
        assert call_count == 1

    def test_invalid_threshold_rejected(self) -> None:
        yf = _FakeYFinance()
        with pytest.raises(ValueError, match="threshold_krw"):
            KospiSmallCapResolver(yf_client=yf, threshold_krw=0.0)  # type: ignore[arg-type]
