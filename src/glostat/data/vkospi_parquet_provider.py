from __future__ import annotations

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

# v1.10.18 — Parquet-file backend for VkospiClient.
#
# WHY: MOET 프로젝트의 KRX Data Marketplace fetcher가 VKOSPI 일별 OHLCV를
# parquet 형태로 저장 (`/Applications/MOET/data/us_market/vkospi.parquet`).
# GLOSTAT의 vkospi_client는 pluggable provider를 지원하므로 parquet 직접
# 참조하는 backend 추가로 위기 spike 포함된 실측 데이터 사용 가능.
#
# Schema (MOET 표준):
#   index: date (datetime.date)
#   columns: open, high, low, close, volume (float64)
#
# Use-case: kr-vkospi-hindcast 실측 — 합성 VKOSPI (mean reversion만 묘사)
# 한계 해소. v1.10.10/17의 binding constraint (n=0/3) → 실측 측정 가능.

log: Final = structlog.get_logger(__name__)

_DEFAULT_MOET_PATH: Final[Path] = Path(
    "/Applications/MOET/data/us_market/vkospi.parquet"
)


def parse_parquet(path: Path) -> tuple[VkospiBar, ...]:  # noqa: PLR0912
    """Read MOET-format VKOSPI parquet and return sorted VkospiBar series.

    Skips rows with negative or null close. Raises VkospiDataError on missing
    file or empty result so caller surfaces clear error.
    """
    if not path.exists():
        raise VkospiDataError(f"VKOSPI parquet not found: {path}")
    try:
        import pandas as pd  # noqa: PLC0415 — keep optional dep cold
        df = pd.read_parquet(path)
    except Exception as exc:
        raise VkospiDataError(
            f"VKOSPI parquet read failed for {path}: {exc}"
        ) from exc
    if df.empty:
        raise VkospiDataError(f"VKOSPI parquet empty: {path}")
    if "close" not in df.columns:
        raise VkospiDataError(
            f"VKOSPI parquet missing 'close' column: {list(df.columns)}"
        )
    bars: list[VkospiBar] = []
    n_rejected = 0
    for ts, row in df["close"].items():
        # WHY: parquet index can be pandas.Timestamp (has callable .date()),
        # python datetime (callable), or datetime.date already. Resolve to
        # a clean datetime.date in all cases.
        if isinstance(ts, date) and not hasattr(ts, "hour"):
            # Pure datetime.date already.
            ts_date = ts
        else:
            date_attr = getattr(ts, "date", None)
            if callable(date_attr):
                try:
                    ts_date = date_attr()
                except Exception:
                    n_rejected += 1
                    continue
            elif isinstance(ts, date):
                ts_date = ts
            else:
                n_rejected += 1
                continue
        if not isinstance(ts_date, date):
            n_rejected += 1
            continue
        try:
            close = float(row)
        except (TypeError, ValueError):
            n_rejected += 1
            continue
        if close < 0:
            n_rejected += 1
            continue
        bars.append(VkospiBar(bar_date=ts_date, close=close))
    if not bars:
        raise VkospiDataError(
            f"VKOSPI parquet {path} produced zero usable bars "
            f"({n_rejected} rejected)"
        )
    if n_rejected:
        log.info(
            "vkospi_parquet.rejected",
            path=str(path), accepted=len(bars), rejected=n_rejected,
        )
    return tuple(sorted(bars, key=lambda b: b.bar_date))


def make_parquet_provider(path: Path) -> HistoryProvider:
    """Return async provider slicing parquet bars by [start, end] window.

    Parsed once on first call, cached in-memory thereafter.
    """
    cache: dict[str, tuple[VkospiBar, ...]] = {}

    async def provider(start: date, end: date) -> tuple[VkospiBar, ...]:
        if "all" not in cache:
            cache["all"] = parse_parquet(path)
        return tuple(b for b in cache["all"] if start <= b.bar_date <= end)

    return provider


def attach_parquet_provider(
    client: VkospiClient,
    path: Path = _DEFAULT_MOET_PATH,
) -> None:
    """Wire MOET VKOSPI parquet as the live backend.

    Default path = `/Applications/MOET/data/us_market/vkospi.parquet`.
    """
    client.set_history_provider(make_parquet_provider(path))


__all__ = [
    "attach_parquet_provider",
    "make_parquet_provider",
    "parse_parquet",
]
