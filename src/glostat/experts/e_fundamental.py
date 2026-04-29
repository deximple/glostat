from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

import structlog

from glostat.core.errors import ExpertSkipError
from glostat.core.types import ExpertSignal
from glostat.data.data_router import DataRouter
from glostat.data.sec_edgar_client import CompanyFacts
from glostat.data.sector_stats import (
    SectorStats,
    SectorStatsBundle,
    empty_bundle,
    fallback_stats,
)
from glostat.data.yfinance_client import Fundamentals

# E_FUNDAMENTAL — first real Expert. Inputs:
#   yfinance fundamentals (PER, ROE, EPS_TTM, market cap)
#   SEC EDGAR XBRL company_facts (Revenue, NetIncome history → trend)
# Score formula (PR description, kept conservative):
#   per_z   = z-score of trailing PER vs sector median (fallback: global S&P 5y avg)
#   roe_z   = z-score of ROE vs sector median (fallback: global S&P 5y avg)
#   eps_sur = (latest_actual_eps - latest_estimate) / |latest_estimate|, 0 if no estimate
#   net     = clip(0.4*per_z_inverted + 0.4*roe_z + 0.2*eps_sur, -3, +3)
#   PER z-score is INVERTED (low PER = positive signal for value tilt).
# Sprint 1 PR #4: replaced hardcoded sector medians with injected SectorStatsBundle
# computed per universe; sector resolved via entity_map lookup.

log: Final = structlog.get_logger(__name__)

# Sprint 1 PR #4: hardcoded _SECTOR_MEDIAN_* placeholders removed. Sector stats
# are now sourced from `SectorStatsBundle` injected by the universe build job.
# Legacy single-arg `_per_z_score(per)` / `_roe_z_score(roe)` helpers fall back
# to `fallback_stats("UNKNOWN")` (global S&P 5y averages) for backwards compat.

_WEIGHT_PER: Final[float] = 0.4
_WEIGHT_ROE: Final[float] = 0.4
_WEIGHT_EPS_SUR: Final[float] = 0.2

_DIRECTION_THRESHOLD: Final[float] = 1.5
_SCORE_CLIP: Final[float] = 3.0
_SWING_HORIZON_DAYS: Final[int] = 30


@dataclass(frozen=True, slots=True)
class FundamentalScore:
    per_z: float
    roe_z: float
    eps_surprise: float
    net_score: float
    raw_score: float = 0.0
    sector: str = "UNKNOWN"
    sector_sample_size: int = 0
    sector_fallback: bool = True

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
    def clipped(self) -> bool:
        return abs(self.raw_score) > _SCORE_CLIP


@dataclass(frozen=True, slots=True)
class _Source:
    name: str
    snapshot_id: str
    ts: datetime


# Optional sector resolver — async function ticker → sector name.
# If not injected, expert defaults to UNKNOWN sector → fallback medians.
SectorResolver = Any  # Callable[[str], Awaitable[str]] but kept as Any to avoid mypy noise


class EFundamentalExpert:
    name = "E_FUNDAMENTAL"

    def __init__(
        self,
        *,
        router: DataRouter,
        sector_stats: SectorStatsBundle | None = None,
        sector_resolver: SectorResolver | None = None,
    ) -> None:
        self._router = router
        self._sector_stats = sector_stats or empty_bundle("default")
        self._sector_resolver = sector_resolver

    async def compute(self, ticker: str, ts: datetime) -> ExpertSignal:
        ticker = ticker.upper().strip()
        sources: list[_Source] = []
        fundamentals = await self._fetch_fundamentals(ticker, sources)
        # Sprint 4 PR #3: fail-fast on missing primary signal. PER (or forward PE)
        # is the dominant input — if both are absent yfinance has effectively no
        # data for this ticker (delisted, illiquid, parser glitch). Returning a
        # silent net_score=0 here was responsible for half the AUC=0.5 in PR #2.
        if fundamentals.pe_ratio is None and fundamentals.forward_pe is None:
            raise ExpertSkipError(
                f"E_FUNDAMENTAL: missing PER for {ticker}@{ts.date().isoformat()}"
            )
        company_facts = await self._fetch_company_facts(ticker, sources)
        sector = await self._resolve_sector(ticker)
        score = self._score(fundamentals, company_facts, sector)
        return _build_signal(
            ticker=ticker,
            ts=ts,
            score=score,
            fundamentals=fundamentals,
            sources=sources,
        )

    async def _resolve_sector(self, ticker: str) -> str:
        if self._sector_resolver is None:
            return "UNKNOWN"
        try:
            return str(await self._sector_resolver(ticker))
        except Exception as exc:
            log.warning("e_fundamental.sector_resolve_failed", ticker=ticker, err=str(exc))
            return "UNKNOWN"

    async def _fetch_fundamentals(
        self, ticker: str, sources: list[_Source]
    ) -> Fundamentals:
        client, method = self._router.route(self.name, "fundamentals")
        result: Fundamentals = await getattr(client, method)(ticker)
        snap_id = getattr(client, "last_snapshot_id", None)
        if snap_id is not None:
            sources.append(
                _Source(name="yfinance.info", snapshot_id=snap_id, ts=datetime.now(tz=UTC))
            )
        return result

    async def _fetch_company_facts(
        self, ticker: str, sources: list[_Source]
    ) -> CompanyFacts | None:
        # WHY: company_facts requires CIK; fetch via separate router lookup is overkill
        # for MVP single-Expert. Resolve CIK directly via the SEC client behind the route.
        try:
            sec_client, _ = self._router.route(self.name, "company_facts")
        except Exception as exc:
            log.warning("e_fundamental.no_sec_route", err=str(exc))
            return None
        if not hasattr(sec_client, "ticker_to_cik"):
            return None
        try:
            cik = await sec_client.ticker_to_cik(ticker)
        except KeyError:
            log.warning("e_fundamental.ticker_not_in_sec", ticker=ticker)
            return None
        cik_snap = getattr(sec_client, "last_snapshot_id", None)
        if cik_snap is not None:
            sources.append(
                _Source(
                    name="sec_edgar.company_tickers",
                    snapshot_id=cik_snap,
                    ts=datetime.now(tz=UTC),
                )
            )
        facts: CompanyFacts = await sec_client.get_company_facts(cik)
        facts_snap = getattr(sec_client, "last_snapshot_id", None)
        if facts_snap is not None:
            sources.append(
                _Source(
                    name="sec_edgar.companyfacts",
                    snapshot_id=facts_snap,
                    ts=datetime.now(tz=UTC),
                )
            )
        return facts

    def _score(
        self, f: Fundamentals, _facts: CompanyFacts | None, sector: str
    ) -> FundamentalScore:
        stats = self._sector_stats.get(sector)
        per_z = _per_z_score_for(f.pe_ratio, stats)
        roe_z = _roe_z_score_for(f.roe, stats)
        eps_sur = _eps_surprise(f.eps, f.forward_eps)
        # WHY: lower PER → positive value tilt → invert the z so cheap = positive.
        per_signal = -per_z
        net = (
            _WEIGHT_PER * per_signal
            + _WEIGHT_ROE * roe_z
            + _WEIGHT_EPS_SUR * eps_sur
        )
        net_clipped = max(-_SCORE_CLIP, min(_SCORE_CLIP, net))
        return FundamentalScore(
            per_z=per_signal,
            roe_z=roe_z,
            eps_surprise=eps_sur,
            net_score=net_clipped,
            raw_score=net,
            sector=sector,
            sector_sample_size=stats.sample_size,
            sector_fallback=stats.is_fallback,
        )


def _per_z_score_for(per: float | None, stats: SectorStats) -> float:
    if per is None or per <= 0:
        return 0.0
    return (per - stats.per_median) / max(stats.per_stddev, 1e-3)


def _roe_z_score_for(roe: float | None, stats: SectorStats) -> float:
    if roe is None:
        return 0.0
    return (roe - stats.roe_median) / max(stats.roe_stddev, 1e-3)


def _per_z_score(per: float | None) -> float:
    # WHY: backward-compat helper used by Sprint 1 PR #1-#3 unit tests; fixed to
    # global S&P fallback medians. New code should pass a SectorStats explicitly.
    return _per_z_score_for(per, fallback_stats("UNKNOWN"))


def _roe_z_score(roe: float | None) -> float:
    return _roe_z_score_for(roe, fallback_stats("UNKNOWN"))


def _eps_surprise(actual: float | None, estimate: float | None) -> float:
    if actual is None or estimate is None or estimate == 0:
        return 0.0
    return (actual - estimate) / abs(estimate)


def _build_signal(
    *,
    ticker: str,
    ts: datetime,
    score: FundamentalScore,
    fundamentals: Fundamentals,
    sources: list[_Source],
) -> ExpertSignal:
    basis = (
        f"PER {fundamentals.pe_ratio} z={score.per_z:.2f}, "
        f"ROE {fundamentals.roe} z={score.roe_z:.2f}, "
        f"EPS surprise {score.eps_surprise * 100:.1f}% "
        f"[sector={score.sector} n={score.sector_sample_size}"
        f"{' fallback' if score.sector_fallback else ''}]"
    )
    metadata: tuple[tuple[str, str], ...] = tuple(
        sorted(
            {
                "per_raw": _fmt(fundamentals.pe_ratio),
                "roe_raw": _fmt(fundamentals.roe),
                "eps_ttm": _fmt(fundamentals.eps),
                "eps_forward": _fmt(fundamentals.forward_eps),
                "market_cap": _fmt(fundamentals.market_cap),
                "per_z": f"{score.per_z:.4f}",
                "roe_z": f"{score.roe_z:.4f}",
                "eps_surprise": f"{score.eps_surprise:.4f}",
                "net_score": f"{score.net_score:.4f}",
                "raw_score": f"{score.raw_score:.4f}",
                "clipped": str(score.clipped),
                "weight_per": f"{_WEIGHT_PER}",
                "weight_roe": f"{_WEIGHT_ROE}",
                "weight_eps_surprise": f"{_WEIGHT_EPS_SUR}",
                "sector": score.sector,
                "sector_sample_size": str(score.sector_sample_size),
                "sector_fallback": str(score.sector_fallback),
            }.items()
        )
    )
    source_strings: tuple[str, ...] = tuple(
        f"{s.name}#{s.snapshot_id[:12]}" for s in sources
    ) or ("e_fundamental.synthetic",)
    return ExpertSignal(
        expert_name="E_FUNDAMENTAL",
        ticker=ticker,
        direction=score.direction,  # type: ignore[arg-type]
        net_score=score.net_score,
        confidence=score.confidence,
        archetype="continuation",
        basis=basis,
        sources=source_strings,
        expires_at=ts + timedelta(days=_SWING_HORIZON_DAYS),
        metadata=metadata,
    )


def _fmt(v: Any) -> str:
    return "n/a" if v is None else str(v)
