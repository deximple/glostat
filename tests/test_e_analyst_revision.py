from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from glostat.core.errors import ExpertSkipError
from glostat.data.yfinance_types import (
    AnalystRecommendationEvent,
    AnalystRecommendationHistory,
)
from glostat.experts.e_analyst_revision import (
    AnalystRevisionScore,
    EAnalystRevisionExpert,
    score_revisions,
)

# v1.8.0 — Sell-side analyst revision drift expert tests.


def _ev(*, days_ago: int, action: str, firm: str = "GoldmanSachs") -> AnalystRecommendationEvent:
    return AnalystRecommendationEvent(
        ts=datetime.now(tz=UTC) - timedelta(days=days_ago),
        firm=firm,
        from_grade="Hold",
        to_grade="Buy" if action == "up" else "Sell",
        action=action,
    )


# ── score_revisions ───────────────────────────────────────────────────────


class TestScoreRevisions:
    def test_pure_upgrades_long(self) -> None:
        events = [_ev(days_ago=10, action="up") for _ in range(4)]
        score = score_revisions(events, today=datetime.now(tz=UTC))
        assert score.upgrades == 4
        assert score.downgrades == 0
        assert score.net_revisions == 4
        assert score.direction == "LONG"
        assert score.net_score > 0.6

    def test_pure_downgrades_short(self) -> None:
        events = [_ev(days_ago=10, action="down") for _ in range(3)]
        score = score_revisions(events, today=datetime.now(tz=UTC))
        assert score.downgrades == 3
        assert score.net_revisions == -3
        assert score.direction == "SHORT"
        assert score.net_score < -0.6

    def test_mixed_balanced_neutral(self) -> None:
        events = [
            _ev(days_ago=10, action="up"),
            _ev(days_ago=10, action="down"),
        ]
        score = score_revisions(events, today=datetime.now(tz=UTC))
        assert score.net_revisions == 0
        assert score.direction == "NEUTRAL"

    def test_old_events_outside_window_ignored(self) -> None:
        events = [
            _ev(days_ago=200, action="up"),   # outside 60d window
            _ev(days_ago=10, action="down"),
        ]
        score = score_revisions(events, today=datetime.now(tz=UTC))
        # Only the down counts.
        assert score.upgrades == 0
        assert score.downgrades == 1
        assert score.direction == "NEUTRAL"   # net = -1, score = -0.5 < threshold

    def test_other_action_classified_as_other(self) -> None:
        events = [_ev(days_ago=10, action="reit")]
        score = score_revisions(events, today=datetime.now(tz=UTC))
        assert score.other_actions == 1
        assert score.upgrades == 0
        assert score.downgrades == 0
        assert score.net_revisions == 0

    def test_clip_at_score_clip(self) -> None:
        # 20 upgrades → 10.0 raw, clip to +2.5.
        events = [_ev(days_ago=10, action="up") for _ in range(20)]
        score = score_revisions(events, today=datetime.now(tz=UTC))
        assert score.net_score == pytest.approx(2.5, abs=1e-6)


# ── Expert.compute integration ────────────────────────────────────────────


class _FakeYf:
    last_snapshot_id = "fake-rec-snap"

    def __init__(self, *, history: AnalystRecommendationHistory | None = None,
                 fail: bool = False) -> None:
        self._history = history
        self._fail = fail

    async def get_recommendations(self, ticker: str) -> Any:
        if self._fail:
            raise RuntimeError("fake recs failure")
        return self._history or AnalystRecommendationHistory(
            ticker=ticker, events=(),
        )


class _FakeRouter:
    def __init__(self, client: Any) -> None:
        self._client = client

    def route(self, _expert: str, _method: str) -> tuple[Any, str]:
        return self._client, "get_recommendations"


class TestExpertCompute:
    @pytest.mark.asyncio
    async def test_yf_failure_skips_cleanly(self) -> None:
        expert = EAnalystRevisionExpert(
            router=_FakeRouter(_FakeYf(fail=True)),  # type: ignore[arg-type]
        )
        with pytest.raises(ExpertSkipError, match="recommendations failed"):
            await expert.compute("AAPL", datetime.now(tz=UTC))

    @pytest.mark.asyncio
    async def test_no_events_skips_cleanly(self) -> None:
        expert = EAnalystRevisionExpert(
            router=_FakeRouter(_FakeYf()),  # type: ignore[arg-type]
        )
        with pytest.raises(ExpertSkipError, match="no analyst recommendations"):
            await expert.compute("UNKNOWN", datetime.now(tz=UTC))

    @pytest.mark.asyncio
    async def test_strong_upgrades_long_signal(self) -> None:
        history = AnalystRecommendationHistory(
            ticker="AAPL",
            events=tuple(_ev(days_ago=10, action="up") for _ in range(5)),
        )
        expert = EAnalystRevisionExpert(
            router=_FakeRouter(_FakeYf(history=history)),  # type: ignore[arg-type]
        )
        sig = await expert.compute("AAPL", datetime.now(tz=UTC))
        assert sig.direction == "LONG"
        assert sig.expert_name == "E_ANALYST_REVISION"
        assert sig.net_score > 0.6


class TestAnalystRevisionScore:
    def test_confidence_at_full(self) -> None:
        s = AnalystRevisionScore(
            upgrades=10, downgrades=0, other_actions=0,
            net_revisions=10, raw_score=2.5, net_score=2.5,
        )
        assert s.confidence == 1.0
