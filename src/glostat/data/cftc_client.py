from __future__ import annotations

import asyncio
import csv
import io
import json
import time
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final

import httpx
import structlog

from glostat.core.errors import GlostatError
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

log: Final = structlog.get_logger(__name__)

# Phase 1C — public CFTC COT (Commitments of Traders) Legacy report scraper.
# Direct ZIP download from cftc.gov; no API key required, weekly cadence (Tuesday
# snapshot, Friday release). One ZIP per year, ~2 MB each. Each ZIP holds
# annual.txt — a CSV with ~428 contracts × ~52 weekly rows.
#
# The Legacy "Futures Only" file is the right shape for our use:
#   commercial_long, commercial_short, noncommercial_long, noncommercial_short
# are all spelled out per row. Net positioning + open-interest percentile rank
# (5y rolling) feeds Thesis E8.

_BASE_URL: Final[str] = "https://www.cftc.gov/files/dea/history/deacot{year}.zip"
_USER_AGENT: Final[str] = "GLOSTAT phase1c (deximple@gmail.com)"
_RATE_LIMIT_PER_SEC: Final[int] = 2
_DEFAULT_CACHE: Final[Path] = Path("cache") / "cftc"
_TIMEOUT_S: Final[float] = 30.0


class CftcDataError(GlostatError):
    """Raised when the CFTC archive is missing, empty, or malformed."""


# Canonical contract name → list of (substring patterns, exclude patterns).
# CFTC uses inconsistent naming across years; we match permissively then dedupe.
# WHY tuple-of-tuples: explicit, ordered, immutable. No regex — substring-only.
CONTRACT_PATTERNS: Final[dict[str, tuple[tuple[str, ...], tuple[str, ...]]]] = {
    "WTI_CRUDE":   (("CRUDE OIL, LIGHT SWEET",), ("FINANCIAL",)),
    "NAT_GAS":     (("NAT GAS NYME",), ()),
    "GOLD":        (("GOLD - COMMODITY EXCHANGE",), ()),
    "SILVER":      (("SILVER - COMMODITY EXCHANGE",), ()),
    "COPPER":      (("COPPER- #1",), ()),
    "CORN":        (("CORN - CHICAGO",), ()),
    "WHEAT":       (("WHEAT-SRW",), ()),
}


@dataclass(frozen=True, slots=True)
class CotRecord:
    contract: str                 # canonical name (key from CONTRACT_PATTERNS)
    market_name: str              # raw CFTC contract name
    report_date: date
    open_interest: int
    commercial_long: int
    commercial_short: int
    noncommercial_long: int
    noncommercial_short: int

    @property
    def commercial_net(self) -> int:
        return self.commercial_long - self.commercial_short

    @property
    def noncommercial_net(self) -> int:
        return self.noncommercial_long - self.noncommercial_short

    @property
    def commercial_net_pct(self) -> float:
        if self.open_interest <= 0:
            return 0.0
        return self.commercial_net / self.open_interest


@dataclass
class _Throttle:
    rate_per_sec: int
    _last: float = 0.0

    async def wait(self) -> None:
        delay = 1.0 / max(1, self.rate_per_sec)
        now = time.monotonic()
        wait = max(0.0, self._last + delay - now)
        if wait > 0.0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()


@dataclass(slots=True)
class CftcClient:
    cache_dir: Path = field(default_factory=lambda: _DEFAULT_CACHE)
    snapshot_broker: SnapshotBroker | None = None
    timeout_s: float = _TIMEOUT_S
    _throttle: _Throttle = field(default_factory=lambda: _Throttle(_RATE_LIMIT_PER_SEC))
    _last_snapshot_id: str | None = None
    _records_by_year: dict[int, tuple[CotRecord, ...]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def last_snapshot_id(self) -> str | None:
        return self._last_snapshot_id

    async def fetch_year(self, year: int) -> tuple[CotRecord, ...]:
        if year in self._records_by_year:
            return self._records_by_year[year]
        cache_path = self.cache_dir / f"deacot{year}.zip"
        if not cache_path.exists():
            await self._download_year(year, cache_path)
        records = _parse_zip(cache_path)
        self._records_by_year[year] = records
        self._record_snapshot(year, records)
        return records

    async def fetch_range(
        self, start: date, end: date
    ) -> tuple[CotRecord, ...]:
        years = list(range(start.year, end.year + 1))
        all_recs: list[CotRecord] = []
        for y in years:
            recs = await self.fetch_year(y)
            all_recs.extend(r for r in recs if start <= r.report_date <= end)
        all_recs.sort(key=lambda r: (r.contract, r.report_date))
        return tuple(all_recs)

    async def _download_year(self, year: int, dest: Path) -> None:
        await self._throttle.wait()
        url = _BASE_URL.format(year=year)
        try:
            async with httpx.AsyncClient(
                timeout=self.timeout_s,
                headers={"User-Agent": _USER_AGENT},
                follow_redirects=True,
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise CftcDataError(f"CFTC fetch failed for {year}: {exc}") from exc
        body = resp.content
        if len(body) < 1024:
            raise CftcDataError(
                f"CFTC archive {year} too small ({len(body)} bytes); rejected"
            )
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(dest)
        log.info("cftc.downloaded", year=year, bytes=len(body), path=str(dest))

    def _record_snapshot(self, year: int, records: tuple[CotRecord, ...]) -> None:
        if self.snapshot_broker is None:
            self._last_snapshot_id = None
            return
        payload = {
            "year": year,
            "n_records": len(records),
            "contracts": sorted({r.contract for r in records}),
        }
        params = {"year": year, "source": "cftc.deacot"}
        key = SnapshotKey(
            uaid=f"CFTC.COT.{year}",
            edge_type="cot_legacy_year",
            ts_utc=datetime.now(tz=UTC),
            tool="cftc.deacot.zip",
            params_canon=json.dumps(params, sort_keys=True, separators=(",", ":")),
        )
        rec = self.snapshot_broker.save_snapshot(key, payload)
        self._last_snapshot_id = rec.leaf.leaf_hash


def _parse_zip(path: Path) -> tuple[CotRecord, ...]:
    try:
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            if not names:
                raise CftcDataError(f"empty archive: {path}")
            with zf.open(names[0]) as fp:
                raw = fp.read().decode("utf-8", errors="replace")
    except (zipfile.BadZipFile, OSError) as exc:
        raise CftcDataError(f"unreadable CFTC zip {path}: {exc}") from exc
    return _parse_csv(raw)


def _parse_csv(raw: str) -> tuple[CotRecord, ...]:
    reader = csv.reader(io.StringIO(raw), quotechar='"')
    out: list[CotRecord] = []
    header_seen = False
    for row in reader:
        if not row:
            continue
        if not header_seen:
            header_seen = True
            continue
        canonical = _match_contract(row[0])
        if canonical is None:
            continue
        rec = _row_to_record(row, canonical)
        if rec is not None:
            out.append(rec)
    return tuple(out)


def _match_contract(market_name: str) -> str | None:
    name_upper = market_name.upper()
    for canonical, (includes, excludes) in CONTRACT_PATTERNS.items():
        if any(e in name_upper for e in excludes):
            continue
        if all(p in name_upper for p in includes):
            return canonical
    return None


def _row_to_record(row: list[str], canonical: str) -> CotRecord | None:
    try:
        report_date = date.fromisoformat(row[2].strip())
        oi = _to_int(row[7])
        nc_long = _to_int(row[8])
        nc_short = _to_int(row[9])
        comm_long = _to_int(row[11])
        comm_short = _to_int(row[12])
    except (ValueError, IndexError):
        return None
    return CotRecord(
        contract=canonical,
        market_name=row[0].strip(),
        report_date=report_date,
        open_interest=oi,
        commercial_long=comm_long,
        commercial_short=comm_short,
        noncommercial_long=nc_long,
        noncommercial_short=nc_short,
    )


def _to_int(s: str) -> int:
    s = s.strip().replace(",", "")
    if not s or s == "-" or s.lower() == "nan":
        return 0
    return int(float(s))


def commercial_net_percentile(
    records: tuple[CotRecord, ...],
    *,
    contract: str,
    as_of: date,
    lookback_years: int = 5,
) -> float | None:
    # 5y rolling percentile rank (0-1) of latest commercial_net_pct vs history.
    # Returns None if < 26 weekly samples (~6mo) — too thin to rank.
    cutoff = date(as_of.year - lookback_years, as_of.month, as_of.day)
    series = [
        r for r in records
        if r.contract == contract and cutoff <= r.report_date <= as_of
    ]
    if len(series) < 26:
        return None
    series.sort(key=lambda r: r.report_date)
    latest = series[-1].commercial_net_pct
    prior = [r.commercial_net_pct for r in series[:-1]]
    n_below = sum(1 for v in prior if v < latest)
    return n_below / len(prior)


__all__ = [
    "CONTRACT_PATTERNS",
    "CftcClient",
    "CftcDataError",
    "CotRecord",
    "commercial_net_percentile",
]
