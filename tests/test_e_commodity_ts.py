from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from glostat.data.cftc_client import CotRecord
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_types import OhlcvBar, OhlcvSeries
from glostat.experts.e_commodity_ts import (
    ETF_TO_COT_CONTRACT,
    UNIVERSE,
    ECommodityTsExpert,
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


def _trend_series(
    ticker: str, start: date, n_days: int, start_price: float, daily_pct: float
) -> list[tuple[date, float]]:
    out: list[tuple[date, float]] = []
    price = start_price
    for i in range(n_days):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        out.append((d, price))
        price *= 1.0 + daily_pct
    return out


def _patch_cache(
    cache: PriceCache, mapping: dict[str, list[tuple[date, float]]]
) -> None:
    for t, closes in mapping.items():
        cache._mem[t.upper()] = _series(t, closes)


def _empty_cache(tmp_path: Path) -> PriceCache:
    from glostat.data.yfinance_client import YFinanceClient
    broker = SnapshotBroker(root=tmp_path / "snap")
    yf = YFinanceClient(snapshot_broker=broker)
    return PriceCache(client=yf, start=date(2023, 1, 1), end=date(2024, 12, 31),
                      cache_dir=tmp_path / "ohlcv")


def test_universe_size_is_ten() -> None:
    assert len(UNIVERSE) == 10
    assert ETF_TO_COT_CONTRACT["URA"] == ""  # URA has no COT


def test_signal_neutral_when_outside_universe(tmp_path: Path) -> None:
    cache = _empty_cache(tmp_path)
    expert = ECommodityTsExpert(price_cache=cache, cftc_client=None)
    sig = asyncio.run(expert.signal_for("AAPL", date(2024, 6, 15)))
    assert sig.direction == "NEUTRAL"


def test_signal_neutral_when_history_too_short(tmp_path: Path) -> None:
    cache = _empty_cache(tmp_path)
    closes = _trend_series("USO", date(2024, 1, 1), 30, 100.0, 0.001)
    _patch_cache(cache, {"USO": closes})
    expert = ECommodityTsExpert(price_cache=cache, cftc_client=None)
    sig = asyncio.run(expert.signal_for("USO", date(2024, 1, 30)))
    assert sig.direction == "NEUTRAL"


def test_ts_only_long_when_uptrend_and_no_cot(tmp_path: Path) -> None:
    cache = _empty_cache(tmp_path)
    # 350 trading days at +0.2%/day → strong uptrend, price > 200dMA.
    closes = _trend_series("URA", date(2023, 1, 1), 600, 50.0, 0.002)
    _patch_cache(cache, {"URA": closes})
    expert = ECommodityTsExpert(price_cache=cache, cftc_client=None)
    sig = asyncio.run(expert.signal_for("URA", date(2024, 6, 28)))
    assert sig.direction == "LONG"
    assert sig.score > 1.0


def test_ts_only_short_when_downtrend_and_no_cot(tmp_path: Path) -> None:
    cache = _empty_cache(tmp_path)
    closes = _trend_series("URA", date(2023, 1, 1), 600, 100.0, -0.002)
    _patch_cache(cache, {"URA": closes})
    expert = ECommodityTsExpert(price_cache=cache, cftc_client=None)
    sig = asyncio.run(expert.signal_for("URA", date(2024, 6, 28)))
    assert sig.direction == "SHORT"
    assert sig.score < -1.0


def test_signal_amplifies_when_ts_and_cot_agree(tmp_path: Path) -> None:
    cache = _empty_cache(tmp_path)
    # USO uptrend + extreme commercial LONG (rank > 0.85).
    _patch_cache(cache, {"USO": _trend_series(
        "USO", date(2023, 1, 1), 600, 50.0, 0.002)})

    class _FakeCftc:
        last_snapshot_id = None

        async def fetch_range(self, start: date, end: date) -> tuple[CotRecord, ...]:
            recs: list[CotRecord] = []
            base = date(2019, 1, 1)
            for i in range(260):
                rec = CotRecord(
                    contract="WTI_CRUDE", market_name="WTI",
                    report_date=base + timedelta(weeks=i),
                    open_interest=1_000_000,
                    commercial_long=200_000 + i * 100,  # rising → latest rank ≈ 1.0
                    commercial_short=100_000,
                    noncommercial_long=0, noncommercial_short=0,
                )
                recs.append(rec)
            return tuple(recs)

    expert = ECommodityTsExpert(price_cache=cache, cftc_client=_FakeCftc())
    sig = asyncio.run(expert.signal_for("USO", date(2024, 6, 28)))
    assert sig.direction == "LONG"
    assert abs(sig.score) >= 1.0


def test_signal_collapses_when_ts_and_cot_disagree(tmp_path: Path) -> None:
    cache = _empty_cache(tmp_path)
    _patch_cache(cache, {"USO": _trend_series(
        "USO", date(2023, 1, 1), 600, 50.0, 0.002)})

    class _FakeCftcLowRank:
        last_snapshot_id = None

        async def fetch_range(self, start: date, end: date) -> tuple[CotRecord, ...]:
            base = date(2019, 1, 1)
            recs: list[CotRecord] = []
            for i in range(260):
                rec = CotRecord(
                    contract="WTI_CRUDE", market_name="WTI",
                    report_date=base + timedelta(weeks=i),
                    open_interest=1_000_000,
                    # Latest record has the SMALLEST commercial_long → rank 0.0.
                    commercial_long=200_000 - i * 100,
                    commercial_short=100_000,
                    noncommercial_long=0, noncommercial_short=0,
                )
                recs.append(rec)
            return tuple(recs)

    expert = ECommodityTsExpert(price_cache=cache, cftc_client=_FakeCftcLowRank())
    sig = asyncio.run(expert.signal_for("USO", date(2024, 6, 28)))
    # Disagreement weight < |1.0| → NEUTRAL band.
    assert sig.direction == "NEUTRAL"
    assert abs(sig.score) < 1.0


def test_warm_calls_cot_fetcher_when_present(tmp_path: Path) -> None:
    cache = _empty_cache(tmp_path)

    calls: list[tuple[date, date]] = []

    class _FakeCftc:
        last_snapshot_id = None

        async def fetch_range(self, start: date, end: date) -> tuple[CotRecord, ...]:
            calls.append((start, end))
            return ()

    expert = ECommodityTsExpert(price_cache=cache, cftc_client=_FakeCftc())
    asyncio.run(expert.warm_cot(date(2024, 1, 1), date(2024, 12, 31)))
    assert calls
    assert calls[0][0].year == 2018  # 6y back


def test_etf_to_cot_contract_keys_match_universe() -> None:
    assert set(ETF_TO_COT_CONTRACT.keys()) == set(UNIVERSE)
