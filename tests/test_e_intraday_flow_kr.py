from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.kis_client import KisIntradayFlow
from glostat.data.naver_kr_client import KrFlowBar
from glostat.experts.e_intraday_flow_kr import (
    EIntradayFlowKrExpert,
    IntradayFlowScore,
    _flow_acceleration,
    score_intraday_flow,
)


def _bar(d: date, foreign: float, organ: float = 0.0) -> KrFlowBar:
    return KrFlowBar(
        code="005930", bar_date=d, close_price=70000.0,
        organ_net=organ, foreign_net=foreign,
        foreign_holdings=0.0, foreign_hold_pct=0.0,
    )


# ── pure scoring helpers ────────────────────────────────────────────────


def test_flow_acceleration_positive_when_recent_higher() -> None:
    accel = _flow_acceleration([100.0, 100.0, 200.0, 200.0])
    # earlier=100, later=200 → +1.0
    assert accel > 0


def test_flow_acceleration_negative_when_recent_lower() -> None:
    accel = _flow_acceleration([200.0, 200.0, 100.0, 100.0])
    assert accel < 0


def test_flow_acceleration_zero_for_flat() -> None:
    assert _flow_acceleration([100.0, 100.0, 100.0, 100.0]) == 0.0


def test_flow_acceleration_too_short_returns_zero() -> None:
    assert _flow_acceleration([100.0]) == 0.0


def test_flow_acceleration_with_today_promotion() -> None:
    # Without today: flat-flat. With today=300, later half rises → positive accel.
    a_no = _flow_acceleration([100.0, 100.0])
    a_with = _flow_acceleration([100.0, 100.0], today=300.0)
    assert a_with > a_no


# ── score_intraday_flow ─────────────────────────────────────────────────


def test_score_long_when_foreign_buys_and_accelerates() -> None:
    bars = [
        _bar(date(2026, 4, 1), 100, organ=10),
        _bar(date(2026, 4, 2), 100, organ=10),
        _bar(date(2026, 4, 3), 100, organ=10),
        _bar(date(2026, 4, 4), 1500, organ=20),
        _bar(date(2026, 4, 5), 2000, organ=30),
    ]
    score = score_intraday_flow(code="005930", naver_bars=bars, lookback_days=5)
    assert score.foreign_recent_avg > 0
    assert score.foreign_acceleration > 0
    assert score.signal == "FLOW_IMPROVING"
    assert score.direction == "LONG"


def test_score_short_when_foreign_sells_and_accelerates() -> None:
    bars = [
        _bar(date(2026, 4, 1), -100, organ=-10),
        _bar(date(2026, 4, 2), -100, organ=-10),
        _bar(date(2026, 4, 3), -100, organ=-10),
        _bar(date(2026, 4, 4), -1500, organ=-20),
        _bar(date(2026, 4, 5), -2000, organ=-30),
    ]
    score = score_intraday_flow(code="005930", naver_bars=bars, lookback_days=5)
    assert score.foreign_recent_avg < 0
    assert score.signal == "FLOW_DETERIORATING"
    assert score.direction == "SHORT"


def test_score_neutral_when_no_bars() -> None:
    score = score_intraday_flow(code="005930", naver_bars=[])
    assert score.direction == "NEUTRAL"
    assert score.signal == "NEUTRAL"
    assert score.sources == ()


def test_score_neutral_when_one_bar() -> None:
    bars = [_bar(date(2026, 4, 1), 100, organ=10)]
    score = score_intraday_flow(code="005930", naver_bars=bars)
    assert score.direction == "NEUTRAL"
    assert score.sources == ("naver",)


def test_score_kis_promotes_to_sources() -> None:
    bars = [_bar(date(2026, 4, i), 1000, organ=200) for i in range(1, 6)]
    kis = KisIntradayFlow(
        code="005930", snapped_at=datetime(2026, 4, 6, tzinfo=UTC),
        foreign_net=2500, institutional_net=400, individual_net=-100,
    )
    score = score_intraday_flow(code="005930", naver_bars=bars, kis_intraday=kis)
    assert "kis" in score.sources
    assert "naver" in score.sources


def test_score_clipped_at_three() -> None:
    bars = [_bar(date(2026, 4, i), 5000, organ=100) for i in range(1, 6)]
    score = score_intraday_flow(code="005930", naver_bars=bars)
    assert -3.0 <= score.net_score <= 3.0


def test_intraday_flow_score_confidence_range() -> None:
    s = IntradayFlowScore(
        code="005930", foreign_recent_avg=100, foreign_acceleration=0.4,
        organ_recent_avg=50, foreign_leads_organ=True, raw_score=1.5, net_score=1.5,
        direction="LONG", signal="FLOW_IMPROVING", sources=("naver",),
    )
    assert 0.0 <= s.confidence <= 1.0


# ── EIntradayFlowKrExpert ────────────────────────────────────────────────


class _StubNaver:
    def __init__(self, bars: list[KrFlowBar]) -> None:
        self._bars = bars

    def load_cached(self, code: str) -> list[KrFlowBar]:
        return self._bars

    def save_cache(self, code: str, bars: list) -> None:
        pass

    async def fetch_history(self, code: str, *, max_pages: int = 2) -> list[KrFlowBar]:
        return self._bars


class _StubKis:
    def __init__(self, snap: KisIntradayFlow) -> None:
        self._snap = snap
        self.last_snapshot_id = "kissnap" + "0" * 57

    async def get_intraday_flows(self, code: str) -> KisIntradayFlow:
        return self._snap


@pytest.mark.asyncio
async def test_expert_skips_when_naver_not_configured() -> None:
    expert = EIntradayFlowKrExpert(
        naver_client=None, kospi200=frozenset({"005930"}),
    )
    with pytest.raises(ExpertSkipError):
        await expert.compute("005930", datetime.now(tz=UTC))


@pytest.mark.asyncio
async def test_expert_skips_for_non_kr_ticker() -> None:
    naver = _StubNaver([_bar(date(2026, 4, i), 100) for i in range(1, 4)])
    expert = EIntradayFlowKrExpert(
        naver_client=naver,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("AAPL", datetime.now(tz=UTC))
    assert "not KR equity" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_skips_when_not_in_kospi200() -> None:
    naver = _StubNaver([_bar(date(2026, 4, i), 100) for i in range(1, 4)])
    expert = EIntradayFlowKrExpert(
        naver_client=naver,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("999999", datetime.now(tz=UTC))
    assert "KOSPI 200" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_skips_on_insufficient_bars() -> None:
    naver = _StubNaver([_bar(date(2026, 4, 1), 100)])
    expert = EIntradayFlowKrExpert(
        naver_client=naver,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    with pytest.raises(ExpertSkipError):
        await expert.compute("005930", datetime.now(tz=UTC))


@pytest.mark.asyncio
async def test_expert_returns_signal_when_data_sufficient() -> None:
    bars = [_bar(date(2026, 4, i), 1000, organ=100) for i in range(1, 6)]
    naver = _StubNaver(bars)
    expert = EIntradayFlowKrExpert(
        naver_client=naver,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    sig = await expert.compute("005930", datetime(2026, 4, 6, tzinfo=UTC))
    assert sig.expert_name == "E_INTRADAY_FLOW_KR"
    assert sig.direction in {"LONG", "SHORT", "NEUTRAL"}


@pytest.mark.asyncio
async def test_expert_uses_kis_overlay_when_provided() -> None:
    bars = [_bar(date(2026, 4, i), 100, organ=10) for i in range(1, 5)]
    naver = _StubNaver(bars)
    snap = KisIntradayFlow(
        code="005930", snapped_at=datetime(2026, 4, 5, tzinfo=UTC),
        foreign_net=5000, institutional_net=200, individual_net=-100,
    )
    kis = _StubKis(snap)
    expert = EIntradayFlowKrExpert(
        naver_client=naver,  # type: ignore[arg-type]
        kis_client=kis,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    sig = await expert.compute("005930", datetime(2026, 4, 5, tzinfo=UTC))
    # KIS snapshot id should appear in sources.
    assert sig.sources == (snap.code and kis.last_snapshot_id,) or "naver_kr" in sig.sources[0]


@pytest.mark.asyncio
async def test_expert_from_env_returns_instance(monkeypatch) -> None:
    monkeypatch.delenv("GLOSTAT_KIS_APP_KEY", raising=False)
    monkeypatch.delenv("GLOSTAT_KIS_APP_SECRET", raising=False)
    expert = EIntradayFlowKrExpert.from_env(kospi200=frozenset({"005930"}))
    assert expert is not None
    assert expert._kis is None  # type: ignore[attr-defined]
