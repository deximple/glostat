from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.ecos_types import EcosObservation, EcosSeries
from glostat.experts.e_macro_kr import (
    EMacroKrExpert,
    MacroKrInputs,
    MacroKrScore,
    _all_inputs_missing,
    _cpi_surprise,
    _relative_change,
    _rolling_change,
    _signed_term,
    score_macro_kr,
)


def _series(stat: str, item: str, cycle: str, values: list[float]) -> EcosSeries:
    obs = tuple(
        EcosObservation(stat, item, f"2026{i+1:02d}", v) for i, v in enumerate(values)
    )
    return EcosSeries(stat_code=stat, item_code=item, cycle=cycle, observations=obs)


# ── pure scoring helpers ────────────────────────────────────────────────


def test_rolling_change_returns_diff() -> None:
    s = _series("722Y001", "0101000", "M", [3.50, 3.25, 3.00, 2.75])
    assert _rolling_change(s, periods=3) == pytest.approx(-0.75)


def test_rolling_change_too_short_returns_none() -> None:
    s = _series("722Y001", "0101000", "M", [3.0, 2.75])
    assert _rolling_change(s, periods=3) is None


def test_rolling_change_none_input() -> None:
    assert _rolling_change(None, periods=3) is None


def test_relative_change_60d() -> None:
    # Build 65 daily KRW observations: 1300 → 1400 over the window.
    vals = [1300 + i for i in range(65)]
    s = _series("731Y001", "0000001", "D", vals)
    rc = _relative_change(s, lookback=60)
    assert rc is not None
    assert rc == pytest.approx((vals[-1] / vals[-1 - 60]) - 1.0)


def test_relative_change_zero_base_returns_none() -> None:
    s = _series("731Y001", "0000001", "D", [0.0, 1.0])
    assert _relative_change(s, lookback=1) is None


def test_cpi_surprise_above_trend() -> None:
    # 12 months at 100, then jump to 105 → 5% above trend.
    s = _series("901Y009", "0", "M", [100.0] * 12 + [105.0])
    surprise = _cpi_surprise(s, trailing_n=12)
    assert surprise == pytest.approx(0.05)


def test_cpi_surprise_too_short_returns_none() -> None:
    s = _series("901Y009", "0", "M", [100.0])
    assert _cpi_surprise(s, trailing_n=12) is None


def test_signed_term_invert_flips_sign() -> None:
    # base rate cut (negative Δ) should produce a POSITIVE contribution after invert=True.
    term = _signed_term(-0.50, scale_pp=0.50, weight=1.0, invert=True)
    assert term > 0
    term_no_invert = _signed_term(-0.50, scale_pp=0.50, weight=1.0, invert=False)
    assert term_no_invert < 0


def test_signed_term_value_none_returns_zero() -> None:
    assert _signed_term(None, scale_pp=0.5, weight=1.0, invert=True) == 0.0


def test_signed_term_clipped_to_band() -> None:
    # Huge value should clip at ±2 stddev → final at ±weight × 2.
    term = _signed_term(10.0, scale_pp=0.05, weight=0.8, invert=False)
    assert term == pytest.approx(0.8 * 2.0)


def test_all_inputs_missing_true() -> None:
    inp = MacroKrInputs(None, None, None, None)
    assert _all_inputs_missing(inp) is True


def test_all_inputs_missing_partial_false() -> None:
    inp = MacroKrInputs(0.5, None, None, None)
    assert _all_inputs_missing(inp) is False


# ── score_macro_kr ──────────────────────────────────────────────────────


def test_score_macro_kr_dovish_bullish() -> None:
    # base rate cut + KRW weakening + CPI on trend + KOSPI up = LONG.
    inp = MacroKrInputs(
        base_rate_change_3m=-0.50,
        krw_usd_trend_60d=0.04,
        cpi_surprise=0.0,
        kospi_momentum_60d=0.05,
    )
    score = score_macro_kr(inp)
    assert score.net_score > 0
    assert score.direction == "LONG"


def test_score_macro_kr_hawkish_bearish() -> None:
    # base rate hike + KRW strengthening + CPI hot + KOSPI down = SHORT.
    inp = MacroKrInputs(
        base_rate_change_3m=0.75,
        krw_usd_trend_60d=-0.03,
        cpi_surprise=0.04,
        kospi_momentum_60d=-0.06,
    )
    score = score_macro_kr(inp)
    assert score.net_score < 0
    assert score.direction == "SHORT"


def test_score_macro_kr_neutral_when_flat() -> None:
    inp = MacroKrInputs(0.0, 0.0, 0.0, 0.0)
    score = score_macro_kr(inp)
    assert score.direction == "NEUTRAL"
    assert score.net_score == pytest.approx(0.0)


def test_score_macro_kr_clipped_at_three() -> None:
    # All terms maxed → raw_score should clip at ±3.
    inp = MacroKrInputs(-5.0, 1.0, -1.0, 1.0)
    score = score_macro_kr(inp)
    assert -3.0 <= score.net_score <= 3.0


def test_macro_kr_score_confidence_range() -> None:
    s = MacroKrScore(0.5, 0.3, 0.2, 0.1, 1.1, 1.1)
    assert 0.0 <= s.confidence <= 1.0


# ── EMacroKrExpert ──────────────────────────────────────────────────────


class _StubEcos:
    def __init__(self, *, base, krw_usd, cpi, kospi) -> None:
        self._series = {
            "base": base, "krw_usd": krw_usd, "cpi": cpi, "kospi": kospi,
        }
        self.last_snapshot_id = "stubsnap0000000000"
        self.calls: list[tuple[str, Any, Any]] = []

    async def get_base_rate(self, start, end):
        self.calls.append(("base", start, end))
        return self._series["base"]

    async def get_krw_usd(self, start, end):
        self.calls.append(("krw_usd", start, end))
        return self._series["krw_usd"]

    async def get_cpi(self, start, end):
        self.calls.append(("cpi", start, end))
        return self._series["cpi"]

    async def get_fx_reserves(self, start, end):
        return _series("732Y001", "99", "M", [])

    async def get_kospi_index(self, start, end):
        self.calls.append(("kospi", start, end))
        return self._series["kospi"]

    async def aclose(self) -> None:
        return None


@pytest.mark.asyncio
async def test_expert_skips_when_ecos_not_configured() -> None:
    expert = EMacroKrExpert(ecos_client=None)
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("005930", datetime.now(tz=UTC))
    assert "ECOS" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_skips_for_non_kr_ticker() -> None:
    ecos = _StubEcos(
        base=_series("722Y001", "0101000", "M", [3.0, 2.75, 2.50, 2.25]),
        krw_usd=_series("731Y001", "0000001", "D", [1300] * 65),
        cpi=_series("901Y009", "0", "M", [100] * 13),
        kospi=_series("802Y001", "0001000", "D", [4000] * 65),
    )
    expert = EMacroKrExpert(ecos_client=ecos)  # type: ignore[arg-type]
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("AAPL", datetime.now(tz=UTC))
    assert "not KR equity" in str(exc.value)


@pytest.mark.asyncio
async def test_expert_returns_signal_for_kr_ticker_dovish() -> None:
    # Cuts + weakening KRW + on-trend CPI + KOSPI rally → LONG.
    ecos = _StubEcos(
        base=_series("722Y001", "0101000", "M", [3.50, 3.25, 3.00, 2.75]),
        krw_usd=_series("731Y001", "0000001", "D", [1300 + i * 2 for i in range(65)]),
        cpi=_series("901Y009", "0", "M", [100.0] * 13),
        kospi=_series("802Y001", "0001000", "D", [3500 + i * 5 for i in range(65)]),
    )
    expert = EMacroKrExpert(ecos_client=ecos)  # type: ignore[arg-type]
    sig = await expert.compute("005930", datetime(2026, 4, 1, tzinfo=UTC))
    assert sig.expert_name == "E_MACRO_KR"
    assert sig.direction == "LONG"
    assert sig.confidence > 0
    assert sig.net_score > 0


@pytest.mark.asyncio
async def test_expert_skips_when_all_inputs_missing() -> None:
    empty = _series("x", "y", "M", [])
    ecos = _StubEcos(base=empty, krw_usd=empty, cpi=empty, kospi=empty)
    expert = EMacroKrExpert(ecos_client=ecos)  # type: ignore[arg-type]
    with pytest.raises(ExpertSkipError) as exc:
        await expert.compute("005930", datetime(2026, 4, 1, tzinfo=UTC))
    assert "no usable ECOS series" in str(exc.value)


@pytest.mark.asyncio
async def test_from_env_returns_none_without_key(monkeypatch) -> None:
    monkeypatch.delenv("GLOSTAT_ECOS_API_KEY", raising=False)
    assert EMacroKrExpert.from_env() is None


@pytest.mark.asyncio
async def test_from_env_returns_expert_with_key(monkeypatch) -> None:
    monkeypatch.setenv("GLOSTAT_ECOS_API_KEY", "k1")
    expert = EMacroKrExpert.from_env()
    assert expert is not None
    if expert._ecos is not None:  # type: ignore[attr-defined]
        await expert._ecos.aclose()  # type: ignore[attr-defined]


# ── thesis_wrapper integration (universe gating) ────────────────────────


@pytest.mark.asyncio
async def test_wrap_macro_kr_skips_us_ticker() -> None:
    from glostat.predictor.calibration import synthetic_calibration_for_mock  # noqa: PLC0415
    from glostat.predictor.thesis_wrappers import wrap_macro_kr  # noqa: PLC0415

    cal = synthetic_calibration_for_mock()

    # Expert is irrelevant — wrapper rejects on universe before calling it.
    contrib = await wrap_macro_kr(object(), "AAPL", datetime.now(tz=UTC), cal)
    assert contrib.direction == "skip"
    assert "not KR equity" in (contrib.skip_reason or "")


@pytest.mark.asyncio
async def test_wrap_macro_kr_kosdaq_accepted() -> None:
    # KOSDAQ tickers are also KR (universe = any KR ticker for macro).
    from glostat.predictor.calibration import synthetic_calibration_for_mock  # noqa: PLC0415
    from glostat.predictor.thesis_wrappers import wrap_macro_kr  # noqa: PLC0415

    cal = synthetic_calibration_for_mock()
    ecos = _StubEcos(
        base=_series("722Y001", "0101000", "M", [3.0, 2.75, 2.5, 2.25]),
        krw_usd=_series("731Y001", "0000001", "D", [1300] * 65),
        cpi=_series("901Y009", "0", "M", [100] * 13),
        kospi=_series("802Y001", "0001000", "D", [4000] * 65),
    )
    expert = EMacroKrExpert(ecos_client=ecos)  # type: ignore[arg-type]
    contrib = await wrap_macro_kr(
        expert, "035720", datetime(2026, 4, 1, tzinfo=UTC), cal,
    )
    # 035720 (Kakao) is KOSPI but the universe gate is "any KR ticker", so accepted.
    assert contrib.direction != "skip"
    assert contrib.value is not None
