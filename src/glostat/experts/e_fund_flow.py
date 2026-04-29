from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import DataRouter
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.data.yfinance_types import HoldersSnapshot

# E_FUND_FLOW v2 — Sprint 5 PR #1 redesign.
# Issuer-CIK 13F is dropped entirely (issuers don't file 13F; PR #3 diagnosed this
# was the dominant cause of the 87% INSUFFICIENT skip rate). Sole signal is the
# top-N institutional_holders snapshot delta:
#   current snapshot vs prior snapshot (from SnapshotBroker if present)
#   per-holder Δshares aggregated across the union of holders
#   classify:
#     NET_BUY    aggregate Δ > 0 AND ≥ 5 holders increasing → +1.5
#     NET_SELL   aggregate Δ < 0 AND ≥ 5 holders decreasing → -1.5
#     MIXED      otherwise (still a valid neutral signal) → 0.0
#     INSUFFICIENT  < 3 distinct holders OR no prior snapshot → ExpertSkipError

log: Final = structlog.get_logger(__name__)

_SCORE_CLIP: Final[float] = 3.0
_DIRECTION_THRESHOLD: Final[float] = 1.0
_SWING_HORIZON_DAYS: Final[int] = 30
_MIN_HOLDERS: Final[int] = 3
_MIN_AGREEING_HOLDERS: Final[int] = 5
# Sprint 5 PR #1: prior snapshot must precede the current verdict day by at
# least this many calendar days. Anything tighter risks treating the current
# snapshot (or a same-day re-fetch) as "prior" and emitting a fake delta.
_PRIOR_MIN_GAP_DAYS: Final[int] = 1

_PATTERN_SCORE: Final[dict[str, float]] = {
    "NET_BUY":      1.5,
    "NET_SELL":    -1.5,
    "MIXED":        0.0,
    "INSUFFICIENT": 0.0,
    # Back-compat: legacy 13F patterns keep their score table entries so older
    # consumers (tests, dashboards) still resolve to a number when reading the map.
    "REVERSAL_BUY":   2.0,
    "ACCUMULATING":   1.5,
    "DISTRIBUTION":  -1.5,
    "REVERSAL_SELL": -1.0,
}

_PATTERN_ARCHETYPE: Final[dict[str, str]] = {
    "NET_BUY":      "continuation",
    "NET_SELL":     "continuation",
    "MIXED":        "mixed",
    "INSUFFICIENT": "mixed",
    "REVERSAL_BUY":   "contrarian",
    "REVERSAL_SELL":  "contrarian",
    "ACCUMULATING":   "continuation",
    "DISTRIBUTION":   "continuation",
}


@dataclass(frozen=True, slots=True)
class FundFlowScore:
    pattern: str
    quarter_directions: tuple[str, ...]   # holder-level: ("in", "in", "out", ...)
    pattern_score: float
    option_proxy: float                   # always 0 in v2 (option flow not in MVP)
    net_score: float
    top_holder: str
    top_holder_pct: float
    raw_score: float = 0.0
    holders_increasing: int = 0
    holders_decreasing: int = 0
    aggregate_delta_shares: int = 0

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

    @property
    def archetype(self) -> str:
        return _PATTERN_ARCHETYPE.get(self.pattern, "mixed")


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    snapshot_id: str
    ts: datetime


class EFundFlowExpert:
    name = "E_FUND_FLOW"

    def __init__(self, *, router: DataRouter) -> None:
        self._router = router

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        ticker = ticker.upper().strip()
        sources: list[_Source] = []
        holders = await self._fetch_holders(ticker, sources)
        if holders is None or len(holders.rows) < _MIN_HOLDERS:
            n = 0 if holders is None else len(holders.rows)
            raise ExpertSkipError(
                f"E_FUND_FLOW: INSUFFICIENT holders ({n} < {_MIN_HOLDERS}) "
                f"for {ticker}@{ts.date().isoformat()}"
            )
        # WHY: cross-day delta requires a prior snapshot at least
        # _PRIOR_MIN_GAP_DAYS calendar days older than the verdict day. Anchor
        # to the day boundary so a same-day mock fixture is never treated as
        # "prior" and emits a fake-zero delta.
        day_start = datetime(ts.year, ts.month, ts.day, tzinfo=ts.tzinfo or UTC)
        cutoff = day_start - timedelta(days=_PRIOR_MIN_GAP_DAYS)
        prior = self._load_prior_holders(ticker, cutoff)
        score = self._score(holders, prior)
        if score.pattern == "INSUFFICIENT":
            raise ExpertSkipError(
                f"E_FUND_FLOW: INSUFFICIENT prior snapshot for "
                f"{ticker}@{ts.date().isoformat()}"
            )
        return _build_signal(ticker=ticker, ts=ts, score=score, sources=sources)

    async def _fetch_holders(
        self, ticker: str, sources: list[_Source]
    ) -> HoldersSnapshot | None:
        try:
            client, method = self._router.route(self.name, "institutional_holders")
        except Exception as exc:
            log.warning("e_fund_flow.holders_route_failed", err=str(exc))
            return None
        try:
            result: HoldersSnapshot = await getattr(client, method)(
                ticker, kind="institutional"
            )
        except Exception as exc:
            log.warning("e_fund_flow.holders_fetch_failed", ticker=ticker, err=str(exc))
            return None
        snap_id = getattr(client, "last_snapshot_id", None)
        if snap_id is not None:
            sources.append(
                _Source(
                    name="yfinance.holders",
                    snapshot_id=snap_id,
                    ts=datetime.now(tz=UTC),
                )
            )
        return result

    def _load_prior_holders(
        self, ticker: str, ts: datetime
    ) -> HoldersSnapshot | None:
        # WHY: cross-day delta requires a prior holders snapshot. The yfinance
        # client persists every fetch into the broker via SnapshotBroker (INV-GS-022),
        # so look back for the most recent snapshot whose ts < current ts.
        try:
            client, _ = self._router.route(self.name, "institutional_holders")
        except Exception:
            return None
        broker: SnapshotBroker | None = getattr(client, "_broker", None)
        if broker is None:
            return None
        uaid = f"XNAS.{ticker.upper()}"
        try:
            records = list(broker.list_snapshots(uaid=uaid, edge_type="holders.institutional"))
        except Exception as exc:
            log.warning("e_fund_flow.prior_lookup_failed", ticker=ticker, err=str(exc))
            return None
        prior = _pick_most_recent_before(records, ts)
        if prior is None:
            return None
        try:
            payload = broker.read_snapshot(prior)
        except Exception as exc:
            log.warning("e_fund_flow.prior_read_failed", leaf=prior, err=str(exc))
            return None
        return _payload_to_snapshot(payload, ticker)

    def _score(
        self,
        holders: HoldersSnapshot,
        prior: HoldersSnapshot | None,
    ) -> FundFlowScore:
        directions, agg_delta, increasing, decreasing = _holder_deltas(holders, prior)
        pattern = _classify_pattern_v2(prior, increasing, decreasing, agg_delta)
        pattern_score = _PATTERN_SCORE.get(pattern, 0.0)
        raw = pattern_score
        net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
        top_holder, top_pct = _top_holder(holders)
        log.debug(
            "e_fund_flow.score",
            pattern=pattern, agg_delta=agg_delta,
            inc=increasing, dec=decreasing, score=net,
            top_holder=top_holder, top_pct=top_pct,
        )
        return FundFlowScore(
            pattern=pattern,
            quarter_directions=tuple(directions),
            pattern_score=pattern_score,
            option_proxy=0.0,
            net_score=net,
            top_holder=top_holder,
            top_holder_pct=top_pct,
            raw_score=raw,
            holders_increasing=increasing,
            holders_decreasing=decreasing,
            aggregate_delta_shares=agg_delta,
        )


def _holder_deltas(
    current: HoldersSnapshot,
    prior: HoldersSnapshot | None,
) -> tuple[list[str], int, int, int]:
    if prior is None:
        return ([], 0, 0, 0)
    prior_by_name = {name: shares for (name, _pct, shares, _ts) in prior.rows}
    directions: list[str] = []
    agg = 0
    inc = 0
    dec = 0
    for (name, _pct, shares, _ts) in current.rows:
        prior_shares = int(prior_by_name.get(name, 0))
        delta = int(shares) - prior_shares
        agg += delta
        if delta > 0:
            directions.append("in")
            inc += 1
        elif delta < 0:
            directions.append("out")
            dec += 1
        else:
            directions.append("flat")
    return (directions, agg, inc, dec)


def _classify_pattern_v2(
    prior: HoldersSnapshot | None,
    increasing: int,
    decreasing: int,
    agg_delta: int,
) -> str:
    if prior is None:
        return "INSUFFICIENT"
    if agg_delta > 0 and increasing >= _MIN_AGREEING_HOLDERS:
        return "NET_BUY"
    if agg_delta < 0 and decreasing >= _MIN_AGREEING_HOLDERS:
        return "NET_SELL"
    return "MIXED"


def _pick_most_recent_before(records: list[Any], ts: datetime) -> str | None:
    best_ts: datetime | None = None
    best_leaf: str | None = None
    for rec in records:
        rec_ts = getattr(rec.leaf.key, "ts_utc", None)
        if rec_ts is None:
            continue
        if rec_ts >= ts:
            continue
        if best_ts is None or rec_ts > best_ts:
            best_ts = rec_ts
            best_leaf = rec.leaf.leaf_hash
    return best_leaf


def _payload_to_snapshot(payload: dict, ticker: str) -> HoldersSnapshot | None:
    try:
        rows = tuple(
            (
                str(h["name"]),
                float(h.get("pct_held", 0.0)),
                int(h.get("shares", 0) or 0),
                str(h.get("date_reported", "") or ""),
            )
            for h in payload.get("holders", [])
        )
    except (KeyError, TypeError, ValueError):
        return None
    holders = tuple((name, pct) for (name, pct, _s, _t) in rows)
    fetched_raw = payload.get("fetched_at")
    if isinstance(fetched_raw, str):
        try:
            fetched = datetime.fromisoformat(fetched_raw)
        except ValueError:
            fetched = datetime.now(tz=UTC)
    else:
        fetched = datetime.now(tz=UTC)
    return HoldersSnapshot(
        ticker=ticker.upper(),
        kind="institutional",
        holders=holders,
        fetched_at=fetched,
        rows=rows,
    )


def _top_holder(holders: HoldersSnapshot | None) -> tuple[str, float]:
    if holders is None or not holders.holders:
        return ("n/a", 0.0)
    name, pct = holders.holders[0]
    return (name, pct)


def _build_signal(
    *,
    ticker: str,
    ts: datetime,
    score: FundFlowScore,
    sources: list[_Source],
) -> ExpertSignal:
    direction_word = {
        "NET_BUY":      "net institutional buying",
        "NET_SELL":     "net institutional selling",
        "MIXED":        "mixed institutional flow",
        "INSUFFICIENT": "insufficient prior snapshot",
        "REVERSAL_BUY": "selling then BUY",
        "ACCUMULATING": "buying",
        "DISTRIBUTION": "selling",
        "REVERSAL_SELL": "buying then SELL",
    }.get(score.pattern, "unknown")
    basis = (
        f"{score.pattern}: Δshares={score.aggregate_delta_shares:+d} "
        f"({score.holders_increasing} up / {score.holders_decreasing} down) "
        f"of {direction_word}, "
        f"top holder {score.top_holder} {score.top_holder_pct:+.2%} of float"
    )
    metadata: tuple[tuple[str, str], ...] = tuple(
        sorted(
            {
                "pattern": score.pattern,
                "holders_increasing": str(score.holders_increasing),
                "holders_decreasing": str(score.holders_decreasing),
                "aggregate_delta_shares": str(score.aggregate_delta_shares),
                "pattern_score": f"{score.pattern_score:.4f}",
                "option_proxy": f"{score.option_proxy:.4f}",
                "net_score": f"{score.net_score:.4f}",
                "raw_score": f"{score.raw_score:.4f}",
                "top_holder": score.top_holder,
                "top_holder_pct": f"{score.top_holder_pct:.6f}",
                "direction_threshold": f"{_DIRECTION_THRESHOLD}",
                "min_agreeing_holders": str(_MIN_AGREEING_HOLDERS),
            }.items()
        )
    )
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_fund_flow.synthetic",)
    return ExpertSignal(
        expert_name="E_FUND_FLOW",
        ticker=ticker,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype=score.archetype,  # type: ignore[arg-type]
        basis=basis,
        sources=source_strings,
        expires_at=ts + timedelta(days=_SWING_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "_PATTERN_SCORE",
    "EFundFlowExpert",
    "FundFlowScore",
    "_classify_pattern_v2",
    "_holder_deltas",
    "_top_holder",
]
