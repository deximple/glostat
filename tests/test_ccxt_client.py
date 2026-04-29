from __future__ import annotations

import pytest

from glostat.data.ccxt_client import (
    CcxtBinanceClient,
    FundingRateBar,
    _safe_float,
    _timeframe_ms,
)


def test_timeframe_ms_handles_known_units() -> None:
    assert _timeframe_ms("1m") == 60_000
    assert _timeframe_ms("8h") == 8 * 3_600_000
    assert _timeframe_ms("1d") == 86_400_000
    # Unknown defaults to 8h
    assert _timeframe_ms("foo") == 8 * 3_600_000


def test_safe_float_returns_none_on_garbage() -> None:
    assert _safe_float(None) is None
    assert _safe_float("abc") is None
    assert _safe_float("1.5") == 1.5
    assert _safe_float(2) == 2.0


def test_funding_rate_bar_immutable() -> None:
    from datetime import UTC, datetime

    bar = FundingRateBar(ts=datetime(2025, 1, 1, tzinfo=UTC), funding_rate=0.0001)
    with pytest.raises((AttributeError, TypeError)):
        bar.funding_rate = 0.0002  # type: ignore[misc]


def test_ccxt_client_constructible_without_network(tmp_path) -> None:
    # Construction must not touch network or import ccxt eagerly
    c = CcxtBinanceClient(cache_dir=tmp_path)
    assert c._exchange is None  # lazy init
