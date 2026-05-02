from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import DataRouter
from glostat.data.yfinance_types import AnalystRecommendationEvent

# v1.8.0 — Sell-side analyst revision expert (US/global liquid names).
#
# WHY: Stickel 1991 + Womack 1996 + literature post-2010 documents that
# analyst forward-EPS / rating revisions in the past 30-90 days correlate
# with subsequent price moves (typical AUC 0.53-0.56 in liquid universes).
# The "analyst revision drift" is one of the better-documented academic
# anomalies, and yfinance exposes the data for free via Ticker.upgrades_downgrades.
#
# Score formula:
#   net_revisions = sum(+1 if action=up else -1 if action=down else 0)
#                   over the last LOOKBACK_DAYS window
#   raw_score = clip(net_revisions * GAIN_PER_REVISION, ±SCORE_CLIP)
#
# Universe: US (yfinance has reliable analyst data for Russell 2000+) plus KR
# top KOSPI 200 names. Crypto / FX / commodity ETFs skip cleanly.

log: Final = structlog.get_logger(__name__)

_LOOKBACK_DAYS: Final[int] = 60
_GAIN_PER_REVISION: Final[float] = 0.5
_SCORE_CLIP: Final[float] = 2.5
_DIRECTION_THRESHOLD: Final[float] = 0.6
_HORIZON_DAYS: Final[int] = 30

# Common up/down vocabulary in yfinance Action field. Lowercased for match.
_UP_TOKENS: Final[frozenset[str]] = frozenset({"up", "upgrade", "upgraded"})
_DOWN_TOKENS: Final[frozenset[str]] = frozenset({"down", "downgrade", "downgraded"})


@dataclass(frozen=True, slots=True)
class AnalystRevisionScore:
    upgrades: int
    downgrades: int
    other_actions: int
    net_revisions: int
    raw_score: float
    net_score: float

    @property
    def direction(self) -> str:
        if self.net_score > _DIRECTION_THRESHOLD:
            return "LONG"
        if self.net_score < -_DIRECTION_THRESHOLD:
            return "SHORT"
        return "NEUTRAL"

    @property
    def confidence(self) -> float:
        return min(1.0, abs(self.net_score) / _SCORE_CLIP)


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    snapshot_id: str


class EAnalystRevisionExpert:
    """Sell-side analyst rating-revision drift expert.

    Computes net up/down rec changes in the last 60 days; positive net =
    LONG (continuation expected). Skips cleanly when yfinance returns no
    analyst data (most non-equities + thinly-covered names).
    """

    name = "E_ANALYST_REVISION"

    def __init__(self, *, router: DataRouter) -> None:
        self._router = router

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        client, method = self._router.route(self.name, "recommendations")
        sources: list[_Source] = []
        try:
            history = await getattr(client, method)(ticker)
        except Exception as exc:
            raise ExpertSkipError(
                f"E_ANALYST_REVISION: yfinance recommendations failed for "
                f"{ticker}: {exc}"
            ) from exc
        events = tuple(history.events)
        if not events:
            raise ExpertSkipError(
                f"E_ANALYST_REVISION: no analyst recommendations for {ticker}"
            )
        snap_id = getattr(client, "last_snapshot_id", None)
        if snap_id is not None:
            sources.append(_Source(
                name="yfinance.recommendations", snapshot_id=snap_id,
            ))
        score = score_revisions(events, today=ts)
        return _build_signal(
            ticker=ticker.upper(), ts=ts, score=score, sources=sources,
        )


def score_revisions(
    events: Sequence[AnalystRecommendationEvent], *, today: datetime,
) -> AnalystRevisionScore:
    cutoff = today - timedelta(days=_LOOKBACK_DAYS)
    upgrades = 0
    downgrades = 0
    other = 0
    for e in events:
        if e.ts < cutoff:
            continue
        action = (e.action or "").lower()
        if action in _UP_TOKENS:
            upgrades += 1
        elif action in _DOWN_TOKENS:
            downgrades += 1
        else:
            other += 1
    net = upgrades - downgrades
    raw = max(
        -_SCORE_CLIP,
        min(_SCORE_CLIP, net * _GAIN_PER_REVISION),
    )
    return AnalystRevisionScore(
        upgrades=upgrades, downgrades=downgrades, other_actions=other,
        net_revisions=net, raw_score=raw, net_score=raw,
    )


def _build_signal(
    *,
    ticker: str,
    ts: datetime,
    score: AnalystRevisionScore,
    sources: list[_Source],
) -> ExpertSignal:
    basis = (
        f"upgrades={score.upgrades} downgrades={score.downgrades} "
        f"other={score.other_actions} net={score.net_revisions} "
        f"(window={_LOOKBACK_DAYS}d)"
    )
    metadata = tuple(sorted({
        "upgrades": str(score.upgrades),
        "downgrades": str(score.downgrades),
        "other_actions": str(score.other_actions),
        "net_revisions": str(score.net_revisions),
        "raw_score": f"{score.raw_score:.4f}",
        "net_score": f"{score.net_score:.4f}",
        "lookback_days": str(_LOOKBACK_DAYS),
    }.items()))
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_analyst_revision.synthetic",)
    return ExpertSignal(
        expert_name="E_ANALYST_REVISION",  # type: ignore[arg-type]
        ticker=ticker,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="continuation",   # revisions cluster + drift in same direction
        basis=basis,
        sources=source_strings,
        expires_at=ts + timedelta(days=_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "AnalystRevisionScore",
    "EAnalystRevisionExpert",
    "score_revisions",
]
