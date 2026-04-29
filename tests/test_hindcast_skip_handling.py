from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from glostat.cli import _load_market_meta
from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.yfinance_client import YFinanceClient
from glostat.replay.live_hindcast import (
    LiveActualReturnFetcher,
    LiveHindcastVerdictBuilder,
)
from glostat.replay.live_hindcast_network import (
    render_network_summary,
    summarize_network,
)

# Sprint 4 PR #3 — hindcast must distinguish "no verdict (all experts skipped)"
# from "verdict built from surviving experts" so Sharpe / AUC denominators are
# the trades that actually happened, not the misses + fake neutrals.

_DAY: date = date(2026, 2, 17)
_NOW: datetime = datetime(2026, 2, 17, 12, 0, tzinfo=UTC)


def _market_meta() -> Any:
    return _load_market_meta("XNAS")


def _make_signal(name: str, *, direction: str = "LONG", net: float = 1.5) -> ExpertSignal:
    return ExpertSignal(
        expert_name=name,  # type: ignore[arg-type]
        ticker="AAPL",
        direction=direction,  # type: ignore[arg-type]
        net_score=net,
        confidence=0.6,
        archetype="continuation",
        basis="test",
        sources=("test#abc",),
        expires_at=_NOW + timedelta(days=30),
        metadata=(("test", "true"),),
    )


class _StubExpert:
    def __init__(self, name: str, behaviour: str, signal: ExpertSignal | None = None) -> None:
        self.name = name
        self._behaviour = behaviour
        self._signal = signal

    async def compute(self, _ticker: str, _ts: datetime) -> ExpertSignal:
        if self._behaviour == "skip":
            raise ExpertSkipError(f"{self.name}: stubbed skip")
        if self._behaviour == "raise":
            raise RuntimeError(f"{self.name}: stubbed runtime")
        assert self._signal is not None
        return self._signal


def _patch_experts(
    monkeypatch: pytest.MonkeyPatch, sequence: tuple[_StubExpert, ...]
) -> None:
    iterator = iter(sequence)

    def _fund(*_a: Any, **_kw: Any) -> _StubExpert:
        return next(iterator)

    def _time(*_a: Any, **_kw: Any) -> _StubExpert:
        return next(iterator)

    def _flow(*_a: Any, **_kw: Any) -> _StubExpert:
        return next(iterator)

    monkeypatch.setattr("glostat.replay.live_hindcast.EFundamentalExpert", _fund)
    monkeypatch.setattr("glostat.replay.live_hindcast.ETimeExpert", _time)
    monkeypatch.setattr("glostat.replay.live_hindcast.EFundFlowExpert", _flow)


def _builder() -> LiveHindcastVerdictBuilder:
    fake_yf = MagicMock(spec=YFinanceClient)
    fake_yf.throttle = MagicMock(acquire_count=0, throttled_count=0)
    fake_yf.retry_stats = MagicMock(
        retry_count=0, retry_429_count=0, retry_5xx_count=0,
        retry_empty_count=0, retry_timeout_count=0,
    )
    fake_sec = MagicMock()
    fake_sec.throttle = MagicMock(acquire_count=0, throttled_count=0)
    fake_sec.retry_stats = MagicMock(
        retry_count=0, retry_429_count=0, retry_5xx_count=0,
        retry_empty_count=0, retry_timeout_count=0,
    )
    return LiveHindcastVerdictBuilder(
        market_meta=_market_meta(),
        horizon_days=30,
        yf_client=fake_yf,
        sec_client=fake_sec,
        router=MagicMock(),
    )


def test_hindcast_drops_when_all_experts_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_experts(monkeypatch, (
        _StubExpert("E_FUNDAMENTAL", "skip"),
        _StubExpert("E_TIME", "skip"),
        _StubExpert("E_FUND_FLOW", "skip"),
    ))
    builder = _builder()
    verdict = asyncio.run(builder.build("AAPL", _DAY))
    assert verdict is None
    assert builder.skipped_count == 1
    assert builder.build_count == 0
    assert builder.expert_skip_breakdown == {
        "E_FUNDAMENTAL": 1, "E_TIME": 1, "E_FUND_FLOW": 1,
    }


def test_hindcast_partial_when_some_experts_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1 expert skips, 2 emit → verdict should still build (verdict_builder accepts ≥1 signal).
    _patch_experts(monkeypatch, (
        _StubExpert("E_FUNDAMENTAL", "ok", signal=_make_signal("E_FUNDAMENTAL")),
        _StubExpert("E_TIME", "skip"),
        _StubExpert("E_FUND_FLOW", "ok", signal=_make_signal("E_FUND_FLOW", net=1.2)),
    ))
    builder = _builder()
    verdict = asyncio.run(builder.build("AAPL", _DAY))
    assert verdict is not None
    assert builder.skipped_count == 0
    assert builder.build_count == 1
    assert builder.expert_skip_breakdown == {"E_TIME": 1}
    # Verdict must reflect only the surviving signals.
    contributing_names = {s.expert_name for s in verdict.contributing_signals}
    assert contributing_names == {"E_FUNDAMENTAL", "E_FUND_FLOW"}


def test_metric_denominator_excludes_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    # Skipped verdicts must not increment build_count, so the harness sees only
    # the trades that actually happened.
    _patch_experts(monkeypatch, (
        _StubExpert("E_FUNDAMENTAL", "skip"),
        _StubExpert("E_TIME", "skip"),
        _StubExpert("E_FUND_FLOW", "skip"),
        _StubExpert("E_FUNDAMENTAL", "ok", signal=_make_signal("E_FUNDAMENTAL")),
        _StubExpert("E_TIME", "ok", signal=_make_signal("E_TIME", net=1.4)),
        _StubExpert("E_FUND_FLOW", "ok", signal=_make_signal("E_FUND_FLOW", net=1.6)),
    ))
    builder = _builder()
    v_skip = asyncio.run(builder.build("XYZ", _DAY))
    v_ok = asyncio.run(builder.build("AAPL", _DAY + timedelta(days=1)))
    assert v_skip is None
    assert v_ok is not None
    assert builder.build_count == 1
    assert builder.skipped_count == 1


def test_network_summary_reports_skip_breakdown(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_experts(monkeypatch, (
        _StubExpert("E_FUNDAMENTAL", "skip"),
        _StubExpert("E_TIME", "ok", signal=_make_signal("E_TIME")),
        _StubExpert("E_FUND_FLOW", "skip"),
    ))
    builder = _builder()
    asyncio.run(builder.build("AAPL", _DAY))
    fetcher = LiveActualReturnFetcher(
        yf_client=builder.yf_client, cache_path=Path("/tmp/glostat_skip_handling.parquet"),
    )
    summary = summarize_network(builder, fetcher)
    assert summary["verdicts_built"] == 1
    assert summary["verdicts_skipped"] == 0  # 1 expert emitted → verdict built
    assert summary["expert_skip_breakdown"] == {"E_FUNDAMENTAL": 1, "E_FUND_FLOW": 1}
    rendered = render_network_summary(summary)
    assert "verdicts_skipped" in rendered
    assert "expert_skip_breakdown" in rendered
    assert "E_FUNDAMENTAL=1" in rendered


def test_failure_path_does_not_increment_skip(monkeypatch: pytest.MonkeyPatch) -> None:
    # A pure RuntimeError must NOT count as a skip; it's a hard failure.
    _patch_experts(monkeypatch, (
        _StubExpert("E_FUNDAMENTAL", "raise"),
        _StubExpert("E_TIME", "raise"),
        _StubExpert("E_FUND_FLOW", "raise"),
    ))
    builder = _builder()
    verdict = asyncio.run(builder.build("AAPL", _DAY))
    assert verdict is None
    # All 3 experts hit the generic except branch → verdict skipped (no signals)
    # but expert_skip_breakdown stays empty (those weren't ExpertSkipErrors).
    assert builder.expert_skip_breakdown == {}
    assert builder.skipped_count == 1
