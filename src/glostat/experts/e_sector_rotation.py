from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

import structlog

from glostat.phase1b.price_cache import PriceCache
from glostat.phase1b.types import PhaseSignal

# E1 — Sector Rotation Expert.
# Universe: 11 SPDR sector ETFs + SPY benchmark.
# Signal: trailing 6-month (126 trading days) total-return ranked across sectors.
# Construction (long-short, dollar-neutral approximation):
#   - rank sectors by 126d return
#   - top-3 → LONG (score = +2.0)
#   - bottom-3 → SHORT (score = -2.0)
#   - middle 5 → NEUTRAL (score = 0)
# Forward 30d return measures the realised drift after the rebalance.

log: Final = structlog.get_logger(__name__)

SECTOR_ETFS: Final[tuple[str, ...]] = (
    "XLF",  # Financials
    "XLE",  # Energy
    "XLK",  # Technology
    "XLV",  # Health Care
    "XLI",  # Industrials
    "XLP",  # Consumer Staples
    "XLY",  # Consumer Discretionary
    "XLU",  # Utilities
    "XLB",  # Materials
    "XLRE", # Real Estate (XRE in spec is the iShares ticker; XLRE is the SPDR equivalent)
    "XLC",  # Communication Services
)
BENCHMARK: Final[str] = "SPY"

_LOOKBACK_TRADING_DAYS: Final[int] = 126   # ~6 months
_TOP_K: Final[int] = 3
_BOTTOM_K: Final[int] = 3
_LONG_SCORE: Final[float] = 2.0
_SHORT_SCORE: Final[float] = -2.0
_LONG_CONFIDENCE: Final[float] = 0.7
# A trading day → calendar day padding factor; 6 months ≈ 183 calendar days ≥ 126 trading.
_LOOKBACK_CALENDAR_DAYS: Final[int] = 200


@dataclass(frozen=True, slots=True)
class SectorRotationScore:
    rank: int
    n_sectors: int
    momentum_pct: float
    direction: str

    @property
    def score(self) -> float:
        if self.direction == "LONG":
            return _LONG_SCORE
        if self.direction == "SHORT":
            return _SHORT_SCORE
        return 0.0


class ESectorRotationExpert:
    name = "E_SECTOR_ROTATION"

    def __init__(self, *, price_cache: PriceCache) -> None:
        self._cache = price_cache

    async def compute_for_day(self, day: date) -> dict[str, PhaseSignal]:
        # WHY: ranking is cross-sectional — must be computed for the whole
        # universe at once, then per-ticker signals returned.
        momenta: dict[str, float | None] = {}
        for etf in SECTOR_ETFS:
            momenta[etf] = await self._momentum(etf, day)
        # Sort sectors with valid momentum; tickers without momentum (e.g. ETF
        # too young) are skipped from the rank but still get a NEUTRAL signal.
        ranked = sorted(
            ((t, m) for t, m in momenta.items() if m is not None),
            key=lambda x: x[1],
            reverse=True,
        )
        n_sectors = len(ranked)
        out: dict[str, PhaseSignal] = {}
        for rank, (etf, mom) in enumerate(ranked):
            if rank < _TOP_K:
                direction = "LONG"
            elif rank >= n_sectors - _BOTTOM_K:
                direction = "SHORT"
            else:
                direction = "NEUTRAL"
            score = SectorRotationScore(
                rank=rank,
                n_sectors=n_sectors,
                momentum_pct=mom,
                direction=direction,
            )
            out[etf] = PhaseSignal(
                expert=self.name,
                ticker=etf,
                day=day,
                score=score.score,
                direction=direction,
                confidence=_LONG_CONFIDENCE,
                metadata=(
                    ("rank", str(rank)),
                    ("n_sectors", str(n_sectors)),
                    ("momentum_126d_pct", f"{mom * 100:.2f}"),
                ),
            )
        # Tickers with no momentum data → NEUTRAL signal so the loop sees them.
        for etf, mom in momenta.items():
            if etf in out:
                continue
            out[etf] = PhaseSignal(
                expert=self.name,
                ticker=etf,
                day=day,
                score=0.0,
                direction="NEUTRAL",
                confidence=0.0,
                metadata=(("reason", "no_momentum_data"),),
            )
        return out

    async def _momentum(self, ticker: str, day: date) -> float | None:
        await self._cache.get(ticker)
        c0 = self._cache.close_at_or_before(
            ticker, day - timedelta(days=_LOOKBACK_CALENDAR_DAYS)
        )
        c1 = self._cache.close_at_or_before(ticker, day)
        if c0 is None or c1 is None or c0 <= 0:
            return None
        return (c1 - c0) / c0


__all__ = [
    "BENCHMARK",
    "SECTOR_ETFS",
    "ESectorRotationExpert",
    "SectorRotationScore",
]
