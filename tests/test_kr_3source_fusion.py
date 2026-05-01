from __future__ import annotations

from datetime import date

import pytest

from glostat.data.kis_client import KisDailySummary
from glostat.data.naver_kr_client import (
    FusedFlowBar,
    KrFlowBar,
    _disagree,
    _median,
    fuse_three_source_flows,
)
from glostat.data.toss_client import TossInvestorBar


def _naver_bar(d: date, foreign: int) -> KrFlowBar:
    return KrFlowBar(
        code="005930", bar_date=d, close_price=70000.0,
        organ_net=10.0, foreign_net=float(foreign),
        foreign_holdings=0.0, foreign_hold_pct=0.0,
    )


def _toss_bar(d: date, foreign_won: float) -> TossInvestorBar:
    return TossInvestorBar(
        bar_date=d, ticker="005930",
        foreign_net_won=foreign_won, institutional_net_won=200.0,
        retail_net_won=50.0,
    )


def _kis_summary(d: date, foreign_won: float) -> KisDailySummary:
    return KisDailySummary(
        code="005930", bar_date=d,
        foreign_net_won=foreign_won, institutional_net_won=300.0,
        individual_net_won=100.0,
    )


# ── helpers ─────────────────────────────────────────────────────────────


def test_median_odd_count() -> None:
    assert _median([1.0, 2.0, 3.0]) == 2.0


def test_median_even_count() -> None:
    assert _median([1.0, 2.0, 3.0, 4.0]) == 2.5


def test_median_empty_returns_zero() -> None:
    assert _median([]) == 0.0


def test_disagree_above_threshold_true() -> None:
    assert _disagree([100.0, 1000.0], 0.50) is True


def test_disagree_below_threshold_false() -> None:
    assert _disagree([100.0, 110.0], 0.50) is False


def test_disagree_single_value_false() -> None:
    assert _disagree([100.0], 0.50) is False


# ── fuse_three_source_flows ─────────────────────────────────────────────


def test_fuse_naver_only_returns_naver_source() -> None:
    bars = [_naver_bar(date(2026, 4, 1), 100), _naver_bar(date(2026, 4, 2), 200)]
    fused = fuse_three_source_flows(code="005930", naver_bars=bars)
    assert len(fused) == 2
    assert all("naver" in b.sources for b in fused)
    assert fused[0].units == "shares"
    assert fused[0].cross_validated is False


def test_fuse_kis_only_returns_kis_source_in_won() -> None:
    kis = [_kis_summary(date(2026, 4, 1), 1.0e9)]
    fused = fuse_three_source_flows(code="005930", kis_daily=kis)
    assert len(fused) == 1
    assert fused[0].sources == ("kis",)
    assert fused[0].units == "won"
    assert fused[0].foreign_net == 1.0e9


def test_fuse_toss_and_kis_cross_validated_in_won() -> None:
    toss = [_toss_bar(date(2026, 4, 1), 1.0e9)]
    kis = [_kis_summary(date(2026, 4, 1), 1.05e9)]
    fused = fuse_three_source_flows(code="005930", toss_bars=toss, kis_daily=kis)
    assert len(fused) == 1
    assert fused[0].cross_validated is True
    assert "kis" in fused[0].sources
    assert "toss" in fused[0].sources
    assert fused[0].units == "won"
    # Median of [1.0e9, 1.05e9] = 1.025e9
    assert fused[0].foreign_net == pytest.approx(1.025e9)


def test_fuse_three_sources_naver_dominant_units_shares() -> None:
    naver = [_naver_bar(date(2026, 4, 1), 100)]
    toss = [_toss_bar(date(2026, 4, 1), 1.0e9)]   # different units, ignored
    kis = [_kis_summary(date(2026, 4, 1), 1.0e9)]
    fused = fuse_three_source_flows(
        code="005930", naver_bars=naver, toss_bars=toss, kis_daily=kis,
    )
    assert len(fused) == 1
    bar = fused[0]
    # Naver-dominant → units=shares; toss/kis (won) skip the median; cross_validated=False.
    assert bar.units == "shares"
    assert bar.foreign_net == 100.0
    # All three sources are listed as "present" even though only Naver fed the median.
    assert set(bar.sources) == {"naver", "toss", "kis"}


def test_fuse_disagreement_uses_median(caplog) -> None:
    toss = [_toss_bar(date(2026, 4, 1), 1.0e9)]
    kis = [_kis_summary(date(2026, 4, 1), 5.0e9)]   # 5x toss → > 50% disagreement
    fused = fuse_three_source_flows(
        code="005930", toss_bars=toss, kis_daily=kis,
    )
    assert len(fused) == 1
    # Median = 3.0e9
    assert fused[0].foreign_net == pytest.approx(3.0e9)
    assert fused[0].cross_validated is True


def test_fuse_disagreement_threshold_param_disable() -> None:
    toss = [_toss_bar(date(2026, 4, 1), 1.0e9)]
    kis = [_kis_summary(date(2026, 4, 1), 1.6e9)]
    fused = fuse_three_source_flows(
        code="005930", toss_bars=toss, kis_daily=kis,
        disagreement_threshold=10.0,  # very high threshold = never trigger
    )
    assert len(fused) == 1
    # No warning even though sources differ.
    assert fused[0].cross_validated is True


def test_fuse_chronological_order() -> None:
    naver = [
        _naver_bar(date(2026, 4, 3), 300),
        _naver_bar(date(2026, 4, 1), 100),
        _naver_bar(date(2026, 4, 2), 200),
    ]
    fused = fuse_three_source_flows(code="005930", naver_bars=naver)
    dates = [b.bar_date for b in fused]
    assert dates == sorted(dates)


def test_fuse_empty_inputs_returns_empty() -> None:
    fused = fuse_three_source_flows(code="005930")
    assert fused == []


def test_fused_bar_dataclass_is_immutable() -> None:
    bar = FusedFlowBar(
        bar_date=date(2026, 4, 1), code="005930",
        foreign_net=100.0, organ_net=10.0, sources=("naver",),
        units="shares", cross_validated=False,
    )
    with pytest.raises((AttributeError, TypeError)):
        bar.foreign_net = 999.0  # type: ignore[misc]


def test_fuse_kis_only_per_date_no_cross() -> None:
    # KIS daily is one date — naver covers other dates; ensure both rendered.
    naver = [_naver_bar(date(2026, 4, 1), 100), _naver_bar(date(2026, 4, 2), 200)]
    kis = [_kis_summary(date(2026, 4, 2), 5.0e9)]
    fused = fuse_three_source_flows(
        code="005930", naver_bars=naver, kis_daily=kis,
    )
    assert len(fused) == 2
    # Apr 1: naver-only; Apr 2: naver dominant + kis present
    bar1 = next(b for b in fused if b.bar_date == date(2026, 4, 1))
    bar2 = next(b for b in fused if b.bar_date == date(2026, 4, 2))
    assert bar1.sources == ("naver",)
    assert set(bar2.sources) == {"kis", "naver"}
