from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Final

import structlog

# v1.4 N1 — Toss Securities investor-trend cache reader (local parquet only).
#
# WHY: TITAN's pattern — Toss does not expose a stable public REST API for
# investor flows. Instead operators export Toss app data into local parquet
# shards (cache/toss/{code}.parquet) which this reader consumes. No live HTTP.
#
# Schema (each row):
#   bar_date          : date (UTC midnight)
#   ticker            : 6-digit KR code
#   foreign_net_won   : float (외국인 순매수, KRW)
#   institutional_net_won : float (기관 순매수, KRW)
#   retail_net_won    : float (개인 순매수, KRW)
#   source            : "toss" (constant)
#
# When the cache directory is empty / file missing → returns empty list.

log: Final = structlog.get_logger(__name__)

_DEFAULT_CACHE_DIR: Final[Path] = Path("cache") / "toss"


@dataclass(frozen=True, slots=True)
class TossInvestorBar:
    bar_date: date
    ticker: str
    foreign_net_won: float
    institutional_net_won: float
    retail_net_won: float
    source: str = "toss"


class TossClient:
    """Local parquet cache reader for Toss investor-trend data.

    No live API. Operators populate `cache/toss/{code}.parquet` by exporting
    Toss app data manually; the reader skips silently when files are absent.
    """

    def __init__(self, *, cache_dir: Path | None = None) -> None:
        self._cache_dir = cache_dir or _DEFAULT_CACHE_DIR

    @property
    def cache_dir(self) -> Path:
        return self._cache_dir

    def cache_path(self, code: str) -> Path:
        c = _normalize_code(code)
        return self._cache_dir / f"{c}.parquet"

    def is_available(self, code: str) -> bool:
        return self.cache_path(code).exists()

    def load_investor_trend(
        self, code: str, *, days_back: int | None = None,
    ) -> list[TossInvestorBar]:
        path = self.cache_path(code)
        if not path.exists():
            return []
        try:
            import pyarrow.parquet as pq  # noqa: PLC0415

            table = pq.read_table(path)
            rows = table.to_pylist()
        except Exception as exc:
            log.warning("toss.cache_load_failed", code=code, err=str(exc))
            return []
        bars: list[TossInvestorBar] = []
        for r in rows:
            bd = _coerce_date(r.get("bar_date"))
            if bd is None:
                continue
            bars.append(TossInvestorBar(
                bar_date=bd,
                ticker=str(r.get("ticker", code)),
                foreign_net_won=float(r.get("foreign_net_won", 0) or 0),
                institutional_net_won=float(r.get("institutional_net_won", 0) or 0),
                retail_net_won=float(r.get("retail_net_won", 0) or 0),
                source=str(r.get("source", "toss")),
            ))
        bars.sort(key=lambda b: b.bar_date)
        if days_back is not None and bars:
            cutoff = bars[-1].bar_date.toordinal() - days_back
            bars = [b for b in bars if b.bar_date.toordinal() >= cutoff]
        return bars

    def save_investor_trend(self, code: str, bars: list[TossInvestorBar]) -> Path:
        # Used by tests + downstream tools to seed the cache without reaching
        # into pyarrow plumbing themselves.
        path = self.cache_path(code)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not bars:
            return path
        try:
            import pyarrow as pa  # noqa: PLC0415
            import pyarrow.parquet as pq  # noqa: PLC0415

            payload = [{
                "bar_date": b.bar_date.isoformat(),
                "ticker": b.ticker,
                "foreign_net_won": b.foreign_net_won,
                "institutional_net_won": b.institutional_net_won,
                "retail_net_won": b.retail_net_won,
                "source": b.source,
            } for b in sorted(bars, key=lambda x: x.bar_date)]
            table = pa.Table.from_pylist(payload)
            tmp = path.with_suffix(path.suffix + ".tmp")
            pq.write_table(table, tmp, compression="zstd")
            tmp.replace(path)
        except Exception as exc:
            log.warning("toss.cache_save_failed", code=code, err=str(exc))
        return path


def _normalize_code(ticker: str) -> str:
    t = (ticker or "").strip().upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        t = t[:-3]
    return t


def _coerce_date(raw: object) -> date | None:
    if raw is None:
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    s = str(raw).strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


__all__ = [
    "TossClient",
    "TossInvestorBar",
]
