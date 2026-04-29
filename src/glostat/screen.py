from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Literal

import structlog

from glostat.core.types import MarketMeta, Verdict
from glostat.data.sector_stats import SectorStatsBundle, empty_bundle
from glostat.data.universe import Universe

# Universe screening (Sprint 1 PR #4).
# Run all 3 Experts across every ticker in a Universe → rank by edge_bps and
# return top-N. Concurrency capped at SCREEN_SEMAPHORE to respect free-stack
# rate limits (yfinance 5/sec, SEC EDGAR 10/sec).

log: Final = structlog.get_logger(__name__)

SCREEN_SEMAPHORE: Final[int] = 10
DEFAULT_TOP_N: Final[int] = 10

SortKey = Literal["edge_bps", "confidence", "edge_x_disagreement"]
ExpertChoice = Literal["fundamental", "time", "fund_flow", "all"]


@dataclass(frozen=True, slots=True)
class ScreenRow:
    ticker: str
    sector: str
    action: str
    conviction_w: float
    edge_bps: float
    cost_passed: bool
    disagreement_weight: float
    contributing_basis: tuple[str, ...]
    verdict_evidence_hash: str

    @property
    def composite_rank(self) -> float:
        # WHY: the spec asks for "edge_bps × disagreement_weight inverted" — meaning
        # high edge AND low disagreement together produce the strongest rank score.
        # disagreement_weight in our codebase is actually agreement (1.0 = consensus),
        # so multiply directly: high edge × high agreement ranks first.
        return self.edge_bps * max(self.disagreement_weight, 1e-3)


@dataclass(frozen=True, slots=True)
class ScreenResult:
    universe: str
    fetched_at: datetime
    rows: tuple[ScreenRow, ...]
    total_processed: int
    total_failed: int
    total_filtered_out: int

    def top(self, n: int) -> tuple[ScreenRow, ...]:
        return self.rows[:n]


# Caller injects an async function: (ticker) → Verdict. This keeps screen.py
# free of CLI / fixture / network plumbing — pure orchestration.
VerdictBuilderFn = Callable[[str], Awaitable[tuple[Verdict, str]]]
SectorResolverFn = Callable[[str], Awaitable[str]]


async def screen_universe(
    universe: Universe,
    *,
    market_meta: MarketMeta,  # noqa: ARG001 — kept for future per-market policy
    build_verdict: VerdictBuilderFn,
    sector_of: SectorResolverFn,
    top_n: int = DEFAULT_TOP_N,
    sort_by: SortKey = "edge_x_disagreement",
    only_cost_passed: bool = True,
    semaphore: int = SCREEN_SEMAPHORE,
) -> ScreenResult:
    sem = asyncio.Semaphore(semaphore)
    rows: list[ScreenRow] = []
    failed = 0

    async def _one(ticker: str) -> ScreenRow | None:
        async with sem:
            try:
                verdict, sector = await build_verdict(ticker)
            except Exception as exc:
                log.warning("screen.verdict_failed", ticker=ticker, err=str(exc))
                return None
            try:
                resolved_sector = sector or await sector_of(ticker)
            except Exception:
                resolved_sector = sector or "UNKNOWN"
            return _row_from_verdict(verdict, resolved_sector)

    results = await asyncio.gather(
        *(_one(t) for t in universe.tickers), return_exceptions=False
    )
    for r in results:
        if r is None:
            failed += 1
            continue
        rows.append(r)

    cost_filtered = 0
    if only_cost_passed:
        before = len(rows)
        rows = [r for r in rows if r.cost_passed]
        cost_filtered = before - len(rows)

    rows.sort(key=_sort_key(sort_by), reverse=True)
    return ScreenResult(
        universe=universe.name,
        fetched_at=datetime.now(tz=UTC),
        rows=tuple(rows[:top_n]),
        total_processed=len(universe.tickers),
        total_failed=failed,
        total_filtered_out=cost_filtered,
    )


def _row_from_verdict(verdict: Verdict, sector: str) -> ScreenRow:
    basis = tuple(s.basis for s in verdict.contributing_signals)
    return ScreenRow(
        ticker=verdict.ticker,
        sector=sector,
        action=verdict.action,
        conviction_w=verdict.conviction_w,
        edge_bps=verdict.edge_bps,
        cost_passed=verdict.cost_passed,
        disagreement_weight=verdict.disagreement_weight,
        contributing_basis=basis,
        verdict_evidence_hash=verdict.evidence_hash,
    )


def _sort_key(sort_by: SortKey) -> Callable[[ScreenRow], float]:
    if sort_by == "edge_bps":
        return lambda r: r.edge_bps
    if sort_by == "confidence":
        return lambda r: r.conviction_w
    return lambda r: r.composite_rank


def render_screen_table(
    result: ScreenResult,
    *,
    disclaimer: str,
    universe_label: str = "",
) -> str:
    # WHY: simple text table — INV-GS-024 requires the disclaimer printed for
    # every consumer-facing emission. screen output is consumer-facing.
    lines: list[str] = []
    lines.append(
        f"=== GLOSTAT screen — {universe_label or result.universe} "
        f"({result.fetched_at.isoformat()}) ==="
    )
    lines.append(
        f"  processed={result.total_processed}  "
        f"failed={result.total_failed}  "
        f"filtered_out_by_cost_gate={result.total_filtered_out}  "
        f"top_returned={len(result.rows)}"
    )
    lines.append("")
    lines.append(
        f"  {'#':>3}  {'TICKER':<7}  {'SECTOR':<22}  {'ACTION':<6}  "
        f"{'CONV_W':>6}  {'EDGE_bps':>9}  {'AGREE':>6}  HEAD-BASIS"
    )
    lines.append("  " + "-" * 100)
    for i, row in enumerate(result.rows, start=1):
        head = row.contributing_basis[0] if row.contributing_basis else ""
        head = (head[:60] + "…") if len(head) > 60 else head
        lines.append(
            f"  {i:>3}  {row.ticker:<7}  {row.sector:<22}  {row.action:<6}  "
            f"{row.conviction_w:>6.2f}  {row.edge_bps:>9.2f}  "
            f"{row.disagreement_weight:>6.2f}  {head}"
        )
    lines.append("")
    lines.append(disclaimer)
    return "\n".join(lines)


def screen_to_json(result: ScreenResult) -> dict[str, Any]:
    return {
        "universe": result.universe,
        "fetched_at": result.fetched_at.isoformat(),
        "total_processed": result.total_processed,
        "total_failed": result.total_failed,
        "total_filtered_out": result.total_filtered_out,
        "rows": [
            {
                "ticker": r.ticker,
                "sector": r.sector,
                "action": r.action,
                "conviction_w": r.conviction_w,
                "edge_bps": r.edge_bps,
                "cost_passed": r.cost_passed,
                "disagreement_weight": r.disagreement_weight,
                "evidence_hash": r.verdict_evidence_hash,
                "basis": list(r.contributing_basis),
            }
            for r in result.rows
        ],
    }


def select_experts_for_screen(
    choice: ExpertChoice,
    *,
    router: Any,
    sector_stats: SectorStatsBundle,
    sector_resolver: SectorResolverFn | None = None,
) -> list[Any]:
    # WHY: imported lazily to avoid a circular import (experts → data_router →
    # screen would be fine, but cli → screen → experts → cli_mocks is cleaner).
    from glostat.experts import EFundamentalExpert, EFundFlowExpert, ETimeExpert  # noqa: PLC0415

    if choice == "fundamental":
        return [
            EFundamentalExpert(
                router=router,
                sector_stats=sector_stats,
                sector_resolver=sector_resolver,
            )
        ]
    if choice == "time":
        return [ETimeExpert(router=router)]
    if choice == "fund_flow":
        return [EFundFlowExpert(router=router)]
    return [
        EFundamentalExpert(
            router=router,
            sector_stats=sector_stats,
            sector_resolver=sector_resolver,
        ),
        ETimeExpert(router=router),
        EFundFlowExpert(router=router),
    ]


def empty_universe_stats(universe_name: str) -> SectorStatsBundle:
    return empty_bundle(universe_name)


def iter_basis_lines(rows: Iterable[ScreenRow]) -> list[str]:
    out: list[str] = []
    for r in rows:
        for b in r.contributing_basis:
            out.append(f"{r.ticker}: {b}")
    return out


__all__ = [
    "DEFAULT_TOP_N",
    "SCREEN_SEMAPHORE",
    "ExpertChoice",
    "ScreenResult",
    "ScreenRow",
    "SortKey",
    "VerdictBuilderFn",
    "empty_universe_stats",
    "iter_basis_lines",
    "render_screen_table",
    "screen_to_json",
    "screen_universe",
    "select_experts_for_screen",
]
