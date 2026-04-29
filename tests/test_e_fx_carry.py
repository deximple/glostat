from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path

from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_types import OhlcvBar, OhlcvSeries
from glostat.experts.e_fx_carry import (
    DEFENSIVE_TICKERS,
    UNIVERSE,
    EFxCarryExpert,
    FxCarrySnapshot,
)
from glostat.phase1b.price_cache import PriceCache


def _bar(d: date, close: float) -> OhlcvBar:
    ts = datetime(d.year, d.month, d.day, tzinfo=UTC)
    return OhlcvBar(
        ts=ts, open=close, high=close, low=close, close=close, volume=1000,
        adj_close=close,
    )


def _series(ticker: str, closes: list[tuple[date, float]]) -> OhlcvSeries:
    return OhlcvSeries(ticker=ticker, bars=tuple(_bar(d, c) for d, c in closes))


def _patch_cache_with(
    cache: PriceCache, mapping: dict[str, list[tuple[date, float]]]
) -> None:
    for t, closes in mapping.items():
        cache._mem[t.upper()] = _series(t, closes)


def _empty_cache(tmp_path: Path) -> PriceCache:
    from glostat.data.yfinance_client import YFinanceClient
    broker = SnapshotBroker(root=tmp_path / "snap")
    yf = YFinanceClient(snapshot_broker=broker)
    return PriceCache(client=yf, start=date(2024, 1, 1), end=date(2024, 12, 31),
                      cache_dir=tmp_path / "ohlcv")


def test_universe_contains_targets_and_macro_inputs() -> None:
    assert "XLU" in UNIVERSE and "XLV" in UNIVERSE
    assert "^VIX" in UNIVERSE
    assert "FXY" in UNIVERSE
    assert "EWZ" in UNIVERSE


def test_snapshot_emits_legs_when_thresholds_breached(tmp_path: Path) -> None:
    cache = _empty_cache(tmp_path)
    # Need window+1 bars for trailing returns (FXY 5d → 6 bars, EWZ 3d → 4 bars).
    _patch_cache_with(cache, {
        "^VIX": [
            (date(2024, 4, 29), 30.0),
            (date(2024, 4, 30), 30.0),
            (date(2024, 5, 1), 30.0),
            (date(2024, 5, 2), 30.0),
            (date(2024, 5, 3), 30.0),
            (date(2024, 5, 6), 30.0),
            (date(2024, 5, 7), 30.0),
        ],
        "FXY": [
            (date(2024, 4, 29), 100.0),
            (date(2024, 4, 30), 100.0),
            (date(2024, 5, 1), 100.5),
            (date(2024, 5, 2), 101.0),
            (date(2024, 5, 3), 101.5),
            (date(2024, 5, 6), 102.5),
            (date(2024, 5, 7), 105.0),
        ],
        "EWZ": [
            (date(2024, 4, 29), 30.0),
            (date(2024, 4, 30), 30.0),
            (date(2024, 5, 1), 30.0),
            (date(2024, 5, 2), 30.0),
            (date(2024, 5, 3), 30.0),
            (date(2024, 5, 6), 30.0),
            (date(2024, 5, 7), 28.5),
        ],
    })
    expert = EFxCarryExpert(price_cache=cache)
    snap = expert.snapshot_for_day(date(2024, 5, 7))
    assert snap.is_complete
    assert snap.leg_vix
    assert snap.leg_fxy
    assert snap.leg_ewz
    assert snap.legs_active == 3
    assert snap.risk_off


def test_snapshot_is_neutral_when_no_legs_active(tmp_path: Path) -> None:
    cache = _empty_cache(tmp_path)
    days = (
        date(2024, 4, 29), date(2024, 4, 30),
        date(2024, 5, 1), date(2024, 5, 2), date(2024, 5, 3),
        date(2024, 5, 6), date(2024, 5, 7),
    )
    _patch_cache_with(cache, {
        "^VIX": [(d, 15.0) for d in days],
        "FXY":  [(d, 100.0) for d in days],
        "EWZ":  [(d, 30.0) for d in days],
    })
    expert = EFxCarryExpert(price_cache=cache)
    snap = expert.snapshot_for_day(date(2024, 5, 7))
    assert snap.legs_active == 0
    assert not snap.risk_off


def test_signal_long_for_defensive_when_risk_off() -> None:
    snap = FxCarrySnapshot(
        day=date(2024, 5, 7),
        vix_5d_mean=30.0, fxy_5d_return=0.05, ewz_3d_return=-0.03,
        leg_vix=True, leg_fxy=True, leg_ewz=True,
    )

    class _Fake:
        pass

    expert = EFxCarryExpert.__new__(EFxCarryExpert)  # bypass __init__
    sig = expert.signal_for("XLU", snap)
    assert sig.direction == "LONG"
    assert sig.score > 1.0


def test_signal_short_for_cyclical_when_risk_off() -> None:
    snap = FxCarrySnapshot(
        day=date(2024, 5, 7),
        vix_5d_mean=30.0, fxy_5d_return=0.05, ewz_3d_return=-0.03,
        leg_vix=True, leg_fxy=True, leg_ewz=True,
    )
    expert = EFxCarryExpert.__new__(EFxCarryExpert)
    sig = expert.signal_for("XLF", snap)
    assert sig.direction == "SHORT"
    assert sig.score < -1.0


def test_signal_neutral_when_not_risk_off() -> None:
    snap = FxCarrySnapshot(
        day=date(2024, 5, 7),
        vix_5d_mean=15.0, fxy_5d_return=0.0, ewz_3d_return=0.0,
        leg_vix=False, leg_fxy=False, leg_ewz=False,
    )
    expert = EFxCarryExpert.__new__(EFxCarryExpert)
    sig = expert.signal_for("XLU", snap)
    assert sig.direction == "NEUTRAL"
    assert sig.score == 0.0


def test_signal_neutral_for_unknown_ticker() -> None:
    snap = FxCarrySnapshot(
        day=date(2024, 5, 7),
        vix_5d_mean=30.0, fxy_5d_return=0.05, ewz_3d_return=-0.03,
        leg_vix=True, leg_fxy=True, leg_ewz=True,
    )
    expert = EFxCarryExpert.__new__(EFxCarryExpert)
    sig = expert.signal_for("AAPL", snap)
    assert sig.direction == "NEUTRAL"


def test_defensive_tickers_listed() -> None:
    assert "XLU" in DEFENSIVE_TICKERS and "XLV" in DEFENSIVE_TICKERS


def test_warm_pulls_macro_and_targets(tmp_path: Path, monkeypatch) -> None:
    cache = _empty_cache(tmp_path)
    fetched: list[str] = []

    async def _fake_get(ticker: str) -> None:
        fetched.append(ticker.upper())

    monkeypatch.setattr(cache, "get", _fake_get)
    expert = EFxCarryExpert(price_cache=cache)
    asyncio.run(expert.warm())
    for t in UNIVERSE:
        assert t in fetched
