from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.krx_short_client import KrxShortBalanceBar, KrxShortVolumeBar
from glostat.experts.e_short_selling_kr import (
    EShortSellingKrExpert,
    ShortSellingScore,
    _classify_price,
    _is_above_percentile,
    _rolling_balance_delta,
    score_short_selling,
)

# ── pure scoring helpers ────────────────────────────────────────────────


def test_classify_price_thresholds() -> None:
    assert _classify_price(1.0) == "UP"
    assert _classify_price(-1.0) == "DOWN"
    assert _classify_price(0.0) == "FLAT"
    assert _classify_price(0.4) == "FLAT"


def test_is_above_percentile_true_at_p80() -> None:
    history = [10.0, 20.0, 30.0, 40.0, 50.0, 60.0, 70.0, 80.0, 90.0, 100.0]
    # int(10 * 0.80) = 8 → compare against history[8] = 90.
    assert _is_above_percentile(95.0, history, 0.80) is True
    assert _is_above_percentile(40.0, history, 0.80) is False


def test_is_above_percentile_empty_history_false() -> None:
    assert _is_above_percentile(100.0, [], 0.80) is False


def test_rolling_balance_delta_window_3() -> None:
    bars = [
        KrxShortBalanceBar(bar_date=date(2026, 4, i), code="005930",
                            short_balance_qty=float(100 + i * 10),
                            short_balance_won=0, listed_qty=10000,
                            short_balance_ratio=1.0)
        for i in range(1, 6)
    ]
    delta = _rolling_balance_delta(bars, window=3)
    # bars[-1] = 150, bars[-4] = 120 → 30
    assert delta == 30.0


def test_rolling_balance_delta_short_history_zero() -> None:
    bars = [
        KrxShortBalanceBar(bar_date=date(2026, 4, 1), code="005930",
                            short_balance_qty=100, short_balance_won=0,
                            listed_qty=1000, short_balance_ratio=1.0),
    ]
    assert _rolling_balance_delta(bars, window=3) == 0.0


# ── score_short_selling ─────────────────────────────────────────────────


def _balance(d: date, qty: float) -> KrxShortBalanceBar:
    return KrxShortBalanceBar(
        bar_date=d, code="005930", short_balance_qty=qty,
        short_balance_won=qty * 50000, listed_qty=100000000,
        short_balance_ratio=qty / 1000000,
    )


def _volume(d: date, ratio_pct: float) -> KrxShortVolumeBar:
    return KrxShortVolumeBar(
        bar_date=d, code="005930", short_volume=10000,
        short_value_won=500000000, total_volume=100000,
        short_ratio_pct=ratio_pct,
    )


def test_score_short_cover_when_balance_decreases() -> None:
    bars = [_balance(date(2026, 4, i), 1500000 - i * 50000) for i in range(1, 8)]
    vols = [_volume(date(2026, 4, 7), 5.0)]
    score = score_short_selling(
        balance_bars=bars, volume_bars=vols, price_change_pct=0.0, code="005930",
    )
    assert score.balance_3d_delta < 0
    assert score.signal in {"SHORT_COVER", "SHORT_SQUEEZE_RISK"}


def test_score_squeeze_risk_when_balance_down_and_price_up() -> None:
    bars = [_balance(date(2026, 4, i), 1500000 - i * 100000) for i in range(1, 8)]
    vols = [_volume(date(2026, 4, 7), 5.0)]
    score = score_short_selling(
        balance_bars=bars, volume_bars=vols, price_change_pct=2.5, code="005930",
    )
    assert score.signal == "SHORT_SQUEEZE_RISK"
    assert score.direction == "LONG"


def test_score_short_pressure_when_balance_increases_above_percentile() -> None:
    # Most history low (100k), latest high (3M) → above p80.
    history_bars = [_balance(date(2026, 3, i), 100000) for i in range(1, 30)]
    latest_bar = _balance(date(2026, 4, 1), 3000000)
    bars = [*history_bars, latest_bar]
    score = score_short_selling(
        balance_bars=bars, volume_bars=[], price_change_pct=0.0, code="005930",
    )
    assert score.balance_3d_delta > 0
    assert score.signal == "SHORT_PRESSURE"


def test_score_high_short_ratio_with_price_down_short_pressure() -> None:
    bars = [_balance(date(2026, 4, i), 1000000) for i in range(1, 8)]
    vols = [_volume(date(2026, 4, 7), 12.0)]   # > 10% threshold
    score = score_short_selling(
        balance_bars=bars, volume_bars=vols, price_change_pct=-1.5, code="005930",
    )
    assert score.short_ratio_pct == 12.0
    assert score.raw_score < 0
    assert score.signal == "SHORT_PRESSURE"


def test_score_neutral_on_empty_inputs() -> None:
    score = score_short_selling(
        balance_bars=[], volume_bars=[], price_change_pct=0.0, code="005930",
    )
    assert score.direction == "NEUTRAL"
    assert score.signal == "NEUTRAL"
    assert score.net_score == 0.0


def test_score_clipped_at_three() -> None:
    # Force inputs that would push raw far over ±3.
    bars = [_balance(date(2026, 4, i), 5000000 - i * 500000) for i in range(1, 8)]
    vols = [_volume(date(2026, 4, 7), 25.0)]
    score = score_short_selling(
        balance_bars=bars, volume_bars=vols, price_change_pct=10.0, code="005930",
    )
    assert -3.0 <= score.net_score <= 3.0


def test_short_selling_score_confidence_in_unit_range() -> None:
    s = ShortSellingScore(
        code="005930", latest_balance_qty=0, balance_3d_delta=0,
        short_ratio_pct=0, price_trend="FLAT", raw_score=2.5, net_score=2.5,
        direction="LONG", signal="SHORT_COVER",
    )
    assert 0.0 <= s.confidence <= 1.0


# ── EShortSellingKrExpert ────────────────────────────────────────────────


class _StubKrx:
    def __init__(self, balances, volumes) -> None:
        self._balances = balances
        self._volumes = volumes
        self.last_snapshot_id = "stubsnap" + "0" * 56
        self.calls: list[str] = []

    async def get_short_balance(self, ticker, *, days_back=30, end=None):
        self.calls.append("balance")
        return tuple(self._balances)

    async def get_short_volume(self, ticker, *, days_back=30, end=None):
        self.calls.append("volume")
        return tuple(self._volumes)


@pytest.mark.asyncio
async def test_expert_skips_when_krx_not_configured() -> None:
    expert = EShortSellingKrExpert(krx_client=None, kospi200=frozenset({"005930"}))
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("005930", datetime.now(tz=UTC))
    assert "KRX" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_skips_for_non_kr_ticker() -> None:
    krx = _StubKrx([], [])
    expert = EShortSellingKrExpert(
        krx_client=krx,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("AAPL", datetime.now(tz=UTC))
    assert "not KR equity" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_skips_when_not_in_kospi200() -> None:
    krx = _StubKrx([], [])
    expert = EShortSellingKrExpert(
        krx_client=krx,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("999999", datetime.now(tz=UTC))
    assert "KOSPI 200" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_skips_when_krx_returns_empty() -> None:
    krx = _StubKrx([], [])
    expert = EShortSellingKrExpert(
        krx_client=krx,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("005930", datetime.now(tz=UTC))
    assert "no KRX short data" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_returns_signal_when_data_present() -> None:
    bars = [_balance(date(2026, 4, i), 1500000 - i * 50000) for i in range(1, 8)]
    vols = [_volume(date(2026, 4, 7), 5.0)]
    krx = _StubKrx(bars, vols)
    expert = EShortSellingKrExpert(
        krx_client=krx,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    sig = await expert.compute("005930", datetime(2026, 4, 7, tzinfo=UTC))
    assert sig.expert_name == "E_SHORT_SELLING_KR"
    assert sig.ticker == "005930"
    assert sig.direction in {"LONG", "SHORT", "NEUTRAL"}


@pytest.mark.asyncio
async def test_expert_from_env_returns_instance() -> None:
    expert = EShortSellingKrExpert.from_env(kospi200=frozenset({"005930"}))
    assert expert is not None
    assert expert._kospi200 == frozenset({"005930"})  # type: ignore[attr-defined]
    if expert._krx is not None:  # type: ignore[attr-defined]
        await expert._krx.aclose()  # type: ignore[attr-defined]
