from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from glostat.data.naver_kr_client import KrFlowBar
from glostat.data.yfinance_types import (
    EarningsCalendar,
    Fundamentals,
    OhlcvBar,
    OhlcvSeries,
)
from glostat.replay.phase_kr_hindcast import (
    KrHindcastTrade,
    KrThesisReport,
    PhaseKrHindcastConfig,
    PhaseKrHindcastResult,
    persist_phase_kr_reports,
    render_phase_kr_comparison,
    run_phase_kr_hindcast,
)

# ── Pure helpers ──────────────────────────────────────────────────────────


def _make_trade(
    *, day: date | None = None, ticker: str = "005930",
    raw_score: float = 1.5, direction: str = "LONG",
    forward_return: float = 0.02,
) -> KrHindcastTrade:
    return KrHindcastTrade(
        thesis="E_FUNDAMENTAL_KR",
        ticker=ticker,
        entry_day=day or date(2024, 6, 1),
        raw_score=raw_score,
        direction=direction,
        forward_return=forward_return,
    )


def test_kr_trade_label_positive_return_is_one() -> None:
    t = _make_trade(forward_return=0.05)
    assert t.label == 1


def test_kr_trade_label_negative_return_is_zero() -> None:
    t = _make_trade(forward_return=-0.03)
    assert t.label == 0


def test_kr_trade_signed_return_long_keeps_sign() -> None:
    t = _make_trade(direction="LONG", forward_return=0.05)
    assert t.signed_return == pytest.approx(0.05)


def test_kr_trade_signed_return_short_flips_sign() -> None:
    t = _make_trade(direction="SHORT", forward_return=0.05)
    assert t.signed_return == pytest.approx(-0.05)


def test_kr_trade_signed_return_neutral_zero() -> None:
    t = _make_trade(direction="NEUTRAL", forward_return=0.05)
    assert t.signed_return == 0.0


# ── Report builder ────────────────────────────────────────────────────────


def _make_report(
    *, n_traded: int = 50, is_sharpe: float = 0.8, oos_sharpe: float = 0.4,
) -> KrThesisReport:
    return KrThesisReport(
        thesis="E_FUNDAMENTAL_KR",
        universe=("005930", "000660"),
        period_start=date(2024, 1, 1),
        period_end=date(2026, 3, 29),
        horizon_days=30,
        n_universe=2,
        n_evaluated=200,
        n_skipped=150,
        n_actionable=n_traded,
        n_traded=n_traded,
        is_auc=0.55,
        oos_auc=0.52,
        overall_auc=0.54,
        is_sharpe=is_sharpe,
        oos_sharpe=oos_sharpe,
        overall_sharpe=(is_sharpe + oos_sharpe) / 2,
    )


def test_report_oos_degradation_when_oos_lower() -> None:
    r = _make_report(is_sharpe=1.0, oos_sharpe=0.6)
    assert r.oos_degradation == pytest.approx(0.4, abs=1e-9)


def test_report_oos_degradation_zero_when_oos_higher() -> None:
    r = _make_report(is_sharpe=0.5, oos_sharpe=1.0)
    assert r.oos_degradation == 0.0


def test_report_oos_degradation_max_when_is_negative() -> None:
    r = _make_report(is_sharpe=-0.1, oos_sharpe=0.4)
    assert r.oos_degradation == 1.0


def test_report_to_phase1b_payload_has_expected_keys() -> None:
    r = _make_report()
    payload = r.to_phase1b_payload()
    assert "report" in payload
    body = payload["report"]
    for k in (
        "expert", "universe", "period_start", "period_end", "horizon_days",
        "n_signals", "n_trades", "is_sharpe", "oos_sharpe", "overall_sharpe",
        "is_auc", "oos_auc", "overall_auc",
    ):
        assert k in body, f"missing key {k!r}"


# ── Persistence + comparison rendering ────────────────────────────────────


def _make_result(report: KrThesisReport | None = None) -> PhaseKrHindcastResult:
    r = report or _make_report()
    rev = KrThesisReport(
        thesis="E_FOREIGN_REVERSAL", universe=r.universe,
        period_start=r.period_start, period_end=r.period_end, horizon_days=7,
        n_universe=r.n_universe, n_evaluated=180, n_skipped=160,
        n_actionable=20, n_traded=20,
        is_auc=0.48, oos_auc=0.50, overall_auc=0.49,
        is_sharpe=0.2, oos_sharpe=0.6, overall_sharpe=0.4,
    )
    time_r = KrThesisReport(
        thesis="E_TIME_KR", universe=r.universe,
        period_start=r.period_start, period_end=r.period_end, horizon_days=30,
        n_universe=r.n_universe, n_evaluated=200, n_skipped=190,
        n_actionable=10, n_traded=10,
        is_auc=0.51, oos_auc=0.49, overall_auc=0.50,
        is_sharpe=0.0, oos_sharpe=0.0, overall_sharpe=0.0,
    )
    return PhaseKrHindcastResult(
        fundamental_kr=r, time_kr=time_r,
        foreign_reversal=rev, skipped_tickers=("099999",),
    )


def test_persist_writes_three_jsons_and_comparison(tmp_path: Path) -> None:
    res = _make_result()
    paths = persist_phase_kr_reports(result=res, output_dir=tmp_path)
    assert "comparison" in paths
    assert (tmp_path / "phase_kr_comparison.md").exists()
    for slug in ("e_fundamental_kr", "e_time_kr", "e_foreign_reversal"):
        path = tmp_path / f"{slug}_report.json"
        assert path.exists(), f"missing {slug}"
        body = json.loads(path.read_text("utf-8"))
        assert "report" in body
        assert body["report"]["expert"]


def test_render_comparison_includes_all_three_columns() -> None:
    md = render_phase_kr_comparison(_make_result())
    assert "E_FUNDAMENTAL_KR" in md
    assert "E_TIME_KR" in md
    assert "E_FOREIGN_REVERSAL" in md
    assert "AUC (overall)" in md
    assert "Sharpe IS" in md


def test_render_comparison_shows_skipped_tickers() -> None:
    md = render_phase_kr_comparison(_make_result())
    assert "099999" in md


# ── End-to-end stubbed run ────────────────────────────────────────────────


class _StubYf:
    def __init__(self, *, return_pct: float = 0.03) -> None:
        self.last_snapshot_id = "stubsnap0000000000"
        self._return_pct = return_pct

    async def get_fundamentals(self, ticker: str):
        # Mirror Fundamentals dataclass; KR expert handles None gracefully.
        return Fundamentals(
            ticker=ticker, pe_ratio=8.0, forward_pe=8.0, eps=None,
            forward_eps=None, roe=0.16, market_cap=1.0e10,
            dividend_yield=0.025, beta=1.0,
            fifty_two_week_high=None, fifty_two_week_low=None,
        )

    async def get_ohlcv(self, ticker: str, *, start: date, end: date) -> OhlcvSeries:
        # Synthetic flat→up close path so forward return is +return_pct.
        bars: list[OhlcvBar] = []
        cur = start
        price = 100.0
        while cur <= end:
            ts = datetime(cur.year, cur.month, cur.day, tzinfo=UTC)
            bars.append(OhlcvBar(
                ts=ts, open=price, high=price * 1.005, low=price * 0.995,
                close=price, volume=1_000.0, adj_close=price,
            ))
            cur = cur + timedelta(days=1)
            price = price * (1.0 + self._return_pct / 30.0)
        return OhlcvSeries(ticker=ticker, bars=tuple(bars))

    async def get_earnings_calendar(self, ticker: str):
        return EarningsCalendar(ticker=ticker, upcoming=())


class _StubNaver:
    def __init__(self, code: str = "005930") -> None:
        self._code = code
        self._bars = self._make_bars()

    def _make_bars(self) -> list[KrFlowBar]:
        # Construct a clean REVERSAL_BUY pattern: 4 sell-days then a buy.
        out: list[KrFlowBar] = []
        base_day = date(2024, 1, 1)
        for i in range(60):
            d = base_day + timedelta(days=i)
            if i in {10, 11, 12, 13}:
                f_net = -1000.0
                o_net = -200.0
            elif i == 14:
                f_net = 2000.0  # reversal
                o_net = 500.0
            else:
                f_net = 100.0
                o_net = 100.0
            out.append(KrFlowBar(
                code=self._code, bar_date=d, close_price=100.0 + i * 0.5,
                organ_net=o_net, foreign_net=f_net,
                foreign_holdings=1.0e7, foreign_hold_pct=30.0,
            ))
        return out

    def load_cached(self, code: str) -> list[KrFlowBar]:
        return list(self._bars) if code == self._code else []

    def save_cache(self, code: str, bars) -> None:  # pragma: no cover — no-op
        pass

    async def fetch_history(self, code: str, *, max_pages: int = 30, until_date=None):
        return list(self._bars) if code == self._code else []


@pytest.mark.asyncio
async def test_run_phase_kr_hindcast_end_to_end_smoke() -> None:
    # WHY: drive the orchestrator with stubs so we don't need network. Universe of
    # one ticker; produces non-zero counts and a serialisable PhaseKrHindcastResult.
    config = PhaseKrHindcastConfig(
        universe_tickers=("005930",),
        start=date(2024, 1, 8),
        end=date(2024, 2, 15),
        sample_stride_days=7,
        split_ratio=0.7,
        max_concurrent=1,
    )
    yf = _StubYf()
    naver = _StubNaver()
    result = await run_phase_kr_hindcast(
        config=config, snapshot_broker=None,
        naver_client=naver, yf_client=yf,  # type: ignore[arg-type]
    )
    assert result.fundamental_kr.thesis == "E_FUNDAMENTAL_KR"
    assert result.time_kr.thesis == "E_TIME_KR"
    assert result.foreign_reversal.thesis == "E_FOREIGN_REVERSAL"
    # At least one of the three should have evaluated something.
    assert (
        result.fundamental_kr.n_evaluated
        + result.time_kr.n_evaluated
        + result.foreign_reversal.n_evaluated
    ) > 0
