from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import structlog

from glostat.data.sec_edgar_client import SecEdgarClient
from glostat.data.sector_mapper import resolve_sic_via_sec, sic_to_gics
from glostat.data.sector_stats import (
    SectorStatsBundle,
    compute_universe_stats,
    empty_bundle,
    load_sector_stats,
    save_sector_stats,
)
from glostat.data.universe import Universe
from glostat.data.yfinance_client import YFinanceClient

# Sprint 5 PR #1 — live sector stats wiring extracted from live_hindcast so that
# module stays under the 400-line house cap. Owns:
#   - 7d-TTL parquet cache for the sector stats bundle
#   - submissions endpoint fetcher used by the SIC → GICS mapper
#   - per-universe SectorStatsBundle builder backed by yfinance + SEC EDGAR
#   - the `sector_resolver` callable EFundamentalExpert calls per ticker

log: Final = structlog.get_logger(__name__)

_DEFAULT_CACHE: Final[Path] = Path("cache") / "sector_stats_live.parquet"
_TTL_DAYS: Final[int] = 7


def resolve_sector_stats(
    *,
    universe: Universe | None,
    yf_client: YFinanceClient,
    sec_client: SecEdgarClient,
    cache_path: Path | None = None,
) -> SectorStatsBundle:
    cache = cache_path or _DEFAULT_CACHE
    cached = load_sector_stats(cache_path=cache)
    if cached is not None and not cached.is_stale(timedelta(days=_TTL_DAYS)):
        log.info("live_sector_stats.cache_hit", path=str(cache))
        return cached
    if universe is None:
        log.info("live_sector_stats.no_universe", reason="empty_bundle_fallback")
        return empty_bundle("live")
    bundle = asyncio.run(_build_async(universe, yf_client, sec_client))
    try:
        save_sector_stats(bundle, cache_path=cache)
    except Exception as exc:
        log.warning("live_sector_stats.save_failed", err=str(exc))
    return bundle


def make_sector_resolver(sec_client: SecEdgarClient) -> Any:
    submissions_fetcher = _make_submissions_fetcher(sec_client)

    async def resolver(ticker: str) -> str:
        try:
            sic = await resolve_sic_via_sec(
                ticker, sec_client=sec_client, submissions_fetcher=submissions_fetcher,
            )
        except Exception:
            return "UNKNOWN"
        return sic_to_gics(sic)

    return resolver


async def _build_async(
    universe: Universe,
    yf_client: YFinanceClient,
    sec_client: SecEdgarClient,
) -> SectorStatsBundle:
    submissions_fetcher = _make_submissions_fetcher(sec_client)

    async def resolve(ticker: str) -> tuple[str, tuple[float | None, float | None, float | None]]:
        sector = "UNKNOWN"
        try:
            sic = await resolve_sic_via_sec(
                ticker, sec_client=sec_client, submissions_fetcher=submissions_fetcher,
            )
            sector = sic_to_gics(sic)
        except Exception as exc:
            log.warning("live_sector_stats.sic_failed", ticker=ticker, err=str(exc))
        try:
            f = await yf_client.get_fundamentals(ticker)
        except Exception as exc:
            log.warning("live_sector_stats.fund_failed", ticker=ticker, err=str(exc))
            return (sector, (None, None, None))
        return (sector, (f.pe_ratio, f.roe, f.market_cap))

    return await compute_universe_stats(universe, resolve_ticker=resolve)


def _make_submissions_fetcher(sec_client: SecEdgarClient) -> Any:
    async def fetch(cik: str) -> dict[str, Any]:
        cik_padded = cik.zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        return await sec_client._get_json(url)
    return fetch


__all__ = ["make_sector_resolver", "resolve_sector_stats"]
