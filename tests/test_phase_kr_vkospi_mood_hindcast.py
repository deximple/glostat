from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from glostat.data.vkospi_client import VkospiBar, VkospiClient
from glostat.data.yfinance_types import Fundamentals, OhlcvBar, OhlcvSeries
from glostat.experts.e_vkospi_mood_kr import SmallCapResolver
from glostat.predictor.calibration import load_calibration
from glostat.replay.phase_kr_vkospi_mood_hindcast import (
    PhaseKrVkospiConfig,
    PhaseKrVkospiResult,
    VkospiMoodReport,
    VkospiMoodTrade,
    _Accumulator,
    _build_report,
    _close_on_or_before,
    _compute_auc,
    _compute_sharpe,
    _sample_days,
    persist_phase_kr_vkospi_reports,
    render_phase_kr_vkospi_summary,
    run_phase_kr_vkospi_mood_hindcast,
)

# ── Pure helpers ─────────────────────────────────────────────────────────


class TestSampleDays:
    def test_skips_weekends(self) -> None:
        days = _sample_days(
            start=date(2026, 1, 1), end=date(2026, 1, 10), stride=1,
        )
        assert all(d.weekday() < 5 for d in days)


class TestCloseOnOrBefore:
    def _bar(self, day: int, close: float) -> OhlcvBar:
        ts = datetime(2026, 1, day, tzinfo=UTC)
        return OhlcvBar(
            ts=ts, open=close, high=close, low=close, close=close, volume=1,
        )

    def test_returns_latest_close_on_or_before(self) -> None:
        bars = [self._bar(d, float(d)) for d in (1, 3, 5, 7)]
        assert _close_on_or_before(bars, date(2026, 1, 6)) == 5.0

    def test_no_bar_before_target_returns_none(self) -> None:
        bars = [self._bar(d, float(d)) for d in (5, 6, 7)]
        assert _close_on_or_before(bars, date(2026, 1, 3)) is None


# ── Accumulator + report builder ────────────────────────────────────────


def _trade(
    *, day_offset: int, raw_score: float, direction: str, fwd: float,
    n_basket: int = 5,
) -> VkospiMoodTrade:
    return VkospiMoodTrade(
        thesis="E_VKOSPI_MOOD_KR",
        entry_day=date(2024, 1, 2) + timedelta(days=day_offset),
        raw_score=raw_score, direction=direction,
        forward_return=fwd, n_basket=n_basket,
    )


class TestVkospiMoodTrade:
    def test_label_positive(self) -> None:
        t = _trade(day_offset=0, raw_score=1.0, direction="LONG", fwd=0.02)
        assert t.label == 1

    def test_label_zero(self) -> None:
        t = _trade(day_offset=0, raw_score=1.0, direction="LONG", fwd=-0.01)
        assert t.label == 0

    def test_signed_return_long(self) -> None:
        t = _trade(day_offset=0, raw_score=1.0, direction="LONG", fwd=0.03)
        assert t.signed_return == pytest.approx(0.03, abs=1e-9)


class TestAccumulator:
    def test_record_skip_buckets(self) -> None:
        acc = _Accumulator(thesis="E_VKOSPI_MOOD_KR", horizon_days=20)
        acc.record_skip("vkospi_unavailable")
        acc.record_skip("vkospi_unavailable")
        acc.record_skip("below_threshold")
        assert acc.n_skipped == 3
        assert acc.skip_breakdown["vkospi_unavailable"] == 2
        assert acc.skip_breakdown["below_threshold"] == 1

    def test_record_signal_appends(self) -> None:
        acc = _Accumulator(thesis="E_VKOSPI_MOOD_KR", horizon_days=20)
        acc.record_signal(
            day=date(2024, 6, 1), raw_score=1.5, direction="LONG",
            forward_return=0.02, n_basket=3,
        )
        assert acc.n_actionable == 1
        assert len(acc.trades) == 1
        assert acc.trades[0].n_basket == 3


class TestBuildReport:
    def test_phase1b_payload_shape(self) -> None:
        acc = _Accumulator(thesis="E_VKOSPI_MOOD_KR", horizon_days=20)
        acc.n_evaluated = 30
        for i in range(8):
            acc.record_signal(
                day=date(2024, 1, 2) + timedelta(days=i * 4),
                raw_score=0.8, direction="LONG", forward_return=0.01,
                n_basket=5,
            )
        report = _build_report(
            thesis="E_VKOSPI_MOOD_KR", accumulator=acc,
            universe=("005930",),
            period_start=date(2024, 1, 1), period_end=date(2024, 12, 31),
            split_ratio=0.7,
        )
        payload = report.to_phase1b_payload()
        assert payload["report"]["expert"] == "E_VKOSPI_MOOD_KR"
        assert payload["report"]["n_trades"] == 8
        assert payload["report"]["horizon_days"] == 20

    def test_oos_degradation_capped_when_is_negative(self) -> None:
        report = VkospiMoodReport(
            thesis="E_VKOSPI_MOOD_KR",
            universe=("005930",),
            period_start=date(2024, 1, 1), period_end=date(2024, 12, 31),
            horizon_days=20,
            n_universe=1, n_evaluated=10, n_skipped=0, n_actionable=10,
            n_traded=10,
            is_auc=0.4, oos_auc=0.5, overall_auc=0.45,
            is_sharpe=-0.5, oos_sharpe=0.3, overall_sharpe=-0.1,
        )
        assert report.oos_degradation == 1.0


class TestComputeMetrics:
    def test_auc_below_min_returns_half(self) -> None:
        assert _compute_auc([_trade(
            day_offset=0, raw_score=1.0, direction="LONG", fwd=0.01,
        )]) == 0.5

    def test_sharpe_zero_when_constant(self) -> None:
        trades = [_trade(
            day_offset=i, raw_score=1.0, direction="LONG", fwd=0.0,
        ) for i in range(10)]
        assert _compute_sharpe(trades, horizon_days=20) == 0.0


# ── Integration with fakes ───────────────────────────────────────────────


class _FakeSmallCapResolver(SmallCapResolver):
    def __init__(self, small: bool = False) -> None:
        self._small = small

    async def is_small_cap(self, code: str, as_of: date) -> bool:
        return self._small


class _FakeYFinance:
    last_snapshot_id = "fake-snap"

    def __init__(
        self, *,
        ohlcv_per_ticker: dict[str, tuple[OhlcvBar, ...]] | None = None,
    ) -> None:
        self._ohlcv = ohlcv_per_ticker or {}

    async def get_ohlcv(
        self, ticker: str, *, start: Any, end: Any, interval: str = "1d",
    ) -> OhlcvSeries:
        return OhlcvSeries(
            ticker=ticker, interval=interval,
            bars=self._ohlcv.get(ticker, ()),
        )

    async def get_fundamentals(self, ticker: str) -> Fundamentals:
        return Fundamentals(
            ticker=ticker, pe_ratio=None, forward_pe=None, eps=None,
            forward_eps=None, roe=None, market_cap=None,
            dividend_yield=None, beta=None,
            fifty_two_week_high=None, fifty_two_week_low=None,
        )


def _vkospi_with(*pairs: tuple[date, float]) -> VkospiClient:
    bars = tuple(VkospiBar(bar_date=d, close=c) for d, c in pairs)

    async def provider(_s: date, _e: date) -> tuple[VkospiBar, ...]:
        return bars

    client = VkospiClient()
    client.set_history_provider(provider)
    return client


def _make_drift_event_bars() -> tuple[OhlcvBar, ...]:
    """Build a price series with a +25% jump on Mon 2024-01-08 (index 6).

    The score formula needs |r|>0.10 AND |Δ|>0.10 BOTH well above thresholds
    to clear _DIRECTION_THRESHOLD=0.6. With r=+25% (mag=1.5) and ΔVKOSPI=-15%
    (vol_term=0.75), raw = 1.5*0.75 = 1.125 → LONG.
    """
    bars: list[OhlcvBar] = []
    base_date = datetime(2024, 1, 2, tzinfo=UTC)
    # Index 0..5 = 100; index 6 (2024-01-08, Mon) = 125 → +25% return.
    prices = [100.0] * 6 + [125.0]
    for _ in range(28):
        prices.append(prices[-1] * 1.005)
    for i, p in enumerate(prices):
        ts = base_date + timedelta(days=i)
        bars.append(OhlcvBar(
            ts=ts, open=p, high=p, low=p, close=p, volume=1,
        ))
    return tuple(bars)


class TestRunPhaseKrVkospiMoodHindcast:
    @pytest.mark.asyncio
    async def test_skips_when_vkospi_unavailable(self) -> None:
        # No VKOSPI provider → vkospi_client raises at get_delta_at.
        vkospi = VkospiClient()  # no provider
        yf = _FakeYFinance()
        config = PhaseKrVkospiConfig(
            universe_tickers=("005930",),
            start=date(2024, 1, 8), end=date(2024, 1, 10),
            sample_stride_days=1,
        )
        result = await run_phase_kr_vkospi_mood_hindcast(
            config=config, vkospi_client=vkospi,
            yf_client=yf,  # type: ignore[arg-type]
            small_cap_resolver=_FakeSmallCapResolver(),
        )
        assert result.vkospi_mood_kr.n_traded == 0
        assert "vkospi_unavailable" in result.vkospi_mood_kr.skip_breakdown

    @pytest.mark.asyncio
    async def test_records_drift_aligned_long_basket(self) -> None:
        # Drift case: VKOSPI down (-15%), big positive r_t (+25%), forward
        # drift positive → records LONG basket trade.
        # raw = (0.25-0.10)*10 * min(0.20,0.15)*5 = 1.5 * 0.75 = 1.125 > 0.6
        vkospi = _vkospi_with(
            (date(2024, 1, 5), 22.0),   # prior
            (date(2024, 1, 8), 18.7),   # event day, -15%
        )
        bars = _make_drift_event_bars()
        yf = _FakeYFinance(ohlcv_per_ticker={"005930.KS": bars})
        config = PhaseKrVkospiConfig(
            universe_tickers=("005930",),
            start=date(2024, 1, 8), end=date(2024, 1, 8),
            sample_stride_days=1,
        )
        result = await run_phase_kr_vkospi_mood_hindcast(
            config=config, vkospi_client=vkospi,
            yf_client=yf,  # type: ignore[arg-type]
            small_cap_resolver=_FakeSmallCapResolver(),
        )
        # Score is positive (drift_aligned + |r|>10%) → 1 basket trade
        assert result.vkospi_mood_kr.n_traded == 1
        trade = result.vkospi_mood_kr.universe
        assert trade == ("005930",)

    @pytest.mark.asyncio
    async def test_misaligned_event_does_not_record(self) -> None:
        # VKOSPI UP (+15%) + r_t UP (+25%) → misaligned_up_up → NEUTRAL.
        vkospi = _vkospi_with(
            (date(2024, 1, 5), 18.0),
            (date(2024, 1, 8), 20.7),   # +15% (fear)
        )
        bars = _make_drift_event_bars()
        yf = _FakeYFinance(ohlcv_per_ticker={"005930.KS": bars})
        config = PhaseKrVkospiConfig(
            universe_tickers=("005930",),
            start=date(2024, 1, 8), end=date(2024, 1, 8),
            sample_stride_days=1,
        )
        result = await run_phase_kr_vkospi_mood_hindcast(
            config=config, vkospi_client=vkospi,
            yf_client=yf,  # type: ignore[arg-type]
            small_cap_resolver=_FakeSmallCapResolver(),
        )
        assert result.vkospi_mood_kr.n_traded == 0
        assert "misaligned_or_neutral" in result.vkospi_mood_kr.skip_breakdown

    @pytest.mark.asyncio
    async def test_below_threshold_event_does_not_record(self) -> None:
        # Build OHLCV with only a small +5% jump (below 10% threshold).
        bars: list[OhlcvBar] = []
        base = datetime(2024, 1, 2, tzinfo=UTC)
        prices = [100.0] * 5 + [105.0]   # +5%
        for _i in range(1, 25):
            prices.append(prices[-1] * 1.001)
        for i, p in enumerate(prices):
            ts = base + timedelta(days=i)
            bars.append(OhlcvBar(
                ts=ts, open=p, high=p, low=p, close=p, volume=1,
            ))
        yf = _FakeYFinance(ohlcv_per_ticker={"005930.KS": tuple(bars)})
        vkospi = _vkospi_with(
            (date(2024, 1, 5), 22.0),
            (date(2024, 1, 8), 19.8),
        )
        config = PhaseKrVkospiConfig(
            universe_tickers=("005930",),
            start=date(2024, 1, 8), end=date(2024, 1, 8),
            sample_stride_days=1,
        )
        result = await run_phase_kr_vkospi_mood_hindcast(
            config=config, vkospi_client=vkospi,
            yf_client=yf,  # type: ignore[arg-type]
            small_cap_resolver=_FakeSmallCapResolver(),
        )
        assert result.vkospi_mood_kr.n_traded == 0
        assert "below_threshold" in result.vkospi_mood_kr.skip_breakdown


# ── Persistence ──────────────────────────────────────────────────────────


class TestPersist:
    def test_writes_phase1b_json_and_summary(self, tmp_path: Path) -> None:
        report = VkospiMoodReport(
            thesis="E_VKOSPI_MOOD_KR",
            universe=("005930", "000660"),
            period_start=date(2024, 1, 1), period_end=date(2026, 3, 31),
            horizon_days=20,
            n_universe=2, n_evaluated=500, n_skipped=400, n_actionable=100,
            n_traded=100,
            is_auc=0.55, oos_auc=0.52, overall_auc=0.54,
            is_sharpe=0.3, oos_sharpe=0.2, overall_sharpe=0.25,
        )
        result = PhaseKrVkospiResult(vkospi_mood_kr=report)
        paths = persist_phase_kr_vkospi_reports(
            result=result, output_dir=tmp_path,
        )
        assert "E_VKOSPI_MOOD_KR" in paths
        assert "summary" in paths
        json_path = paths["E_VKOSPI_MOOD_KR"]
        assert json_path.exists()
        payload = json.loads(json_path.read_text())
        assert payload["report"]["expert"] == "E_VKOSPI_MOOD_KR"

    def test_render_summary_includes_metrics(self) -> None:
        report = VkospiMoodReport(
            thesis="E_VKOSPI_MOOD_KR",
            universe=("005930",),
            period_start=date(2024, 1, 1), period_end=date(2026, 3, 31),
            horizon_days=20,
            n_universe=1, n_evaluated=100, n_skipped=80, n_actionable=20,
            n_traded=20,
            is_auc=0.6, oos_auc=0.55, overall_auc=0.58,
            is_sharpe=0.5, oos_sharpe=0.4, overall_sharpe=0.45,
        )
        md = render_phase_kr_vkospi_summary(
            PhaseKrVkospiResult(vkospi_mood_kr=report)
        )
        assert "0.5800" in md
        assert "+0.4500" in md
        assert "OOS degradation" in md


# ── Calibration loader integration ────────────────────────────────────────


class TestCalibrationLoader:
    def test_loader_picks_up_phase_kr_vkospi_report(
        self, tmp_path: Path,
    ) -> None:
        out = tmp_path / "hindcast" / "phase_kr_vkospi_mood"
        out.mkdir(parents=True)
        payload = {
            "report": {
                "expert": "E_VKOSPI_MOOD_KR",
                "overall_auc": 0.58,
                "overall_sharpe": 0.45,
                "is_sharpe": 0.5,
                "oos_sharpe": 0.4,
                "n_trades": 95,
            }
        }
        (out / "e_vkospi_mood_kr_report.json").write_text(json.dumps(payload))
        table = load_calibration(cache_dir=tmp_path)
        cal = table.entries["E_VKOSPI_MOOD_KR"]
        assert cal.n_samples == 95
        assert cal.auc == pytest.approx(0.58, abs=1e-6)
        assert cal.sharpe == pytest.approx(0.45, abs=1e-6)
