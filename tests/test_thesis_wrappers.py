from __future__ import annotations

from datetime import UTC, datetime

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.predictor.calibration import (
    CalibrationTable,
    synthetic_calibration_for_mock,
)
from glostat.predictor.thesis_wrappers import (
    collect_contributions,
    wrap_commodity_ts_static,
    wrap_fomc_drift_static,
    wrap_foreign_reversal_static,
    wrap_fund_flow,
    wrap_fundamental,
    wrap_funding_carry_static,
    wrap_fx_carry_static,
    wrap_insider_cluster_static,
    wrap_pead_static,
    wrap_sector_rotation_static,
    wrap_time,
)

# ── stub experts ──────────────────────────────────────────────────────────


class _StubExpertOk:
    def __init__(self, *, name: str, score: float, direction: str) -> None:
        self._name = name
        self._score = score
        self._direction = direction

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        return ExpertSignal(
            expert_name=self._name,  # type: ignore[arg-type]
            ticker=ticker.upper(),
            direction=self._direction,  # type: ignore[arg-type]
            net_score=self._score,
            confidence=0.7,
            archetype="continuation",
            basis="stub",
            sources=("snap_a", "snap_b"),
            expires_at=ts,
        )


class _StubExpertSkip:
    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        raise ExpertSkipError(f"stub skip for {ticker}")


class _StubExpertCrash:
    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        raise RuntimeError("boom")


# ── live-expert wrappers ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wrap_fundamental_translates_long_to_up() -> None:
    cal = synthetic_calibration_for_mock()
    expert = _StubExpertOk(name="E_FUNDAMENTAL", score=2.5, direction="LONG")
    result = await wrap_fundamental(
        expert, "AAPL", datetime.now(tz=UTC), cal,
    )
    assert result.direction == "up"
    assert result.value == pytest.approx(2.5)
    assert result.calibration_auc == cal.entries["E_FUNDAMENTAL"].auc


@pytest.mark.asyncio
async def test_wrap_fundamental_translates_short_to_down() -> None:
    cal = synthetic_calibration_for_mock()
    expert = _StubExpertOk(name="E_FUNDAMENTAL", score=-1.5, direction="SHORT")
    result = await wrap_fundamental(expert, "MSFT", datetime.now(tz=UTC), cal)
    assert result.direction == "down"


@pytest.mark.asyncio
async def test_wrap_time_translates_neutral() -> None:
    cal = synthetic_calibration_for_mock()
    expert = _StubExpertOk(name="E_TIME", score=0.0, direction="NEUTRAL")
    result = await wrap_time(expert, "AAPL", datetime.now(tz=UTC), cal)
    assert result.direction == "neutral"


@pytest.mark.asyncio
async def test_wrap_handles_expert_skip_error_as_skip() -> None:
    cal = synthetic_calibration_for_mock()
    result = await wrap_fund_flow(
        _StubExpertSkip(), "AAPL", datetime.now(tz=UTC), cal,
    )
    assert result.direction == "skip"
    assert result.skip_reason is not None
    assert "skip for AAPL" in result.skip_reason


@pytest.mark.asyncio
async def test_wrap_handles_unexpected_exception_as_skip() -> None:
    cal = synthetic_calibration_for_mock()
    result = await wrap_fundamental(
        _StubExpertCrash(), "AAPL", datetime.now(tz=UTC), cal,
    )
    assert result.direction == "skip"
    assert "boom" in (result.skip_reason or "")


@pytest.mark.asyncio
async def test_wrap_fundamental_skips_kr_ticker() -> None:
    cal = synthetic_calibration_for_mock()
    expert = _StubExpertOk(name="E_FUNDAMENTAL", score=1.0, direction="LONG")
    result = await wrap_fundamental(expert, "005930", datetime.now(tz=UTC), cal)
    assert result.direction == "skip"
    assert "not US equity" in (result.skip_reason or "")


@pytest.mark.asyncio
async def test_wrap_fundamental_skips_crypto_ticker() -> None:
    cal = synthetic_calibration_for_mock()
    expert = _StubExpertOk(name="E_FUNDAMENTAL", score=1.0, direction="LONG")
    result = await wrap_fundamental(expert, "BTCUSDT", datetime.now(tz=UTC), cal)
    assert result.direction == "skip"


# ── static wrappers ──────────────────────────────────────────────────────


def test_sector_rotation_skips_individual_stock() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_sector_rotation_static("AAPL", cal)
    assert r.direction == "skip"
    assert "SPDR sector ETF universe" in (r.skip_reason or "")


def test_sector_rotation_emits_neutral_for_xlf() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_sector_rotation_static("XLF", cal)
    assert r.direction == "neutral"
    assert r.value == pytest.approx(0.0)


def test_pead_skips_when_no_event() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_pead_static("AAPL", cal)
    assert r.direction == "skip"
    assert "no earnings event" in (r.skip_reason or "")


def test_pead_skips_when_not_in_universe() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_pead_static("ZZZZ", cal, in_universe=False)
    assert r.direction == "skip"
    assert "not in S&P 500 PEAD universe" in (r.skip_reason or "")


def test_fomc_drift_skips_individual_stock() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_fomc_drift_static("AAPL", cal)
    assert r.direction == "skip"


def test_fomc_drift_no_event_skip_for_spy() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_fomc_drift_static("SPY", cal)
    assert r.direction == "skip"
    assert "FOMC event" in (r.skip_reason or "")


def test_insider_cluster_skips_etfs() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_insider_cluster_static("XLF", cal)
    assert r.direction == "skip"
    assert "ETF" in (r.skip_reason or "")


def test_commodity_ts_skips_individual_stock() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_commodity_ts_static("AAPL", cal)
    assert r.direction == "skip"


def test_commodity_ts_neutral_for_gld() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_commodity_ts_static("GLD", cal)
    assert r.direction == "neutral"


def test_fx_carry_skips_individual_stock() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_fx_carry_static("AAPL", cal)
    assert r.direction == "skip"


def test_fx_carry_neutral_for_xlu() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_fx_carry_static("XLU", cal)
    assert r.direction == "neutral"


def test_funding_carry_skips_us_equity() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_funding_carry_static("AAPL", cal)
    assert r.direction == "skip"
    assert "crypto" in (r.skip_reason or "")


def test_funding_carry_neutral_for_btcusdt() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_funding_carry_static("BTCUSDT", cal)
    assert r.direction == "neutral"


def test_foreign_reversal_skips_us_equity() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_foreign_reversal_static("AAPL", cal)
    assert r.direction == "skip"
    assert "KOSPI" in (r.skip_reason or "")


def test_foreign_reversal_neutral_for_kr_ticker() -> None:
    cal = synthetic_calibration_for_mock()
    r = wrap_foreign_reversal_static("005930", cal)
    assert r.direction == "neutral"


# ── orchestrator collect_contributions ────────────────────────────────────


@pytest.mark.asyncio
async def test_collect_contributions_returns_eighteen_for_us_ticker() -> None:
    # v1.5 P6: 18 contributions = 16 prior + E_FUNDAMENTAL_KR_CYCLICAL slot
    # + E_COMMODITY_INDEX_KR slot (KR-only, skip for US).
    cal = synthetic_calibration_for_mock()
    fund = _StubExpertOk(name="E_FUNDAMENTAL", score=2.0, direction="LONG")
    time_e = _StubExpertOk(name="E_TIME", score=1.0, direction="LONG")
    ff = _StubExpertSkip()
    contribs = await collect_contributions(
        ticker="AAPL", ts=datetime.now(tz=UTC), cal_table=cal,
        fundamental_expert=fund, time_expert=time_e, fund_flow_expert=ff,
    )
    assert len(contribs) == 18
    names = {c.name for c in contribs}
    assert "E_FUNDAMENTAL" in names
    assert "E_FUNDAMENTAL_KR" in names
    assert "E_FOREIGN_REVERSAL" in names
    assert "E_INSIDER_KR" in names
    assert "E_MACRO_KR" in names
    assert "E_SHORT_SELLING_KR" in names
    assert "E_FUNDAMENTAL_KR_CYCLICAL" in names
    assert "E_COMMODITY_INDEX_KR" in names
    assert "E_INTRADAY_FLOW_KR" in names


@pytest.mark.asyncio
async def test_collect_contributions_marks_us_appropriate_skips() -> None:
    cal = synthetic_calibration_for_mock()
    fund = _StubExpertOk(name="E_FUNDAMENTAL", score=2.0, direction="LONG")
    time_e = _StubExpertOk(name="E_TIME", score=1.0, direction="LONG")
    ff = _StubExpertSkip()
    contribs = await collect_contributions(
        ticker="AAPL", ts=datetime.now(tz=UTC), cal_table=cal,
        fundamental_expert=fund, time_expert=time_e, fund_flow_expert=ff,
    )
    by_name = {c.name: c for c in contribs}
    # AAPL is US equity → fundamental + time fire; sector rotation skip; foreign skip.
    assert by_name["E_FUNDAMENTAL"].direction == "up"
    assert by_name["E_TIME"].direction == "up"
    assert by_name["E_SECTOR_ROTATION"].direction == "skip"
    assert by_name["E_FOREIGN_REVERSAL"].direction == "skip"
    assert by_name["E_FUNDING_CARRY"].direction == "skip"


@pytest.mark.asyncio
async def test_collect_contributions_unwired_experts_skip_cleanly() -> None:
    cal = synthetic_calibration_for_mock()
    contribs = await collect_contributions(
        ticker="AAPL", ts=datetime.now(tz=UTC), cal_table=cal,
        fundamental_expert=None, time_expert=None, fund_flow_expert=None,
    )
    by_name = {c.name: c for c in contribs}
    assert by_name["E_FUNDAMENTAL"].direction == "skip"
    assert by_name["E_FUNDAMENTAL"].skip_reason == "expert not wired"


@pytest.mark.asyncio
async def test_collect_contributions_bare_calibration_uses_random_default() -> None:
    cal = CalibrationTable()  # no entries
    fund = _StubExpertOk(name="E_FUNDAMENTAL", score=2.0, direction="LONG")
    contribs = await collect_contributions(
        ticker="AAPL", ts=datetime.now(tz=UTC), cal_table=cal,
        fundamental_expert=fund, time_expert=None, fund_flow_expert=None,
    )
    fund_contrib = next(c for c in contribs if c.name == "E_FUNDAMENTAL")
    # Calibration falls back to AUC=0.5 (random)
    assert fund_contrib.calibration_auc == pytest.approx(0.5)
