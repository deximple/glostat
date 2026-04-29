from __future__ import annotations

import asyncio
import json
import statistics
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from glostat.data.sector_mapper import GICS_SECTORS, UNKNOWN_SECTOR
from glostat.data.universe import Universe

# Sector statistics cache (Sprint 1 PR #4).
# WHY: E_FUNDAMENTAL was using hardcoded _SECTOR_MEDIAN_PER / _SECTOR_MEDIAN_ROE
# placeholders. Real sector-aware z-score requires per-sector median + stddev
# computed from a representative universe. Refresh quarterly (TTL 7d for cache).

log: Final = structlog.get_logger(__name__)

_DEFAULT_CACHE: Final = Path("cache") / "sector_stats.parquet"
_DEFAULT_TTL: Final[timedelta] = timedelta(days=7)
_MIN_SAMPLES_PER_SECTOR: Final[int] = 3

# Fallback medians used when a sector has fewer than _MIN_SAMPLES_PER_SECTOR
# names in the universe. Conservative averages from S&P 500 trailing 5y.
# WHY: better to fall back to a defensible default than emit a misleading z=0.
_FALLBACK_PER: Final[float] = 22.0
_FALLBACK_PER_STDDEV: Final[float] = 8.0
_FALLBACK_ROE: Final[float] = 0.18
_FALLBACK_ROE_STDDEV: Final[float] = 0.12


@dataclass(frozen=True, slots=True)
class SectorStats:
    sector: str
    sample_size: int
    per_median: float
    per_stddev: float
    roe_median: float
    roe_stddev: float
    is_fallback: bool = False


@dataclass(frozen=True, slots=True)
class SectorStatsBundle:
    fetched_at: datetime
    universe: str
    by_sector: Mapping[str, SectorStats]

    def get(self, sector: str) -> SectorStats:
        # WHY: graceful degradation — unknown sector or missing entry collapses
        # to the global fallback so callers never need a try/except.
        if sector in self.by_sector:
            return self.by_sector[sector]
        return _fallback_stats(sector)

    def is_stale(self, ttl: timedelta = _DEFAULT_TTL) -> bool:
        return datetime.now(tz=UTC) - self.fetched_at > ttl


# Async resolver type — caller injects fundamentals + sector lookup.
TickerFundamentals = tuple[float | None, float | None, float | None]  # (per, roe, market_cap)
ResolveTicker = Callable[[str], Awaitable[tuple[str, TickerFundamentals]]]


async def compute_universe_stats(
    universe: Universe,
    *,
    resolve_ticker: ResolveTicker,
    semaphore: int = 10,
) -> SectorStatsBundle:
    # WHY: bounded concurrency — yfinance + SEC EDGAR rate limits 5+10/sec.
    # Semaphore=10 keeps both within the per-second window with margin.
    sem = asyncio.Semaphore(semaphore)
    samples: dict[str, list[tuple[float | None, float | None]]] = {}

    async def _one(ticker: str) -> None:
        async with sem:
            try:
                sector, (per, roe, _mcap) = await resolve_ticker(ticker)
            except Exception as exc:
                log.warning("sector_stats.resolve_failed", ticker=ticker, err=str(exc))
                return
            samples.setdefault(sector, []).append((per, roe))

    await asyncio.gather(*(_one(t) for t in universe.tickers), return_exceptions=False)
    by_sector = _aggregate_samples(samples)
    return SectorStatsBundle(
        fetched_at=datetime.now(tz=UTC),
        universe=universe.name,
        by_sector=by_sector,
    )


def _aggregate_samples(
    samples: Mapping[str, list[tuple[float | None, float | None]]],
) -> Mapping[str, SectorStats]:
    out: dict[str, SectorStats] = {}
    for sector, rows in samples.items():
        per_values = [p for p, _ in rows if p is not None and p > 0]
        roe_values = [r for _, r in rows if r is not None]
        if len(rows) < _MIN_SAMPLES_PER_SECTOR or not per_values or not roe_values:
            out[sector] = _fallback_stats(sector)
            continue
        per_median = statistics.median(per_values)
        roe_median = statistics.median(roe_values)
        per_stddev = (
            statistics.stdev(per_values) if len(per_values) >= 2 else _FALLBACK_PER_STDDEV
        )
        roe_stddev = (
            statistics.stdev(roe_values) if len(roe_values) >= 2 else _FALLBACK_ROE_STDDEV
        )
        out[sector] = SectorStats(
            sector=sector,
            sample_size=len(rows),
            per_median=per_median,
            per_stddev=max(per_stddev, 1e-3),
            roe_median=roe_median,
            roe_stddev=max(roe_stddev, 1e-3),
            is_fallback=False,
        )
    # Ensure UNKNOWN bucket always present so E_FUNDAMENTAL never misses.
    out.setdefault(UNKNOWN_SECTOR, _fallback_stats(UNKNOWN_SECTOR))
    return out


def _fallback_stats(sector: str) -> SectorStats:
    return SectorStats(
        sector=sector,
        sample_size=0,
        per_median=_FALLBACK_PER,
        per_stddev=_FALLBACK_PER_STDDEV,
        roe_median=_FALLBACK_ROE,
        roe_stddev=_FALLBACK_ROE_STDDEV,
        is_fallback=True,
    )


_SCHEMA: Final = pa.schema(
    [
        ("sector", pa.string()),
        ("sample_size", pa.int64()),
        ("per_median", pa.float64()),
        ("per_stddev", pa.float64()),
        ("roe_median", pa.float64()),
        ("roe_stddev", pa.float64()),
        ("is_fallback", pa.bool_()),
        ("universe", pa.string()),
        ("fetched_at", pa.timestamp("us", tz="UTC")),
    ]
)


def save_sector_stats(
    bundle: SectorStatsBundle, *, cache_path: Path | None = None
) -> Path:
    path = cache_path or _DEFAULT_CACHE
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = [
        {
            "sector": s.sector,
            "sample_size": int(s.sample_size),
            "per_median": float(s.per_median),
            "per_stddev": float(s.per_stddev),
            "roe_median": float(s.roe_median),
            "roe_stddev": float(s.roe_stddev),
            "is_fallback": bool(s.is_fallback),
            "universe": bundle.universe,
            "fetched_at": bundle.fetched_at,
        }
        for s in bundle.by_sector.values()
    ]
    table = pa.Table.from_pylist(rows, schema=_SCHEMA)
    tmp = path.with_suffix(path.suffix + ".tmp")
    pq.write_table(table, tmp, compression="zstd")
    tmp.replace(path)
    log.info("sector_stats.saved", path=str(path), rows=len(rows))
    return path


def load_sector_stats(*, cache_path: Path | None = None) -> SectorStatsBundle | None:
    path = cache_path or _DEFAULT_CACHE
    if not path.exists():
        return None
    try:
        table = pq.read_table(path)
    except Exception as exc:
        log.warning("sector_stats.load_failed", path=str(path), err=str(exc))
        return None
    rows = table.to_pylist()
    if not rows:
        return None
    by_sector = {
        str(r["sector"]): SectorStats(
            sector=str(r["sector"]),
            sample_size=int(r["sample_size"]),
            per_median=float(r["per_median"]),
            per_stddev=float(r["per_stddev"]),
            roe_median=float(r["roe_median"]),
            roe_stddev=float(r["roe_stddev"]),
            is_fallback=bool(r["is_fallback"]),
        )
        for r in rows
    }
    fetched = rows[0]["fetched_at"]
    if not isinstance(fetched, datetime):
        fetched = datetime.fromisoformat(str(fetched))
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return SectorStatsBundle(
        fetched_at=fetched,
        universe=str(rows[0]["universe"]),
        by_sector=by_sector,
    )


def empty_bundle(universe: str) -> SectorStatsBundle:
    # WHY: E_FUNDAMENTAL must always receive a SectorStatsBundle so .get(sector)
    # works. Empty bundle returns fallback stats for every sector queried.
    return SectorStatsBundle(
        fetched_at=datetime.now(tz=UTC),
        universe=universe,
        by_sector={s: _fallback_stats(s) for s in (*GICS_SECTORS, UNKNOWN_SECTOR)},
    )


def fallback_stats(sector: str) -> SectorStats:
    return _fallback_stats(sector)


def summarize(bundle: SectorStatsBundle) -> str:
    # WHY: human-readable one-line-per-sector summary for `glostat universe build` output.
    lines: list[str] = []
    for sector in sorted(bundle.by_sector):
        s = bundle.by_sector[sector]
        suffix = " (fallback)" if s.is_fallback else ""
        lines.append(
            f"  {sector:<22} n={s.sample_size:>2}  "
            f"PER med={s.per_median:>6.2f} std={s.per_stddev:>5.2f}  "
            f"ROE med={s.roe_median:>+6.3f} std={s.roe_stddev:>5.3f}{suffix}"
        )
    return "\n".join(lines)


def stats_to_json(bundle: SectorStatsBundle) -> str:
    payload = {
        "fetched_at": bundle.fetched_at.isoformat(),
        "universe": bundle.universe,
        "sectors": [
            {
                "sector": s.sector,
                "sample_size": s.sample_size,
                "per_median": s.per_median,
                "per_stddev": s.per_stddev,
                "roe_median": s.roe_median,
                "roe_stddev": s.roe_stddev,
                "is_fallback": s.is_fallback,
            }
            for s in bundle.by_sector.values()
        ],
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


__all__ = [
    "ResolveTicker",
    "SectorStats",
    "SectorStatsBundle",
    "TickerFundamentals",
    "compute_universe_stats",
    "empty_bundle",
    "fallback_stats",
    "load_sector_stats",
    "save_sector_stats",
    "stats_to_json",
    "summarize",
]
