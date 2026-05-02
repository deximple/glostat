from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from glostat.data.regime_us_client import (
    RegimeDataError,
    UstCurveSlope,
    VixTermStructure,
)
from glostat.data.yfinance_types import OhlcvBar, OhlcvSeries
from glostat.predictor.calibration import load_calibration
from glostat.replay.phase_us_regime_hindcast import (
    PhaseUsRegimeConfig,
    PhaseUsRegimeResult,
    UsRegimeReport,
    UsRegimeTrade,
    _Accumulator,
    _build_report,
    _close_on_or_before,
    _compute_auc,
    _compute_sharpe,
    _sample_days,
    persist_phase_us_regime_reports,
    render_phase_us_regime_summary,
    run_phase_us_regime_hindcast,
)

# ── Pure helpers ──────────────────────────────────────────────────────────


class TestSampleDays:
    def test_skips_weekends(self) -> None:
        # 2026-01-03 = Saturday; 2026-01-04 = Sunday → both excluded.
        days = _sample_days(
            start=date(2026, 1, 1),
            end=date(2026, 1, 10),
            stride=1,
        )
        weekdays = {d.weekday() for d in days}
        assert weekdays.issubset({0, 1, 2, 3, 4})

    def test_stride_seven(self) -> None:
        days = _sample_days(
            start=date(2026, 1, 1),
            end=date(2026, 1, 31),
            stride=7,
        )
        # 2026-01-01 (Thu) included; +7d → 2026-01-08 (Thu); etc.
        assert all(d.weekday() < 5 for d in days)
        assert len(days) >= 4


class TestCloseOnOrBefore:
    def _bar(self, day: int, close: float) -> OhlcvBar:
        ts = datetime(2026, 1, day, tzinfo=UTC)
        return OhlcvBar(
            ts=ts, open=close, high=close, low=close, close=close, volume=1,
        )

    def test_returns_latest_close_before_target(self) -> None:
        bars = [self._bar(d, float(d)) for d in (1, 3, 5, 7)]
        assert _close_on_or_before(bars, date(2026, 1, 6)) == 5.0

    def test_target_exactly_on_bar(self) -> None:
        bars = [self._bar(d, float(d)) for d in (1, 3, 5)]
        assert _close_on_or_before(bars, date(2026, 1, 3)) == 3.0

    def test_no_bar_before_target_returns_none(self) -> None:
        bars = [self._bar(d, float(d)) for d in (5, 6, 7)]
        assert _close_on_or_before(bars, date(2026, 1, 3)) is None


# ── Accumulator + report builder ──────────────────────────────────────────


def _trade(*, day_offset: int, score: float, direction: str,
           fwd: float) -> UsRegimeTrade:
    return UsRegimeTrade(
        thesis="E_REGIME_US",
        entry_day=date(2026, 1, 1) + timedelta(days=day_offset),
        raw_score=score,
        direction=direction,
        forward_return=fwd,
        n_basket=50,
    )


class TestUsRegimeTrade:
    def test_label_positive(self) -> None:
        t = _trade(day_offset=0, score=1.0, direction="LONG", fwd=0.02)
        assert t.label == 1

    def test_label_zero(self) -> None:
        t = _trade(day_offset=0, score=1.0, direction="LONG", fwd=-0.01)
        assert t.label == 0

    def test_signed_return_short(self) -> None:
        t = _trade(day_offset=0, score=-1.0, direction="SHORT", fwd=-0.03)
        # SHORT direction flips sign → negative drawdown becomes positive PnL.
        assert t.signed_return == pytest.approx(0.03, abs=1e-9)


class TestAccumulator:
    def test_record_skip_increments_and_buckets(self) -> None:
        acc = _Accumulator(thesis="E_REGIME_US", horizon_days=30)
        acc.record_skip("regime_data_unavailable")
        acc.record_skip("regime_data_unavailable")
        acc.record_skip("no_basket_forward_return")
        assert acc.n_skipped == 3
        assert acc.skip_breakdown["regime_data_unavailable"] == 2
        assert acc.skip_breakdown["no_basket_forward_return"] == 1

    def test_record_signal_appends_trade(self) -> None:
        acc = _Accumulator(thesis="E_REGIME_US", horizon_days=30)
        acc.record_signal(
            day=date(2026, 1, 5), raw_score=1.5, direction="LONG",
            forward_return=0.02, n_basket=42,
        )
        assert acc.n_actionable == 1
        assert len(acc.trades) == 1
        assert acc.trades[0].n_basket == 42


class TestBuildReport:
    def test_phase1b_payload_shape(self) -> None:
        acc = _Accumulator(thesis="E_REGIME_US", horizon_days=30)
        acc.n_evaluated = 10
        for i in range(5):
            acc.record_signal(
                day=date(2026, 1, 1) + timedelta(days=i * 2),
                raw_score=0.8, direction="LONG", forward_return=0.01,
                n_basket=50,
            )
        report = _build_report(
            thesis="E_REGIME_US", accumulator=acc,
            universe=("AAPL", "MSFT"),
            period_start=date(2026, 1, 1), period_end=date(2026, 1, 31),
            split_ratio=0.7,
        )
        payload = report.to_phase1b_payload()
        assert payload["report"]["expert"] == "E_REGIME_US"
        assert payload["report"]["n_trades"] == 5
        assert payload["report"]["horizon_days"] == 30
        assert "skip_breakdown" in payload["report"]

    def test_oos_degradation_capped_when_is_negative(self) -> None:
        report = UsRegimeReport(
            thesis="E_REGIME_US",
            universe=("AAPL",),
            period_start=date(2026, 1, 1),
            period_end=date(2026, 1, 31),
            horizon_days=30,
            n_universe=1, n_evaluated=10, n_skipped=0, n_actionable=10,
            n_traded=10,
            is_auc=0.4, oos_auc=0.5, overall_auc=0.45,
            is_sharpe=-0.5, oos_sharpe=0.3, overall_sharpe=-0.1,
        )
        # is_sharpe ≤ 0 → degradation = 1.0 (max).
        assert report.oos_degradation == 1.0


# ── Metric helpers ────────────────────────────────────────────────────────


class TestComputeMetrics:
    def test_auc_below_min_bars_returns_half(self) -> None:
        trades = [
            _trade(day_offset=0, score=1.0, direction="LONG", fwd=0.01),
        ]
        assert _compute_auc(trades) == 0.5

    def test_sharpe_constant_zero_returns_zero(self) -> None:
        trades = [_trade(
            day_offset=i, score=1.0, direction="LONG", fwd=0.0,
        ) for i in range(10)]
        # All-zero PnL → stdev=0 → annualized_sharpe returns 0.
        assert _compute_sharpe(trades, horizon_days=30) == 0.0

    def test_sharpe_positive_when_long_predictive(self) -> None:
        trades = [_trade(
            day_offset=i, score=1.0, direction="LONG", fwd=0.02 + 0.001 * i,
        ) for i in range(10)]
        s = _compute_sharpe(trades, horizon_days=30)
        assert s > 0.0


# ── Integration with fakes ────────────────────────────────────────────────


class _FakeYFinance:
    last_snapshot_id = "fake-snap"

    def __init__(
        self, *, bars_per_call: dict[str, list[OhlcvBar]] | None = None,
    ) -> None:
        self._bars = bars_per_call or {}

    async def get_ohlcv(
        self, ticker: str, *, start: Any, end: Any, interval: str = "1d",
    ) -> OhlcvSeries:
        bars = tuple(self._bars.get(ticker, []))
        return OhlcvSeries(ticker=ticker, interval=interval, bars=bars)


class _FakeRegimeClient:
    """Stand-in for RegimeUsClient — returns canned VIX/curve per day."""

    def __init__(
        self, *,
        vix_per_day: dict[date, VixTermStructure] | None = None,
        curve_per_day: dict[date, UstCurveSlope] | None = None,
    ) -> None:
        self._vix = vix_per_day or {}
        self._curve = curve_per_day or {}

    async def prefetch(self, *, keys: Any, earliest_as_of: Any) -> None:
        return None

    async def get_vix_term(self, *, as_of: date | None = None) -> VixTermStructure:
        if as_of is None or as_of not in self._vix:
            raise RegimeDataError(
                f"no canned vix for {as_of}",
            )
        return self._vix[as_of]

    async def get_curve_slope(
        self, *, as_of: date | None = None,
    ) -> UstCurveSlope:
        if as_of is None or as_of not in self._curve:
            raise RegimeDataError(
                f"no canned curve for {as_of}",
            )
        return self._curve[as_of]


def _bars_for_basket(*, start_close: float, n: int) -> list[OhlcvBar]:
    out: list[OhlcvBar] = []
    base = datetime(2024, 1, 2, tzinfo=UTC)
    for i in range(n):
        ts = base + timedelta(days=i)
        c = start_close * (1.0 + 0.0005 * i)  # ~+13% per year drift
        out.append(OhlcvBar(
            ts=ts, open=c, high=c, low=c, close=c, volume=1,
        ))
    return out


class TestRunPhaseUsRegimeHindcast:
    @pytest.mark.asyncio
    async def test_skips_neutral_direction_days(self) -> None:
        # Regime returns neutral (calm contango + flat curve) → expert direction
        # is NEUTRAL → trade is skipped (not recorded).
        as_of_days = [
            date(2024, 1, 8),  # Mon
            date(2024, 1, 15),  # Mon
        ]
        regime = _FakeRegimeClient(
            vix_per_day={
                d: VixTermStructure(
                    vix9d=15.0, vix3m=15.5, ratio=15.0 / 15.5,
                    in_backwardation=False,
                ) for d in as_of_days
            },
            curve_per_day={
                d: UstCurveSlope(
                    front_yield_pct=4.0, back_yield_pct=4.05,
                    slope_bps=5.0, inverted=False,
                ) for d in as_of_days
            },
        )
        yf = _FakeYFinance(bars_per_call={
            "AAPL": _bars_for_basket(start_close=200.0, n=200),
        })
        config = PhaseUsRegimeConfig(
            universe_tickers=("AAPL",),
            start=date(2024, 1, 8),
            end=date(2024, 1, 16),
            sample_stride_days=7,
            horizon_days=30,
        )
        result = await run_phase_us_regime_hindcast(
            config=config, yf_client=yf, regime_client=regime,  # type: ignore[arg-type]
        )
        # Both sample days are NEUTRAL → all skipped.
        assert result.regime_us.n_traded == 0
        assert result.regime_us.n_skipped >= 1

    @pytest.mark.asyncio
    async def test_records_long_trades_when_strongly_contango(self) -> None:
        # Strongly contango VIX (ratio=0.8) + steep curve (200bps) → LONG.
        sample_days = [date(2024, 1, 8), date(2024, 1, 15)]
        regime = _FakeRegimeClient(
            vix_per_day={
                d: VixTermStructure(
                    vix9d=14.0, vix3m=17.5, ratio=0.8,
                    in_backwardation=False,
                ) for d in sample_days
            },
            curve_per_day={
                d: UstCurveSlope(
                    front_yield_pct=2.0, back_yield_pct=4.0,
                    slope_bps=200.0, inverted=False,
                ) for d in sample_days
            },
        )
        yf = _FakeYFinance(bars_per_call={
            "AAPL": _bars_for_basket(start_close=200.0, n=200),
            "MSFT": _bars_for_basket(start_close=300.0, n=200),
        })
        config = PhaseUsRegimeConfig(
            universe_tickers=("AAPL", "MSFT"),
            start=date(2024, 1, 8),
            end=date(2024, 1, 16),
            sample_stride_days=7,
            horizon_days=30,
        )
        result = await run_phase_us_regime_hindcast(
            config=config, yf_client=yf, regime_client=regime,  # type: ignore[arg-type]
        )
        # 2 sample days, both fire LONG.
        assert result.regime_us.n_traded == 2
        for t in result.regime_us.universe:
            assert t in ("AAPL", "MSFT")
        # Drift is positive in the synthetic series → all positive labels.

    @pytest.mark.asyncio
    async def test_skip_when_no_regime_data(self) -> None:
        regime = _FakeRegimeClient()  # empty → all RegimeDataError
        yf = _FakeYFinance()
        config = PhaseUsRegimeConfig(
            universe_tickers=("AAPL",),
            start=date(2024, 1, 8),
            end=date(2024, 1, 16),
            sample_stride_days=7,
            horizon_days=30,
        )
        result = await run_phase_us_regime_hindcast(
            config=config, yf_client=yf, regime_client=regime,  # type: ignore[arg-type]
        )
        assert result.regime_us.n_traded == 0
        assert "regime_data_unavailable" in result.regime_us.skip_breakdown


# ── Persistence ───────────────────────────────────────────────────────────


class TestPersist:
    def test_writes_phase1b_json_and_summary_md(self, tmp_path: Path) -> None:
        report = UsRegimeReport(
            thesis="E_REGIME_US",
            universe=("AAPL", "MSFT"),
            period_start=date(2024, 1, 1), period_end=date(2026, 1, 1),
            horizon_days=30,
            n_universe=2, n_evaluated=100, n_skipped=10, n_actionable=90,
            n_traded=90,
            is_auc=0.55, oos_auc=0.52, overall_auc=0.54,
            is_sharpe=0.4, oos_sharpe=0.3, overall_sharpe=0.35,
            notes=("split_ratio=0.70",),
        )
        result = PhaseUsRegimeResult(regime_us=report)
        paths = persist_phase_us_regime_reports(
            result=result, output_dir=tmp_path,
        )
        assert "E_REGIME_US" in paths
        assert "summary" in paths
        json_path = paths["E_REGIME_US"]
        assert json_path.exists()
        payload = json.loads(json_path.read_text())
        assert payload["report"]["expert"] == "E_REGIME_US"
        md = paths["summary"].read_text()
        assert "E_REGIME_US" in md
        assert "AUC" in md

    def test_render_summary_includes_key_metrics(self) -> None:
        report = UsRegimeReport(
            thesis="E_REGIME_US",
            universe=("AAPL",),
            period_start=date(2024, 1, 1), period_end=date(2026, 1, 1),
            horizon_days=30,
            n_universe=1, n_evaluated=50, n_skipped=5, n_actionable=45,
            n_traded=45,
            is_auc=0.6, oos_auc=0.55, overall_auc=0.58,
            is_sharpe=0.5, oos_sharpe=0.4, overall_sharpe=0.45,
        )
        md = render_phase_us_regime_summary(PhaseUsRegimeResult(regime_us=report))
        assert "0.5800" in md
        assert "+0.4500" in md
        assert "OOS degradation" in md


# ── Calibration loader integration ────────────────────────────────────────


class TestCalibrationLoader:
    def test_loader_picks_up_phase_us_regime_report(
        self, tmp_path: Path,
    ) -> None:
        # Write a phase1b-shaped report under tmp_path/hindcast/phase_us_regime/.
        out = tmp_path / "hindcast" / "phase_us_regime"
        out.mkdir(parents=True, exist_ok=True)
        payload = {
            "report": {
                "expert": "E_REGIME_US",
                "overall_auc": 0.62,
                "overall_sharpe": 0.85,
                "is_sharpe": 1.0,
                "oos_sharpe": 0.7,
                "n_trades": 120,
            }
        }
        (out / "e_regime_us_report.json").write_text(json.dumps(payload))

        table = load_calibration(cache_dir=tmp_path)
        cal = table.entries["E_REGIME_US"]
        # Loader picked up real measurement, replacing the n=0 bootstrap.
        assert cal.n_samples == 120
        assert cal.auc == pytest.approx(0.62, abs=1e-6)
        assert cal.sharpe == pytest.approx(0.85, abs=1e-6)
