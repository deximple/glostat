from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

import structlog

# Naver Finance KR investor-flow client (Phase 1D Thesis E9 — 외국인 reversal).
# Source: https://finance.naver.com/item/frgn.naver?code={code}&page={page}
# Each page returns ~20 trading days; pagination walks back ~25 pages → ~500 days.
# Output per row: date, close, organ_net, foreign_net, foreign_holdings, foreign_hold_pct.
# Self-throttle: 1 req/sec to be polite (no published rate limit, but TITAN convention).

log: Final = structlog.get_logger(__name__)

_RATE_LIMIT_PER_SEC: Final[float] = 1.0
_MIN_INTERVAL_S: Final[float] = 1.0 / _RATE_LIMIT_PER_SEC

_FRGN_URL: Final[str] = "https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
_HEADERS: Final[dict[str, str]] = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Referer": "https://finance.naver.com/",
}

_ROW_RE: Final = re.compile(r'<tr[^>]*onMouseOver[^>]*>(.*?)</tr>', re.DOTALL)
_DATE_RE: Final = re.compile(r'<span class="tah p10 gray03">(\d{4})\.(\d{2})\.(\d{2})</span>')
_NUM_RE: Final = re.compile(r'<span class="tah p11[^"]*">\s*([\-\+0-9,\.]+)%?\s*</span>')

_DEFAULT_CACHE_DIR: Final[Path] = Path("cache") / "naver_kr"


class NaverKrError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class KrFlowBar:
    code: str
    bar_date: date
    close_price: float
    organ_net: float       # 기관 순매수 (shares)
    foreign_net: float     # 외국인 순매수 (shares)
    foreign_holdings: float
    foreign_hold_pct: float


def _parse_signed_int(s: str) -> float:
    s2 = s.strip().replace(",", "").replace("+", "")
    if not s2 or s2 == "-":
        return 0.0
    try:
        return float(s2)
    except ValueError:
        return 0.0


def _parse_pct(s: str) -> float:
    s2 = s.strip().replace("%", "").replace(",", "").replace("+", "")
    if not s2:
        return 0.0
    try:
        return float(s2)
    except ValueError:
        return 0.0


def parse_frgn_page(html: str, code: str) -> list[KrFlowBar]:
    bars: list[KrFlowBar] = []
    rows = _ROW_RE.findall(html)
    for row in rows:
        m_date = _DATE_RE.search(row)
        if not m_date:
            continue
        y, mo, d = int(m_date.group(1)), int(m_date.group(2)), int(m_date.group(3))
        try:
            bar_date = date(y, mo, d)
        except ValueError:
            continue
        nums = _NUM_RE.findall(row)
        # Expect 7-8 numeric spans in order:
        #   close, diff, pct, volume, organ_net, foreign_net, foreign_holdings, foreign_pct
        # Some rows may collapse the diff/pct, so we filter by length.
        if len(nums) < 6:
            continue
        try:
            close = _parse_signed_int(nums[0])
            # Walk from end — last is pct, second-last is holdings, then foreign_net, organ_net
            foreign_pct = _parse_pct(nums[-1])
            foreign_holdings = _parse_signed_int(nums[-2])
            foreign_net = _parse_signed_int(nums[-3])
            organ_net = _parse_signed_int(nums[-4])
        except (ValueError, IndexError):
            continue
        bars.append(KrFlowBar(
            code=code,
            bar_date=bar_date,
            close_price=close,
            organ_net=organ_net,
            foreign_net=foreign_net,
            foreign_holdings=foreign_holdings,
            foreign_hold_pct=foreign_pct,
        ))
    return bars


class NaverKrClient:
    def __init__(self, *, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._next_slot: float = 0.0
        self._lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = max(0.0, self._next_slot - now)
            self._next_slot = max(now, self._next_slot) + _MIN_INTERVAL_S
        if wait > 0:
            await asyncio.sleep(wait)

    async def fetch_page(self, code: str, page: int = 1, *, timeout: float = 12.0) -> str:
        await self._throttle()
        import urllib.error  # noqa: PLC0415
        import urllib.request  # noqa: PLC0415

        url = _FRGN_URL.format(code=code, page=page)
        req = urllib.request.Request(url, headers=_HEADERS)
        try:
            return await asyncio.to_thread(_blocking_read, req, timeout)
        except (urllib.error.URLError, TimeoutError) as exc:
            raise NaverKrError(f"naver_frgn fetch failed {code} page={page}: {exc}") from exc

    async def fetch_history(
        self,
        code: str,
        *,
        max_pages: int = 30,
        until_date: date | None = None,
    ) -> list[KrFlowBar]:
        # Walks page=1..max_pages, parses each, stops on empty page or once ALL parsed
        # rows on a page predate `until_date`. Returns oldest-first list.
        all_bars: list[KrFlowBar] = []
        seen_dates: set[date] = set()
        for p in range(1, max_pages + 1):
            try:
                html = await self.fetch_page(code, p)
            except NaverKrError as exc:
                log.warning("naver_kr.page_failed", code=code, page=p, err=str(exc))
                break
            bars = parse_frgn_page(html, code)
            if not bars:
                break
            new_count = 0
            for bar in bars:
                if bar.bar_date in seen_dates:
                    continue
                seen_dates.add(bar.bar_date)
                all_bars.append(bar)
                new_count += 1
            log.info("naver_kr.page_parsed", code=code, page=p, new_rows=new_count)
            if new_count == 0:
                break
            if until_date is not None and all(b.bar_date < until_date for b in bars):
                break
        all_bars.sort(key=lambda b: b.bar_date)
        return all_bars

    def cache_path(self, code: str) -> Path:
        return self._cache_dir / f"{code}.parquet"

    def load_cached(self, code: str) -> list[KrFlowBar]:
        path = self.cache_path(code)
        if not path.exists():
            return []
        try:
            import pyarrow.parquet as pq  # noqa: PLC0415

            table = pq.read_table(path)
            rows = table.to_pylist()
            out: list[KrFlowBar] = []
            for r in rows:
                bd_raw = r.get("bar_date")
                if isinstance(bd_raw, datetime):
                    bd = bd_raw.date()
                elif isinstance(bd_raw, date):
                    bd = bd_raw
                else:
                    bd = date.fromisoformat(str(bd_raw))
                out.append(KrFlowBar(
                    code=str(r.get("code", code)),
                    bar_date=bd,
                    close_price=float(r.get("close_price", 0) or 0),
                    organ_net=float(r.get("organ_net", 0) or 0),
                    foreign_net=float(r.get("foreign_net", 0) or 0),
                    foreign_holdings=float(r.get("foreign_holdings", 0) or 0),
                    foreign_hold_pct=float(r.get("foreign_hold_pct", 0) or 0),
                ))
            return out
        except Exception as exc:
            log.warning("naver_kr.cache_load_failed", code=code, err=str(exc))
            return []

    def save_cache(self, code: str, bars: list[KrFlowBar]) -> Path:
        path = self.cache_path(code)
        if not bars:
            return path
        try:
            import pyarrow as pa  # noqa: PLC0415
            import pyarrow.parquet as pq  # noqa: PLC0415

            payload = [{
                "code": b.code,
                "bar_date": b.bar_date.isoformat(),
                "close_price": b.close_price,
                "organ_net": b.organ_net,
                "foreign_net": b.foreign_net,
                "foreign_holdings": b.foreign_holdings,
                "foreign_hold_pct": b.foreign_hold_pct,
            } for b in sorted(bars, key=lambda x: x.bar_date)]
            table = pa.Table.from_pylist(payload)
            tmp = path.with_suffix(path.suffix + ".tmp")
            pq.write_table(table, tmp, compression="zstd")
            tmp.replace(path)
            log.info("naver_kr.cache_saved", code=code, rows=len(bars), path=str(path))
        except Exception as exc:
            log.warning("naver_kr.cache_save_failed", code=code, err=str(exc))
        return path


def _blocking_read(req, timeout: float) -> str:
    import urllib.request  # noqa: PLC0415

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    # Naver returns euc-kr; fall back to utf-8 if needed
    try:
        return raw.decode("euc-kr", errors="replace")
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace")


# v1.4 N1 — 3-source fusion helper.
#
# Source priority for KR investor flows:
#   1. KIS (real-time intraday) — most authoritative when configured
#   2. Toss (local parquet cache) — operator-curated, schema-pinned
#   3. Naver (HTML scrape) — fallback, always available
# Cross-validation: when 2+ sources disagree by > _DISAGREEMENT_THRESHOLD on the
# same date the helper logs a warning and uses the median across sources for
# that day. This keeps a single bad scrape from poisoning downstream signals.

_DISAGREEMENT_THRESHOLD: Final[float] = 0.50  # 50%


@dataclass(frozen=True, slots=True)
class FusedFlowBar:
    bar_date: date
    code: str
    foreign_net: float           # shares (Naver/KIS) or KRW (Toss); see `units`
    organ_net: float
    sources: tuple[str, ...]     # ordered "kis" / "toss" / "naver" subset
    units: str                   # "shares" or "won"
    cross_validated: bool        # True when ≥ 2 sources contributed for the date


def fuse_three_source_flows(
    *,
    code: str,
    naver_bars: list[KrFlowBar] | None = None,
    toss_bars: list | None = None,         # list[TossInvestorBar]
    kis_daily: list | None = None,         # list[KisDailySummary]
    disagreement_threshold: float = _DISAGREEMENT_THRESHOLD,
) -> list[FusedFlowBar]:
    """Merge three KR investor-flow sources into a single per-date series.

    KIS and Toss are KRW-denominated daily summaries; Naver is share-count.
    The fusion converts to a single canonical surface per date with the share
    count when Naver is present (Toss/KIS provide cross-check), or the KRW
    figure otherwise. Cross-validation flags when sources disagree > threshold.
    """
    by_date: dict[date, dict[str, dict[str, float]]] = {}
    for bar in naver_bars or []:
        slot = by_date.setdefault(bar.bar_date, {})
        slot["naver"] = {
            "foreign": bar.foreign_net, "organ": bar.organ_net, "units": 0,
        }
    for bar in toss_bars or []:
        slot = by_date.setdefault(bar.bar_date, {})
        slot["toss"] = {
            "foreign": float(bar.foreign_net_won),
            "organ": float(bar.institutional_net_won),
            "units": 1,
        }
    for bar in kis_daily or []:
        slot = by_date.setdefault(bar.bar_date, {})
        slot["kis"] = {
            "foreign": float(bar.foreign_net_won),
            "organ": float(bar.institutional_net_won),
            "units": 1,
        }
    out: list[FusedFlowBar] = []
    for d in sorted(by_date.keys()):
        entries = by_date[d]
        sources_present = tuple(s for s in ("kis", "toss", "naver") if s in entries)
        # Pick canonical units: shares if Naver is present, won otherwise.
        prefer_naver = "naver" in entries
        units = "shares" if prefer_naver else "won"
        foreign_vals: list[float] = []
        organ_vals: list[float] = []
        for s in sources_present:
            e = entries[s]
            # Skip mismatched units when picking the median.
            if (units == "shares" and e["units"] == 1) or (
                units == "won" and e["units"] == 0
            ):
                continue
            foreign_vals.append(e["foreign"])
            organ_vals.append(e["organ"])
        if not foreign_vals and sources_present:
            # No same-unit cross — fall back to whichever source we have, dropping
            # the cross-check.
            primary = sources_present[0]
            foreign_vals = [entries[primary]["foreign"]]
            organ_vals = [entries[primary]["organ"]]
            units = "shares" if entries[primary]["units"] == 0 else "won"
        cross_validated = len(foreign_vals) >= 2
        if cross_validated and _disagree(foreign_vals, disagreement_threshold):
            log.warning(
                "naver_kr.fusion_disagreement",
                code=code, bar_date=d.isoformat(),
                sources=sources_present, foreign_vals=foreign_vals,
            )
        fused = FusedFlowBar(
            bar_date=d, code=code,
            foreign_net=_median(foreign_vals) if foreign_vals else 0.0,
            organ_net=_median(organ_vals) if organ_vals else 0.0,
            sources=sources_present, units=units,
            cross_validated=cross_validated,
        )
        out.append(fused)
    return out


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return 0.5 * (s[n // 2 - 1] + s[n // 2])


def _disagree(vals: list[float], threshold: float) -> bool:
    # Disagreement = (max - min) / max(|max|, |min|, 1) > threshold.
    if len(vals) < 2:
        return False
    lo, hi = min(vals), max(vals)
    denom = max(abs(lo), abs(hi), 1.0)
    return (hi - lo) / denom > threshold


__all__ = [
    "FusedFlowBar",
    "KrFlowBar",
    "NaverKrClient",
    "NaverKrError",
    "fuse_three_source_flows",
    "parse_frgn_page",
]
