from __future__ import annotations

import math
from datetime import UTC, date, datetime
from typing import Any

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.dart_client import DartApiError
from glostat.data.dart_types import DartExecutiveTransaction
from glostat.experts.e_insider_velocity_kr import (
    EInsiderVelocityKrExpert,
    InsiderVelocityScore,
    _tx_date,
    _tx_shares,
    _tx_side,
    score_velocity,
)

# v1.7.0 — E_INSIDER_VELOCITY_KR skeleton tests.


def _tx(
    *, bsis_dt: str, shares: int, is_buy: bool = False, is_sell: bool = False,
) -> DartExecutiveTransaction:
    return DartExecutiveTransaction(
        corp_code="00000000",
        repror="홍길동",
        isu_exctv_rgist_at="N",
        isu_exctv_ofcps="대표이사",
        isu_main_shrholdr="N",
        sp_stock_lmp_cnt="100,000",
        sp_stock_lmp_irds_cnt=str(shares),
        sp_stock_lmp_irds_rate="0.01",
        bsis_dt=bsis_dt,
        rcept_dt=bsis_dt,
        trd_kind="장내매수",
        is_buy=is_buy,
        is_sell=is_sell,
    )


# ── helpers ───────────────────────────────────────────────────────────────


class TestTxDate:
    def test_parses_yyyymmdd(self) -> None:
        t = _tx(bsis_dt="20260415", shares=100, is_buy=True)
        assert _tx_date(t) == date(2026, 4, 15)

    def test_invalid_returns_none(self) -> None:
        t = _tx(bsis_dt="not-a-date", shares=100, is_buy=True)
        assert _tx_date(t) is None

    def test_short_returns_none(self) -> None:
        t = _tx(bsis_dt="2026", shares=100, is_buy=True)
        assert _tx_date(t) is None


class TestTxShares:
    def test_parses_with_commas(self) -> None:
        # Build a fresh tx with comma-formatted shares string.
        t2 = DartExecutiveTransaction(
            corp_code="x", repror="x", isu_exctv_rgist_at="N",
            isu_exctv_ofcps="x", isu_main_shrholdr="N",
            sp_stock_lmp_cnt="0",
            sp_stock_lmp_irds_cnt="12,345",
            sp_stock_lmp_irds_rate="0",
            bsis_dt="20260415", rcept_dt="20260415", trd_kind="x",
            is_buy=True,
        )
        assert _tx_shares(t2) == 12345.0

    def test_dash_returns_zero(self) -> None:
        t = DartExecutiveTransaction(
            corp_code="x", repror="x", isu_exctv_rgist_at="N",
            isu_exctv_ofcps="x", isu_main_shrholdr="N",
            sp_stock_lmp_cnt="0", sp_stock_lmp_irds_cnt="-",
            sp_stock_lmp_irds_rate="0", bsis_dt="20260415",
            rcept_dt="20260415", trd_kind="x",
        )
        assert _tx_shares(t) == 0.0


class TestTxSide:
    def test_is_buy_returns_buy(self) -> None:
        t = _tx(bsis_dt="20260415", shares=100, is_buy=True)
        assert _tx_side(t) == "BUY"

    def test_is_sell_returns_sell(self) -> None:
        t = _tx(bsis_dt="20260415", shares=100, is_sell=True)
        assert _tx_side(t) == "SELL"

    def test_neither_returns_other(self) -> None:
        t = _tx(bsis_dt="20260415", shares=100)
        assert _tx_side(t) == "OTHER"


# ── score_velocity ───────────────────────────────────────────────────────


class TestScoreVelocity:
    def test_accelerating_buys_long(self) -> None:
        # Recent 7d: 3 buys of 1000 each = 3000. Prior 7d: 1 buy of 500.
        # Buy velocity = (3000+1)/(500+1) ≈ 6.0; sell velocity = 1.0.
        # net_velocity = log(6) - log(1) ≈ 1.79; raw = 1.79 * 2.0 = 3.58 → clip 2.5.
        today = date(2026, 5, 1)
        txns = [
            _tx(bsis_dt="20260428", shares=1000, is_buy=True),  # recent
            _tx(bsis_dt="20260429", shares=1000, is_buy=True),  # recent
            _tx(bsis_dt="20260430", shares=1000, is_buy=True),  # recent
            _tx(bsis_dt="20260420", shares=500,  is_buy=True),  # prior
        ]
        score = score_velocity(txns, today=today)
        assert score.direction == "LONG"
        assert score.net_score > 1.0   # strong LONG
        assert score.buy_velocity > 2.0

    def test_accelerating_sells_short(self) -> None:
        today = date(2026, 5, 1)
        txns = [
            _tx(bsis_dt="20260428", shares=2000, is_sell=True),
            _tx(bsis_dt="20260430", shares=2000, is_sell=True),
            _tx(bsis_dt="20260420", shares=500,  is_sell=True),  # prior
        ]
        score = score_velocity(txns, today=today)
        assert score.direction == "SHORT"
        assert score.net_score < -0.5

    def test_no_transactions_neutral(self) -> None:
        score = score_velocity([], today=date(2026, 5, 1))
        # buy_vel = 1/1 = 1; sell_vel = 1/1 = 1; net_velocity = 0; raw = 0.
        assert score.net_score == pytest.approx(0.0, abs=1e-6)
        assert score.direction == "NEUTRAL"

    def test_clip_at_score_clip(self) -> None:
        # Massive recent buys, no prior — should clip at +2.5.
        today = date(2026, 5, 1)
        txns = [
            _tx(bsis_dt="20260428", shares=1_000_000, is_buy=True),
            _tx(bsis_dt="20260430", shares=1_000_000, is_buy=True),
        ]
        score = score_velocity(txns, today=today)
        assert -2.5 <= score.net_score <= 2.5
        # Should be at the positive clip boundary.
        assert score.net_score > 2.0


# ── Expert.compute integration ────────────────────────────────────────────


class _FakeDart:
    last_snapshot_id = "fake-velocity-snap"

    def __init__(self, *, txns: list[Any] | None = None,
                 fail_corp: bool = False, fail_txn: bool = False) -> None:
        self._txns = txns or []
        self._fail_corp = fail_corp
        self._fail_txn = fail_txn

    async def get_corp_code(self, code: str) -> str:
        if self._fail_corp:
            raise DartApiError("fake corp_code failure")
        return f"CORP_{code}"

    async def get_executive_transactions(
        self, corp_code: str, *, lookback_days: int = 30,
    ) -> list[Any]:
        if self._fail_txn:
            raise DartApiError("fake txn failure")
        return self._txns


class TestExpertCompute:
    @pytest.mark.asyncio
    async def test_skips_when_dart_unconfigured(self) -> None:
        expert = EInsiderVelocityKrExpert(
            dart_client=None,
            kospi200=frozenset({"096770"}),
        )
        with pytest.raises(ExpertSkipError, match="DART API not configured"):
            await expert.compute("096770", datetime.now(tz=UTC))

    @pytest.mark.asyncio
    async def test_skips_when_not_in_kospi200(self) -> None:
        expert = EInsiderVelocityKrExpert(
            dart_client=_FakeDart(),  # type: ignore[arg-type]
            kospi200=frozenset({"005930"}),
        )
        with pytest.raises(ExpertSkipError, match="not in KOSPI 200"):
            await expert.compute("999999", datetime.now(tz=UTC))

    @pytest.mark.asyncio
    async def test_corp_code_failure_skips_cleanly(self) -> None:
        expert = EInsiderVelocityKrExpert(
            dart_client=_FakeDart(fail_corp=True),  # type: ignore[arg-type]
            kospi200=frozenset({"096770"}),
        )
        with pytest.raises(ExpertSkipError, match="corp_code lookup failed"):
            await expert.compute("096770", datetime.now(tz=UTC))


# ── InsiderVelocityScore dataclass ────────────────────────────────────────


class TestInsiderVelocityScore:
    def test_confidence_normalized(self) -> None:
        s = InsiderVelocityScore(
            buys_recent=100, buys_prior=10,
            sells_recent=0, sells_prior=0,
            buy_velocity=10.0, sell_velocity=1.0,
            net_velocity=math.log(10),
            raw_score=2.0, net_score=2.0,
        )
        # confidence = |2.0| / 2.5 = 0.8
        assert 0.79 < s.confidence < 0.81
