from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glostat.data.sector_stats import (
    SectorStats,
    SectorStatsBundle,
    compute_universe_stats,
    empty_bundle,
    fallback_stats,
    load_sector_stats,
    save_sector_stats,
    summarize,
)
from glostat.data.universe import Universe

# Sprint 1 PR #4 — sector statistics tests.


def _make_universe(tickers: tuple[str, ...]) -> Universe:
    return Universe(
        name="TEST_UNI",
        description="test",
        markets=("XNAS", "XNYS"),
        tickers=tickers,
        size=len(tickers),
    )


# ── compute_universe_stats ─────────────────────────────────────────────────


def test_compute_universe_stats_returns_bundle() -> None:
    universe = _make_universe(("AAPL", "MSFT", "NVDA", "GOOGL", "META"))

    async def resolver(ticker: str) -> tuple[str, tuple[float | None, float | None, float | None]]:
        # All in Technology with similar PER/ROE
        return "Technology", (28.0 + len(ticker), 0.30, 1e12)

    bundle = asyncio.run(compute_universe_stats(universe, resolve_ticker=resolver))
    assert isinstance(bundle, SectorStatsBundle)
    assert "Technology" in bundle.by_sector
    tech = bundle.by_sector["Technology"]
    assert tech.sample_size == 5


def test_sector_stats_median_calculation() -> None:
    universe = _make_universe(("A", "B", "C"))

    async def resolver(ticker: str) -> tuple[str, tuple[float | None, float | None, float | None]]:
        per_map = {"A": 20.0, "B": 30.0, "C": 40.0}
        roe_map = {"A": 0.10, "B": 0.20, "C": 0.30}
        return "Technology", (per_map[ticker], roe_map[ticker], 1e9)

    bundle = asyncio.run(compute_universe_stats(universe, resolve_ticker=resolver))
    tech = bundle.by_sector["Technology"]
    assert tech.per_median == pytest.approx(30.0)
    assert tech.roe_median == pytest.approx(0.20)
    assert tech.sample_size == 3
    assert tech.is_fallback is False


def test_sector_stats_fallback_when_too_few_samples() -> None:
    universe = _make_universe(("A", "B"))  # only 2 samples → below MIN_SAMPLES

    async def resolver(ticker: str) -> tuple[str, tuple[float | None, float | None, float | None]]:
        return "TinySector", (25.0, 0.20, 1e9)

    bundle = asyncio.run(compute_universe_stats(universe, resolve_ticker=resolver))
    tiny = bundle.by_sector["TinySector"]
    assert tiny.is_fallback is True
    assert tiny.per_median == 22.0   # global fallback
    assert tiny.roe_median == 0.18


def test_sector_stats_unknown_bucket_always_present() -> None:
    universe = _make_universe(("A", "B", "C"))

    async def resolver(ticker: str) -> tuple[str, tuple[float | None, float | None, float | None]]:
        return "Technology", (30.0, 0.25, 1e9)

    bundle = asyncio.run(compute_universe_stats(universe, resolve_ticker=resolver))
    # Even though no "UNKNOWN" sector seen, .get() returns fallback.
    unknown = bundle.get("UNKNOWN")
    assert unknown.is_fallback is True


def test_sector_stats_ignores_resolver_failures() -> None:
    universe = _make_universe(("A", "B", "C", "D"))

    async def resolver(ticker: str) -> tuple[str, tuple[float | None, float | None, float | None]]:
        if ticker == "B":
            raise RuntimeError("boom")
        return "Technology", (25.0, 0.20, 1e9)

    bundle = asyncio.run(compute_universe_stats(universe, resolve_ticker=resolver))
    # 3 successful samples → meets MIN_SAMPLES_PER_SECTOR
    assert bundle.by_sector["Technology"].sample_size == 3


# ── persistence ────────────────────────────────────────────────────────────


def test_sector_stats_cached_to_parquet(tmp_path: Path) -> None:
    universe = _make_universe(("A", "B", "C"))

    async def resolver(ticker: str) -> tuple[str, tuple[float | None, float | None, float | None]]:
        per_map = {"A": 22.0, "B": 28.0, "C": 25.0}
        return "Technology", (per_map[ticker], 0.30, 1e9)

    bundle = asyncio.run(compute_universe_stats(universe, resolve_ticker=resolver))
    cache_path = tmp_path / "sector_stats.parquet"
    save_sector_stats(bundle, cache_path=cache_path)
    assert cache_path.exists()

    reloaded = load_sector_stats(cache_path=cache_path)
    assert reloaded is not None
    assert reloaded.universe == "TEST_UNI"
    assert "Technology" in reloaded.by_sector
    assert reloaded.by_sector["Technology"].per_median == pytest.approx(25.0)


def test_load_sector_stats_returns_none_when_missing(tmp_path: Path) -> None:
    bundle = load_sector_stats(cache_path=tmp_path / "nope.parquet")
    assert bundle is None


# ── TTL / staleness ────────────────────────────────────────────────────────


def test_sector_stats_ttl_7d() -> None:
    fresh = SectorStatsBundle(
        fetched_at=datetime.now(tz=UTC) - timedelta(days=3),
        universe="x",
        by_sector={"X": fallback_stats("X")},
    )
    assert not fresh.is_stale()

    stale = SectorStatsBundle(
        fetched_at=datetime.now(tz=UTC) - timedelta(days=8),
        universe="x",
        by_sector={"X": fallback_stats("X")},
    )
    assert stale.is_stale()


# ── helpers ────────────────────────────────────────────────────────────────


def test_empty_bundle_provides_fallback_for_known_sectors() -> None:
    bundle = empty_bundle("US_LARGE_SAMPLE")
    s = bundle.get("Technology")
    assert s.is_fallback is True
    assert s.sample_size == 0


def test_summarize_emits_one_line_per_sector() -> None:
    bundle = SectorStatsBundle(
        fetched_at=datetime.now(tz=UTC),
        universe="x",
        by_sector={
            "Technology": SectorStats(
                sector="Technology", sample_size=10,
                per_median=30.0, per_stddev=5.0,
                roe_median=0.30, roe_stddev=0.05,
                is_fallback=False,
            ),
        },
    )
    out = summarize(bundle)
    assert "Technology" in out
    assert "n=10" in out
    assert "30." in out


def test_get_unknown_sector_returns_fallback() -> None:
    bundle = SectorStatsBundle(
        fetched_at=datetime.now(tz=UTC),
        universe="x",
        by_sector={},
    )
    s = bundle.get("MysterySector")
    assert s.is_fallback is True
