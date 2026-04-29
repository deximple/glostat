from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from glostat.data.sec_edgar_form4 import Form4Transaction
from glostat.experts.e_insider_cluster import EInsiderClusterExpert
from glostat.phase1b.runner_fomc_drift import run_fomc_drift_hindcast
from glostat.phase1b.runner_insider_cluster import run_insider_cluster_hindcast
from glostat.phase1b.runner_pead import run_pead_hindcast
from glostat.phase1b.runner_sector_rotation import run_sector_rotation_hindcast


@dataclass
class _MockCache:
    """Pre-baked OHLCV close table — close_at_or_before + forward_return only."""

    closes: dict[str, dict[date, float]]

    async def get(self, ticker):
        return None

    def close_at_or_before(self, ticker, day):
        s = self.closes.get(ticker.upper(), {})
        best = None
        best_d = None
        for d, c in s.items():
            if d > day:
                continue
            if best_d is None or d > best_d:
                best_d = d
                best = c
        return best

    def forward_return(self, ticker, day, horizon_days=30):
        c0 = self.close_at_or_before(ticker, day)
        c1 = self.close_at_or_before(ticker, day + timedelta(days=horizon_days))
        if c0 is None or c1 is None or c0 <= 0:
            return None
        return (c1 - c0) / c0


def _line(start: date, days: int, c0: float, daily_growth: float) -> dict[date, float]:
    out: dict[date, float] = {}
    p = c0
    for i in range(days):
        d = start + timedelta(days=i)
        if d.weekday() >= 5:
            continue
        out[d] = p
        p *= 1.0 + daily_growth
    return out


def test_run_sector_rotation_hindcast_smoke():
    from glostat.experts.e_sector_rotation import BENCHMARK, SECTOR_ETFS

    closes = {}
    for i, etf in enumerate(SECTOR_ETFS):
        closes[etf] = _line(date(2024, 1, 1), 600, 100.0, i * 0.0003)
    closes[BENCHMARK] = _line(date(2024, 1, 1), 600, 400.0, 0.0003)
    cache = _MockCache(closes=closes)

    rep = asyncio.run(
        run_sector_rotation_hindcast(
            cache=cache,
            start=date(2024, 6, 1),
            end=date(2024, 12, 1),
            rebalance_days=30,
            horizon_days=30,
        )
    )
    assert rep.expert == "E_SECTOR_ROTATION"
    assert rep.universe_size == 11
    assert rep.n_signals > 0


def test_run_fomc_drift_hindcast_smoke():
    from glostat.experts.e_fomc_drift import FOMC_DATES
    from glostat.experts.e_sector_rotation import BENCHMARK, SECTOR_ETFS

    closes: dict[str, dict[date, float]] = {}
    fomc_2024 = [d for d in FOMC_DATES if d.year == 2024]
    for etf in (BENCHMARK, *SECTOR_ETFS):
        s = _line(date(2024, 1, 1), 800, 100.0, 0.0001)
        # Inject a reaction-day jump on each FOMC day so the runner emits
        # at least one direction signal per ticker.
        for fd in fomc_2024:
            s[fd] = 105.0  # ~5% jump vs prior trading-day close
        closes[etf] = s
    cache = _MockCache(closes=closes)

    rep = asyncio.run(
        run_fomc_drift_hindcast(
            cache=cache, start=date(2024, 1, 1), end=date(2024, 12, 31),
            horizon_days=5,
        )
    )
    assert rep.expert == "E_FOMC_DRIFT"
    assert rep.n_signals > 0


def test_run_pead_hindcast_smoke():
    closes = {"AAPL": _line(date(2024, 1, 1), 400, 200.0, 0.0005)}
    cache = _MockCache(closes=closes)

    class _StubYF:
        async def get_earnings_calendar(self, ticker):
            from glostat.data.yfinance_types import EarningsCalendar, EarningsEvent
            return EarningsCalendar(
                ticker=ticker,
                upcoming=(
                    EarningsEvent(
                        ticker=ticker,
                        earnings_date=datetime(2024, 5, 1, 12, tzinfo=UTC),
                        eps_estimate=1.5, eps_actual=1.8, revenue_estimate=None,
                    ),
                    EarningsEvent(
                        ticker=ticker,
                        earnings_date=datetime(2024, 8, 1, 12, tzinfo=UTC),
                        eps_estimate=1.6, eps_actual=1.0, revenue_estimate=None,
                    ),
                ),
            )

    rep = asyncio.run(
        run_pead_hindcast(
            universe=["AAPL"], yf_client=_StubYF(), cache=cache,
            start=date(2024, 1, 1), end=date(2024, 12, 31),
        )
    )
    assert rep.expert == "E_PEAD"
    assert rep.n_signals == 2  # both events produce non-NEUTRAL signal


def test_run_insider_cluster_hindcast_smoke(monkeypatch):
    base = dict(
        issuer_cik="x", accession="a", filed_at=date(2024, 6, 1),
        reporter_role="Director", code="P",
        shares=100.0, price=10.0, value_usd=1000.0,
    )
    txns = [
        Form4Transaction(
            transaction_date=date(2024, 6, 10),
            reporter_name=f"R{i}", reporter_cik=str(i),
            **base,
        )
        for i in range(1, 5)  # 4 unique buyers in same week → cluster
    ]

    closes = {"TEST": _line(date(2024, 1, 1), 400, 50.0, 0.001)}
    cache = _MockCache(closes=closes)

    async def fake_warm(self, ticker, cik, days_back=760):
        self._txn_cache[ticker.upper()] = txns
        return len(txns)

    monkeypatch.setattr(EInsiderClusterExpert, "warm_cache", fake_warm)

    rep = asyncio.run(
        run_insider_cluster_hindcast(
            universe_with_cik=[("TEST", "0001")],
            sec_client=None,  # not used after monkeypatch
            cache=cache,
            start=date(2024, 1, 1), end=date(2024, 12, 31),
        )
    )
    assert rep.expert == "E_INSIDER_CLUSTER"
    assert rep.n_signals > 0
