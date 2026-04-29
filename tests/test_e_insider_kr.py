from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.dart_types import DartExecutiveTransaction
from glostat.experts.e_insider_kr import (
    EInsiderKrExpert,
    InsiderKrScore,
    cluster_count,
    score_insider_kr,
)


def _make_txn(
    *,
    repror: str = "홍길동", trd_kind: str = "장내매수", bsis_dt: str = "20260101",
    irds_cnt: str = "1000", is_buy: bool = True, is_sell: bool = False,
) -> DartExecutiveTransaction:
    return DartExecutiveTransaction(
        corp_code="00126380", repror=repror,
        isu_exctv_rgist_at="Y", isu_exctv_ofcps="이사",
        isu_main_shrholdr="본인",
        sp_stock_lmp_cnt="100000", sp_stock_lmp_irds_cnt=irds_cnt,
        sp_stock_lmp_irds_rate="0.10",
        bsis_dt=bsis_dt, rcept_dt=bsis_dt,
        trd_kind=trd_kind, is_buy=is_buy, is_sell=is_sell,
    )


# ── cluster_count ─────────────────────────────────────────────────────────


def test_cluster_count_unique_buyers_within_window() -> None:
    txns = [
        _make_txn(repror="A", bsis_dt="20260110"),
        _make_txn(repror="B", bsis_dt="20260112"),
        _make_txn(repror="C", bsis_dt="20260115"),
        _make_txn(repror="A", bsis_dt="20260116"),  # duplicate name → still 1
    ]
    n = cluster_count(txns, window_end=date(2026, 1, 20), window_days=14, side="buy")
    assert n == 3


def test_cluster_count_excludes_outside_window() -> None:
    txns = [
        _make_txn(repror="A", bsis_dt="20251201"),  # 50d before window_end
        _make_txn(repror="B", bsis_dt="20260120"),  # inside
    ]
    n = cluster_count(txns, window_end=date(2026, 1, 25), window_days=14, side="buy")
    assert n == 1


def test_cluster_count_separates_buy_sell() -> None:
    txns = [
        _make_txn(repror="A", trd_kind="장내매수", is_buy=True, is_sell=False,
                   bsis_dt="20260110"),
        _make_txn(repror="B", trd_kind="장내매도", is_buy=False, is_sell=True,
                   bsis_dt="20260111"),
        _make_txn(repror="C", trd_kind="장내매도", is_buy=False, is_sell=True,
                   bsis_dt="20260112"),
    ]
    buys = cluster_count(txns, window_end=date(2026, 1, 15), window_days=14, side="buy")
    sells = cluster_count(txns, window_end=date(2026, 1, 15), window_days=14, side="sell")
    assert buys == 1
    assert sells == 2


def test_cluster_count_handles_invalid_date() -> None:
    txns = [_make_txn(repror="A", bsis_dt="garbage")]
    assert cluster_count(txns, window_end=date(2026, 1, 15), window_days=14) == 0


# ── score_insider_kr ──────────────────────────────────────────────────────


def test_score_long_when_three_or_more_buyers_cluster() -> None:
    txns = [
        _make_txn(repror=f"R{i}", bsis_dt="20260110") for i in range(3)
    ]
    score = score_insider_kr(txns, as_of=date(2026, 1, 15))
    assert score.cluster_buyers == 3
    assert score.direction == "LONG"
    assert score.net_score > 0


def test_score_neutral_when_below_threshold() -> None:
    txns = [_make_txn(repror="A", bsis_dt="20260110")]  # only 1 buyer
    score = score_insider_kr(txns, as_of=date(2026, 1, 15))
    assert score.direction == "NEUTRAL"
    assert score.cluster_buyers == 1
    assert score.net_score == 0.0


def test_score_short_when_three_or_more_sellers() -> None:
    txns = [
        _make_txn(repror=f"R{i}", trd_kind="장내매도", is_buy=False, is_sell=True,
                   bsis_dt="20260110") for i in range(3)
    ]
    score = score_insider_kr(txns, as_of=date(2026, 1, 15))
    assert score.direction == "SHORT"
    assert score.net_score < 0


def test_score_buy_minus_sell_signed() -> None:
    txns = (
        [_make_txn(repror=f"B{i}", bsis_dt="20260110") for i in range(4)]
        + [_make_txn(repror=f"S{i}", trd_kind="장내매도", is_buy=False, is_sell=True,
                      bsis_dt="20260111") for i in range(3)]
    )
    score = score_insider_kr(txns, as_of=date(2026, 1, 15))
    # 4 buyers - 3 sellers = +1 → 0.5 raw → below threshold? threshold=1.0 → NEUTRAL
    # but raw_score should be positive and small.
    assert score.raw_score > 0
    assert score.cluster_buyers == 4
    assert score.cluster_sells == 3


def test_insider_kr_score_confidence_clipped() -> None:
    s = InsiderKrScore(cluster_buyers=10, cluster_sells=0, raw_score=5.0,
                       net_score=3.0, direction="LONG")
    assert s.confidence == pytest.approx(1.0)


# ── EInsiderKrExpert ─────────────────────────────────────────────────────


class _StubDart:
    def __init__(self, txns: tuple[DartExecutiveTransaction, ...]) -> None:
        self._txns = txns
        self.last_snapshot_id = "stubsnap0000000000abcd"
        self.calls: list[Any] = []

    async def get_corp_code(self, code: str) -> str:
        self.calls.append(("corp_code", code))
        return "00126380"

    async def get_executive_transactions(
        self, corp_code: str, *, days_back: int = 180,
    ) -> tuple[DartExecutiveTransaction, ...]:
        self.calls.append(("elestock", corp_code, days_back))
        return self._txns


@pytest.mark.asyncio
async def test_expert_skips_when_dart_not_configured() -> None:
    expert = EInsiderKrExpert(dart_client=None, kospi200=frozenset({"005930"}))
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("005930", datetime.now(tz=UTC))
    assert "DART" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_skips_for_non_kr_ticker() -> None:
    dart = _StubDart(())
    expert = EInsiderKrExpert(
        dart_client=dart,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    # Universe enforcement happens via wrapper; expert skips if not in universe.
    with pytest.raises(ExpertSkipError):
        await expert.compute("999999", datetime.now(tz=UTC))


@pytest.mark.asyncio
async def test_expert_returns_signal_for_cluster() -> None:
    txns = tuple(
        _make_txn(repror=f"R{i}", bsis_dt="20260110") for i in range(4)
    )
    dart = _StubDart(txns)
    expert = EInsiderKrExpert(
        dart_client=dart,  # type: ignore[arg-type]
        kospi200=frozenset({"005930"}),
    )
    sig = await expert.compute("005930", datetime(2026, 1, 15, 12, 0, tzinfo=UTC))
    assert sig.expert_name == "E_INSIDER_KR"
    assert sig.direction == "LONG"
    assert sig.confidence > 0


@pytest.mark.asyncio
async def test_from_env_returns_none_without_key(monkeypatch) -> None:
    monkeypatch.delenv("GLOSTAT_DART_API_KEY", raising=False)
    assert EInsiderKrExpert.from_env() is None


@pytest.mark.asyncio
async def test_from_env_returns_expert_with_key(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_DART_API_KEY", "k1")
    expert = EInsiderKrExpert.from_env(kospi200=frozenset({"005930"}))
    assert expert is not None
    assert expert._kospi200 == frozenset({"005930"})  # type: ignore[attr-defined]
    # cleanup
    if expert._dart is not None:  # type: ignore[attr-defined]
        await expert._dart.aclose()  # type: ignore[attr-defined]
