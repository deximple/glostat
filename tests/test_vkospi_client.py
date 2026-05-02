from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import date

import pytest

from glostat.data.vkospi_client import (
    VkospiBar,
    VkospiClient,
    VkospiDataError,
    VkospiDelta,
    compute_delta_at,
)

# ── VkospiBar invariants ─────────────────────────────────────────────────


class TestVkospiBar:
    def test_bar_immutable(self) -> None:
        b = VkospiBar(bar_date=date(2026, 5, 1), close=18.5)
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            b.close = 20.0  # type: ignore[misc]

    def test_negative_close_rejected(self) -> None:
        with pytest.raises(ValueError, match="non-negative"):
            VkospiBar(bar_date=date(2026, 5, 1), close=-1.0)

    def test_zero_close_allowed(self) -> None:
        # KRX may legitimately report a 0 close on a no-data day.
        b = VkospiBar(bar_date=date(2026, 5, 1), close=0.0)
        assert b.close == 0.0


# ── compute_delta_at — pure helper ───────────────────────────────────────


def _series(*pairs: tuple[date, float]) -> tuple[VkospiBar, ...]:
    return tuple(VkospiBar(bar_date=d, close=c) for d, c in pairs)


class TestComputeDeltaAt:
    def test_simple_two_day_delta(self) -> None:
        bars = _series(
            (date(2026, 5, 1), 20.0),
            (date(2026, 5, 2), 25.0),
        )
        d = compute_delta_at(bars, date(2026, 5, 2))
        assert d.close_t == 25.0
        assert d.close_t_minus_1 == 20.0
        assert d.abs_change == 5.0
        assert d.pct_change == pytest.approx(0.25, abs=1e-9)
        assert d.fear_regime is True
        assert d.calm_regime is False

    def test_calm_regime_when_vkospi_drops(self) -> None:
        bars = _series(
            (date(2026, 5, 1), 25.0),
            (date(2026, 5, 2), 20.0),
        )
        d = compute_delta_at(bars, date(2026, 5, 2))
        assert d.pct_change == pytest.approx(-0.20, abs=1e-9)
        assert d.calm_regime is True
        assert d.fear_regime is False

    def test_falls_back_to_prior_bar_when_day_missing(self) -> None:
        # `day` lands on a Sunday; prior trading day is Friday.
        bars = _series(
            (date(2026, 4, 30), 17.5),  # Thu (provides prior)
            (date(2026, 5, 1), 18.0),   # Fri (event day after fallback)
            (date(2026, 5, 4), 19.5),   # Mon
        )
        d = compute_delta_at(bars, date(2026, 5, 3))  # Sun, missing
        # Falls back to most-recent on/before → Friday's close.
        assert d.close_t == 18.0
        assert d.close_t_minus_1 == 17.5

    def test_empty_series_raises(self) -> None:
        with pytest.raises(VkospiDataError, match="empty"):
            compute_delta_at((), date(2026, 5, 1))

    def test_no_prior_bar_raises(self) -> None:
        bars = _series((date(2026, 5, 1), 18.0))
        with pytest.raises(VkospiDataError, match="prior"):
            compute_delta_at(bars, date(2026, 5, 1))

    def test_day_before_series_raises(self) -> None:
        bars = _series(
            (date(2026, 5, 5), 18.0),
            (date(2026, 5, 6), 19.0),
        )
        with pytest.raises(VkospiDataError, match="no VKOSPI bar"):
            compute_delta_at(bars, date(2026, 5, 1))


# ── VkospiClient — provider plumbing ─────────────────────────────────────


class TestVkospiClient:
    @pytest.mark.asyncio
    async def test_no_provider_raises_typed(self) -> None:
        client = VkospiClient()
        with pytest.raises(VkospiDataError, match="provider"):
            await client.get_history(start=date(2026, 1, 1), end=date(2026, 5, 1))

    @pytest.mark.asyncio
    async def test_set_provider_then_fetch(self) -> None:
        client = VkospiClient()

        canned = _series(
            (date(2026, 5, 1), 18.0),
            (date(2026, 5, 2), 19.0),
        )

        async def provider(_s: date, _e: date) -> tuple[VkospiBar, ...]:
            return canned

        client.set_history_provider(provider)
        result = await client.get_history(
            start=date(2026, 5, 1), end=date(2026, 5, 2),
        )
        assert result == canned

    @pytest.mark.asyncio
    async def test_provider_exception_wrapped(self) -> None:
        async def boom(_s: date, _e: date) -> tuple[VkospiBar, ...]:
            raise RuntimeError("network down")

        client = VkospiClient(history_provider=boom)
        with pytest.raises(VkospiDataError, match="network down"):
            await client.get_history(start=date(2026, 1, 1), end=date(2026, 5, 1))

    @pytest.mark.asyncio
    async def test_empty_provider_result_typed_error(self) -> None:
        async def empty(_s: date, _e: date) -> tuple[VkospiBar, ...]:
            return ()

        client = VkospiClient(history_provider=empty)
        with pytest.raises(VkospiDataError, match="no bars"):
            await client.get_history(start=date(2026, 1, 1), end=date(2026, 5, 1))

    @pytest.mark.asyncio
    async def test_invalid_range_raises(self) -> None:
        client = VkospiClient()
        with pytest.raises(ValueError, match="end"):
            await client.get_history(start=date(2026, 5, 5), end=date(2026, 5, 1))

    @pytest.mark.asyncio
    async def test_get_history_caches_within_same_range(self) -> None:
        call_count = 0

        async def provider(s: date, e: date) -> tuple[VkospiBar, ...]:
            nonlocal call_count
            call_count += 1
            return _series((s, 18.0), (e, 19.0))

        client = VkospiClient(history_provider=provider)
        a = await client.get_history(
            start=date(2026, 5, 1), end=date(2026, 5, 2),
        )
        b = await client.get_history(
            start=date(2026, 5, 1), end=date(2026, 5, 2),
        )
        assert a == b
        assert call_count == 1   # cached on second call

    @pytest.mark.asyncio
    async def test_get_delta_at_via_provider(self) -> None:
        async def provider(_s: date, _e: date) -> tuple[VkospiBar, ...]:
            return _series(
                (date(2026, 4, 30), 18.0),
                (date(2026, 5, 1), 22.0),
            )

        client = VkospiClient(history_provider=provider)
        d = await client.get_delta_at(date(2026, 5, 1))
        assert d.fear_regime is True
        assert d.pct_change == pytest.approx((22.0 - 18.0) / 18.0, abs=1e-9)


# ── VkospiDelta convenience flags ────────────────────────────────────────


class TestVkospiDelta:
    def test_zero_change_neither_fear_nor_calm(self) -> None:
        d = VkospiDelta(
            bar_date=date(2026, 5, 1),
            close_t=18.0, close_t_minus_1=18.0,
            abs_change=0.0, pct_change=0.0,
        )
        # Edge case: exactly flat → neither fear nor calm by strict > / < check.
        assert d.fear_regime is False
        assert d.calm_regime is False
