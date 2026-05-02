from __future__ import annotations

import csv
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Final

import structlog

from glostat.data.vkospi_client import (
    HistoryProvider,
    VkospiBar,
    VkospiClient,
    VkospiDataError,
)

# v1.10.7 — CSV-file backend for VkospiClient.
#
# WHY: KRX (data.krx.co.kr) requires a stateful session-cookie + menuId
# trail to access VKOSPI history programmatically — reverse-engineering
# the auth without a browser-automation tool is fragile, and Naver Finance
# no longer exposes VKOSPI on its day-series endpoint. The pragmatic
# unblocker for an operator is to manually export VKOSPI history from the
# KRX Information Data System UI as CSV (one-time per quarter is enough
# for the calibration window) and plug the file in here.
#
# CSV format (minimum two columns; either order accepted):
#   date,close
#   2024-01-02,18.42
#   2024-01-03,17.95
#   ...
# Date forms accepted: YYYY-MM-DD, YYYY/MM/DD, YYYYMMDD, YYYY.MM.DD.
# Header row optional (auto-detected). Lines starting with '#' or blank
# lines ignored.
#
# Usage:
#   client = VkospiClient()
#   attach_csv_provider(client, Path("cache/vkospi_history.csv"))
#   bars = await client.get_history(start=..., end=...)
#
# See docs/VKOSPI_SETUP.md for KRX export instructions.

log: Final = structlog.get_logger(__name__)

_DATE_FORMATS: Final[tuple[str, ...]] = (
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%Y.%m.%d",
    "%Y%m%d",
)
_HEADER_DATE_TOKENS: Final[frozenset[str]] = frozenset({
    "date", "trd_dd", "일자", "날짜", "기준일",
})
_HEADER_CLOSE_TOKENS: Final[frozenset[str]] = frozenset({
    "close", "closing", "체결가", "종가", "현재가", "value",
})


def parse_csv(path: Path) -> tuple[VkospiBar, ...]:
    """Read a VKOSPI history CSV and return sorted, deduplicated bars.

    Skips header rows, comment lines, and rows where either column fails
    to parse. Raises VkospiDataError when the file is empty or every row
    is unparseable so the caller can surface a clear configuration error.
    """
    if not path.exists():
        raise VkospiDataError(f"VKOSPI CSV not found: {path}")
    raw_rows = list(_read_rows(path))
    if not raw_rows:
        raise VkospiDataError(f"VKOSPI CSV empty: {path}")
    date_col, close_col, body = _detect_columns(raw_rows)
    bars: dict[date, VkospiBar] = {}
    parse_failures = 0
    for row in body:
        if len(row) <= max(date_col, close_col):
            parse_failures += 1
            continue
        d = _parse_date(row[date_col])
        c = _parse_float(row[close_col])
        if d is None or c is None or c < 0:
            parse_failures += 1
            continue
        bars[d] = VkospiBar(bar_date=d, close=c)
    if not bars:
        raise VkospiDataError(
            f"VKOSPI CSV {path} produced zero usable rows "
            f"({parse_failures} parse failures)"
        )
    if parse_failures:
        log.info(
            "vkospi_csv.parse_warnings",
            path=str(path), accepted=len(bars), rejected=parse_failures,
        )
    return tuple(sorted(bars.values(), key=lambda b: b.bar_date))


def make_csv_provider(path: Path) -> HistoryProvider:
    """Return an async provider that slices a CSV-loaded series by [start, end].

    The CSV is parsed lazily on the first call; subsequent calls reuse the
    parsed series in-memory. Errors during parsing surface as
    VkospiDataError on the first invocation.
    """
    cache: dict[str, tuple[VkospiBar, ...]] = {}

    async def provider(start: date, end: date) -> tuple[VkospiBar, ...]:
        if "all" not in cache:
            cache["all"] = parse_csv(path)
        return tuple(b for b in cache["all"] if start <= b.bar_date <= end)

    return provider


def attach_csv_provider(client: VkospiClient, path: Path) -> None:
    """Wire `path` as the live history backend on `client`.

    Convenience helper so callers don't need to import HistoryProvider —
    one line in CLI / hindcast / test setup.
    """
    client.set_history_provider(make_csv_provider(path))


# ── internal helpers ─────────────────────────────────────────────────────


def _read_rows(path: Path) -> Iterable[list[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        for row in reader:
            if not row:
                continue
            first = row[0].strip()
            if not first or first.startswith("#"):
                continue
            yield row


def _detect_columns(
    rows: list[list[str]],
) -> tuple[int, int, list[list[str]]]:
    # WHY: accept either column order ("date,close" or "close,date") and
    # tolerate optional header rows. Header detection: first row tokens
    # match _HEADER_DATE_TOKENS / _HEADER_CLOSE_TOKENS. Otherwise default
    # to (0, 1) with first column = date.
    if not rows:
        return 0, 1, []
    head = [c.strip().lower() for c in rows[0]]
    if any(t in head for t in _HEADER_DATE_TOKENS) and any(
        t in head for t in _HEADER_CLOSE_TOKENS
    ):
        date_col = next(
            i for i, c in enumerate(head) if c in _HEADER_DATE_TOKENS
        )
        close_col = next(
            i for i, c in enumerate(head) if c in _HEADER_CLOSE_TOKENS
        )
        return date_col, close_col, rows[1:]
    # No recognisable header — default order.
    return 0, 1, rows


def _parse_date(raw: str) -> date | None:
    s = (raw or "").strip().replace("-", "").replace("/", "").replace(".", "")
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    # Fallback: try formal patterns directly (handles 2024-01-02, etc.).
    from datetime import datetime  # noqa: PLC0415
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _parse_float(raw: str) -> float | None:
    s = (raw or "").strip().replace(",", "").replace("+", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except ValueError:
        return None


__all__ = [
    "attach_csv_provider",
    "make_csv_provider",
    "parse_csv",
]
