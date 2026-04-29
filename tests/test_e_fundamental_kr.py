from __future__ import annotations

from datetime import UTC, datetime

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.data_router import DataRouter
from glostat.data.yfinance_types import Fundamentals
from glostat.experts.e_fundamental_kr import (
    EFundamentalKrExpert,
    FundamentalKrScore,
    _div_z,
    _per_z,
    _roe_z,
    _score_kr,
)

# ── pure score helpers ────────────────────────────────────────────────────


def test_per_z_returns_zero_when_per_missing() -> None:
    assert _per_z(None) == 0.0
    assert _per_z(0.0) == 0.0
    assert _per_z(-5.0) == 0.0


def test_per_z_below_median_is_negative() -> None:
    z = _per_z(8.0)  # < 11.5 median → negative
    assert z < 0


def test_per_z_above_median_is_positive() -> None:
    z = _per_z(20.0)
    assert z > 0


def test_roe_z_returns_zero_when_missing() -> None:
    assert _roe_z(None) == 0.0


def test_roe_z_high_roe_is_positive() -> None:
    assert _roe_z(0.20) > 0


def test_div_z_returns_zero_when_missing() -> None:
    assert _div_z(None) == 0.0


def test_div_z_clipped_to_band() -> None:
    # Even an extreme dividend yield gets capped at +/- 2.0.
    assert _div_z(0.50) <= 2.0
    assert _div_z(-0.50) >= -2.0


# ── _score_kr aggregator ──────────────────────────────────────────────────


def _f(per: float | None = None, roe: float | None = None,
       div: float | None = None) -> Fundamentals:
    return Fundamentals(
        ticker="005930", pe_ratio=per, forward_pe=per, eps=None,
        forward_eps=None, roe=roe, market_cap=None, dividend_yield=div,
        beta=None, fifty_two_week_high=None, fifty_two_week_low=None,
    )


def test_score_kr_neutral_for_typical_kr_megacap() -> None:
    # PER 11.5 (median), ROE 8.5% (median), div 1.8% (median) → all z=0 → neutral
    score = _score_kr(_f(per=11.5, roe=0.085, div=0.018))
    assert abs(score.net_score) < 0.5
    assert score.direction == "NEUTRAL"


def test_score_kr_long_for_cheap_high_roe() -> None:
    # Low PER + high ROE → strong LONG signal
    score = _score_kr(_f(per=6.0, roe=0.18, div=0.025))
    assert score.net_score > 1.0
    assert score.direction == "LONG"


def test_score_kr_short_for_expensive_low_roe() -> None:
    score = _score_kr(_f(per=30.0, roe=0.02, div=0.005))
    assert score.net_score < -1.0
    assert score.direction == "SHORT"


def test_score_kr_clip_at_score_clip() -> None:
    score = _score_kr(_f(per=200.0, roe=-0.20, div=0.0))
    # raw_score may go below -3 but net_score is clipped.
    assert score.net_score >= -3.0
    if score.raw_score < -3.0:
        assert score.clipped


# ── EFundamentalKrExpert.compute via stub router/client ───────────────────


class _StubYf:
    def __init__(self, f: Fundamentals) -> None:
        self._f = f
        self.last_snapshot_id = "abc1234567890123"
        self.calls: list[str] = []

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        self.calls.append(ticker)
        return self._f


def _make_router(yf: _StubYf) -> DataRouter:
    r = DataRouter()
    r.register_client("yfinance", yf)
    return r


@pytest.mark.asyncio
async def test_expert_compute_returns_signal_for_kr_ticker() -> None:
    yf = _StubYf(_f(per=8.0, roe=0.16, div=0.025))
    expert = EFundamentalKrExpert(router=_make_router(yf))
    sig = await expert.compute("005930", datetime.now(tz=UTC))
    assert sig.expert_name == "E_FUNDAMENTAL_KR"
    assert sig.direction in {"LONG", "SHORT", "NEUTRAL"}
    assert sig.ticker == "005930"
    assert "yfinance.info.kr" in sig.sources[0] or sig.sources[0] != ""


@pytest.mark.asyncio
async def test_expert_compute_appends_ks_suffix_to_yfinance() -> None:
    yf = _StubYf(_f(per=10.0, roe=0.12))
    expert = EFundamentalKrExpert(router=_make_router(yf))
    await expert.compute("005930", datetime.now(tz=UTC))
    assert yf.calls == ["005930.KS"]


@pytest.mark.asyncio
async def test_expert_compute_handles_pre_suffixed_ticker() -> None:
    yf = _StubYf(_f(per=10.0, roe=0.12))
    expert = EFundamentalKrExpert(router=_make_router(yf))
    await expert.compute("005930.KS", datetime.now(tz=UTC))
    # Already suffixed → not re-appended.
    assert yf.calls == ["005930.KS"]


@pytest.mark.asyncio
async def test_expert_compute_skip_when_per_and_roe_missing() -> None:
    yf = _StubYf(_f(per=None, roe=None))
    expert = EFundamentalKrExpert(router=_make_router(yf))
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("005930", datetime.now(tz=UTC))
    assert "missing PER and ROE" in str(exc.value)


class _CrashYf:
    last_snapshot_id = "xx"

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        raise RuntimeError("yfinance boom")


@pytest.mark.asyncio
async def test_expert_compute_skip_when_yfinance_crashes() -> None:
    expert = EFundamentalKrExpert(router=_make_router(_CrashYf()))  # type: ignore[arg-type]
    with pytest.raises(ExpertSkipError):
        await expert.compute("005930", datetime.now(tz=UTC))


def test_score_dataclass_invariants() -> None:
    s = FundamentalKrScore(
        per_z=0.5, roe_z=0.3, div_z=0.0, net_score=0.4, raw_score=0.4,
    )
    assert s.confidence < 1.0
    assert s.direction == "NEUTRAL"
    assert s.clipped is False
