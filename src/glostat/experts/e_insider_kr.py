from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.dart_client import (
    DartApiError,
    DartApiKeyMissingError,
    DartClient,
    is_dart_configured,
)
from glostat.data.dart_types import DartExecutiveTransaction
from glostat.data.data_router import normalize_kr_ticker

# v1.2 L2 — KR equivalent of E_INSIDER_CLUSTER (Form 4 → DART elestock).
#
# WHY: TITAN B4-style cluster signal — 3+ executives buying within 14 days
# is a known KR insider conviction pattern. Until now GLOSTAT had no KR
# insider signal because there was no free + structured KR equivalent of SEC
# Form 4. DART elestock.json fills that gap.
#
# Skip behaviour: graceful + universe-aware. If GLOSTAT_DART_API_KEY is unset,
# the expert raises ExpertSkipError with a clear pointer to docs/DART_API_SETUP.md.

log: Final = structlog.get_logger(__name__)

_CLUSTER_THRESHOLD: Final[int] = 3
_WINDOW_DAYS: Final[int] = 14
_LOOKBACK_DAYS: Final[int] = 180
_SCORE_PER_BUYER: Final[float] = 0.5
_SCORE_CLIP: Final[float] = 3.0
_DIRECTION_THRESHOLD: Final[float] = 1.0
_HORIZON_DAYS: Final[int] = 30


@dataclass(frozen=True, slots=True)
class InsiderKrScore:
    cluster_buyers: int
    cluster_sells: int
    raw_score: float
    net_score: float
    direction: str

    @property
    def confidence(self) -> float:
        return min(1.0, abs(self.net_score) / _SCORE_CLIP)


def cluster_count(
    txns: Sequence[DartExecutiveTransaction],
    *,
    window_end: date,
    window_days: int = _WINDOW_DAYS,
    side: str = "buy",
) -> int:
    # WHY: count unique reporters with at least one buy/sell in [end-window, end].
    # Mirrors sec_edgar_form4.cluster_buy_count semantics but for DART payload.
    if window_days < 1:
        return 0
    window_start = window_end - timedelta(days=window_days)
    seen: set[str] = set()
    for t in txns:
        bsis = _parse_yyyymmdd(t.bsis_dt)
        if bsis is None or bsis < window_start or bsis > window_end:
            continue
        is_target = t.is_buy if side == "buy" else t.is_sell
        if not is_target:
            continue
        key = (t.repror or "").strip()
        if key:
            seen.add(key)
    return len(seen)


def _parse_yyyymmdd(s: str) -> date | None:
    if not s or len(s) < 8 or not s[:8].isdigit():
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def score_insider_kr(
    txns: Sequence[DartExecutiveTransaction],
    *,
    as_of: date,
    window_days: int = _WINDOW_DAYS,
    cluster_threshold: int = _CLUSTER_THRESHOLD,
) -> InsiderKrScore:
    n_buyers = cluster_count(txns, window_end=as_of, window_days=window_days, side="buy")
    n_sellers = cluster_count(txns, window_end=as_of, window_days=window_days, side="sell")
    # WHY: signed score — buys positive, sells negative. Cluster threshold sets
    # the "mass" of signal a side needs to fire; below threshold it returns
    # neutral so single transactions don't move the needle.
    buy_signal = n_buyers if n_buyers >= cluster_threshold else 0
    sell_signal = n_sellers if n_sellers >= cluster_threshold else 0
    raw = (buy_signal - sell_signal) * _SCORE_PER_BUYER
    net = max(-_SCORE_CLIP, min(_SCORE_CLIP, raw))
    direction = (
        "LONG" if net > _DIRECTION_THRESHOLD
        else "SHORT" if net < -_DIRECTION_THRESHOLD
        else "NEUTRAL"
    )
    return InsiderKrScore(
        cluster_buyers=n_buyers, cluster_sells=n_sellers,
        raw_score=raw, net_score=net, direction=direction,
    )


class EInsiderKrExpert:
    """KR insider cluster expert — DART elestock-backed.

    Skip cleanly when DART is unavailable so the composite predictor surfaces a
    universe-aware reason rather than crashing. Mirrors the US E_INSIDER_CLUSTER
    cluster_buy_count semantics for parity in the calibration table.
    """

    name = "E_INSIDER_KR"

    def __init__(
        self,
        *,
        dart_client: DartClient | None = None,
        kospi200: frozenset[str] | None = None,
    ) -> None:
        self._dart = dart_client
        self._kospi200 = kospi200 or frozenset()

    @classmethod
    def from_env(
        cls,
        *,
        kospi200: frozenset[str] | None = None,
    ) -> EInsiderKrExpert | None:
        # WHY: factory that returns None when DART isn't configured. The CLI uses
        # this to decide whether to wire the expert at all (so the composite
        # contributions list shows a graceful "DART not configured" skip).
        if not is_dart_configured():
            return None
        try:
            client = DartClient()
        except DartApiKeyMissingError:
            return None
        return cls(dart_client=client, kospi200=kospi200)

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        if self._dart is None:
            raise ExpertSkipError(
                "E_INSIDER_KR: DART client not configured "
                "(see docs/DART_API_SETUP.md)"
            )
        code = normalize_kr_ticker(ticker)
        if self._kospi200 and code not in self._kospi200:
            raise ExpertSkipError(
                f"E_INSIDER_KR: {code} not in KOSPI 200 universe"
            )
        try:
            corp_code = await self._dart.get_corp_code(code)
        except (DartApiError, DartApiKeyMissingError) as exc:
            raise ExpertSkipError(f"E_INSIDER_KR: corp_code lookup failed: {exc}") from exc
        try:
            txns = await self._dart.get_executive_transactions(
                corp_code, days_back=_LOOKBACK_DAYS,
            )
        except (DartApiError, DartApiKeyMissingError) as exc:
            raise ExpertSkipError(
                f"E_INSIDER_KR: elestock fetch failed for {code}: {exc}"
            ) from exc
        as_of = ts.date()
        score = score_insider_kr(txns, as_of=as_of)
        snap_id = self._dart.last_snapshot_id or "dart.elestock"
        return _signal_from_score(
            code=code, ts=ts, score=score, snap_id=snap_id,
            n_txns=len(txns),
        )


def _signal_from_score(
    *, code: str, ts: datetime, score: InsiderKrScore,
    snap_id: str, n_txns: int,
) -> ExpertSignal:
    basis = (
        f"DART elestock cluster — buyers={score.cluster_buyers}, "
        f"sellers={score.cluster_sells} in trailing {_WINDOW_DAYS}d "
        f"(out of {n_txns} txns over {_LOOKBACK_DAYS}d)"
    )
    metadata: tuple[tuple[str, str], ...] = tuple(
        sorted({
            "cluster_buyers": str(score.cluster_buyers),
            "cluster_sells": str(score.cluster_sells),
            "n_txns_180d": str(n_txns),
            "raw_score": f"{score.raw_score:.4f}",
            "net_score": f"{score.net_score:.4f}",
            "confidence": f"{score.confidence:.4f}",
            "code": code,
        }.items())
    )
    return ExpertSignal(
        expert_name="E_INSIDER_KR",  # type: ignore[arg-type]
        ticker=code,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="continuation",
        basis=basis,
        sources=(snap_id,),
        expires_at=ts + timedelta(days=_HORIZON_DAYS),
        metadata=metadata,
    )


__all__ = [
    "EInsiderKrExpert",
    "InsiderKrScore",
    "cluster_count",
    "score_insider_kr",
]
