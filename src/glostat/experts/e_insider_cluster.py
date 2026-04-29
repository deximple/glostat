from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Final

import structlog

from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.sec_edgar_form4 import (
    Form4Transaction,
    cluster_buy_count,
    cluster_buy_value,
    get_form4_transactions,
)
from glostat.phase1b.form4_disk_cache import load as _cache_load
from glostat.phase1b.form4_disk_cache import save as _cache_save
from glostat.phase1b.types import PhaseSignal

# E6 — Russell 2000 Insider Cluster + Low-Coverage Expert.
# Universe: top 200 Russell 2000 stocks by liquidity (NOT in S&P 500).
# Signal:
#   1. Pull all Form 4 transactions filed in trailing 180 days per issuer.
#   2. For each candidate day d:
#      - count unique insider buyers in [d-14d, d] (cluster_buy_count)
#      - if count >= 3 → cluster signal
#   3. Combine with low analyst coverage (<= 5) for stronger conviction.
#      We treat coverage as a filter at universe-build time (cheap), so
#      every ticker entering this hindcast has already passed the screen.
# Score: cluster_count * 0.5, clipped to [0, 3.0]; LONG if score >= 1.0.

log: Final = structlog.get_logger(__name__)

_CLUSTER_THRESHOLD: Final[int] = 3
_SCORE_PER_BUYER: Final[float] = 0.5
_SCORE_CLIP: Final[float] = 3.0
_WINDOW_DAYS: Final[int] = 14
_CONFIDENCE_BASE: Final[float] = 0.5


@dataclass(frozen=True, slots=True)
class InsiderClusterScore:
    cluster_buyers: int
    cluster_value_usd: float
    score: float
    direction: str


class EInsiderClusterExpert:
    name = "E_INSIDER_CLUSTER"

    def __init__(
        self,
        *,
        sec_client: SecEdgarClient,
    ) -> None:
        self._sec = sec_client
        self._txn_cache: dict[str, list[Form4Transaction]] = {}

    async def warm_cache(self, ticker: str, cik: str, days_back: int = 760) -> int:
        # WHY: hindcast spans 2024-01..2026-03 → fetch ~760 days of Form 4s once
        # per ticker, then in-memory filter for each candidate day. Far cheaper
        # than re-pulling per-day filings. Disk cache persists across runs.
        ticker_u = ticker.upper()
        if ticker_u in self._txn_cache:
            return len(self._txn_cache[ticker_u])
        cached = _cache_load(ticker_u, days_back)
        if cached is not None:
            self._txn_cache[ticker_u] = cached
            return len(cached)
        try:
            txns = await get_form4_transactions(
                self._sec, cik, days_back=days_back, limit=80, parallel=3,
            )
        except Exception as exc:
            log.warning(
                "insider.warm_failed",
                ticker=ticker_u, cik=cik, err=str(exc),
            )
            self._txn_cache[ticker_u] = []
            return 0
        self._txn_cache[ticker_u] = txns
        try:
            _cache_save(ticker_u, days_back, txns)
        except Exception as exc:
            log.warning("insider.cache_save_failed", ticker=ticker_u, err=str(exc))
        return len(txns)

    def transactions_for(self, ticker: str) -> list[Form4Transaction]:
        return list(self._txn_cache.get(ticker.upper(), []))

    def score_at(self, ticker: str, day: date) -> InsiderClusterScore:
        txns = self._txn_cache.get(ticker.upper(), [])
        n_buyers = cluster_buy_count(txns, window_end=day, window_days=_WINDOW_DAYS)
        value = cluster_buy_value(txns, window_end=day, window_days=_WINDOW_DAYS)
        raw = n_buyers * _SCORE_PER_BUYER
        score = min(_SCORE_CLIP, raw)
        direction = "LONG" if n_buyers >= _CLUSTER_THRESHOLD else "NEUTRAL"
        if direction == "NEUTRAL":
            score = 0.0
        return InsiderClusterScore(
            cluster_buyers=n_buyers,
            cluster_value_usd=value,
            score=score,
            direction=direction,
        )

    def signal_at(self, ticker: str, day: date) -> PhaseSignal:
        s = self.score_at(ticker, day)
        confidence = (
            min(1.0, _CONFIDENCE_BASE + 0.1 * s.cluster_buyers)
            if s.direction != "NEUTRAL" else 0.0
        )
        return PhaseSignal(
            expert=self.name,
            ticker=ticker.upper(),
            day=day,
            score=s.score,
            direction=s.direction,
            confidence=confidence,
            metadata=(
                ("cluster_buyers", str(s.cluster_buyers)),
                ("cluster_value_usd", f"{s.cluster_value_usd:.0f}"),
                ("window_days", str(_WINDOW_DAYS)),
            ),
        )

    def cluster_event_dates(self, ticker: str) -> list[date]:
        # WHY: enumerate candidate trade dates — only days where at least one
        # buy was filed are interesting; otherwise the cluster window is stale.
        txns = self._txn_cache.get(ticker.upper(), [])
        return sorted({t.transaction_date for t in txns if t.is_buy})


def filter_universe_by_form4_activity(
    candidates: Sequence[tuple[str, str]],
    txn_counts: dict[str, int],
    *,
    min_buys: int = 1,
) -> list[tuple[str, str]]:
    return [
        (ticker, cik)
        for (ticker, cik) in candidates
        if txn_counts.get(ticker.upper(), 0) >= min_buys
    ]


__all__ = [
    "EInsiderClusterExpert",
    "InsiderClusterScore",
    "filter_universe_by_form4_activity",
]
