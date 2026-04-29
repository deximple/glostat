from __future__ import annotations

from pathlib import Path
from typing import Final

import structlog

# v1.1 K1 — KR universe + ticker normalization helpers used by thesis wrappers.
# Extracted from thesis_wrappers.py to keep that module under the 400-line cap.

log: Final = structlog.get_logger(__name__)

_KR_TICKER_PREFIX_LEN: Final[int] = 6
_KOSPI200_FILE: Final[Path] = (
    Path(__file__).resolve().parents[3] / "configs" / "universes" / "kospi200.txt"
)


def load_kospi200(path: Path = _KOSPI200_FILE) -> frozenset[str]:
    if not path.exists():
        log.warning("kr_universe.kospi200_missing", path=str(path))
        return frozenset()
    out: set[str] = set()
    for raw in path.read_text("utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if len(line) == _KR_TICKER_PREFIX_LEN and line.isdigit():
            out.add(line)
    return frozenset(out)


def is_kr_ticker(ticker: str) -> bool:
    t = ticker.strip().upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        t = t[:-3]
    return len(t) == _KR_TICKER_PREFIX_LEN and t.isdigit()


def kr_canonical(ticker: str) -> str:
    t = ticker.strip().upper()
    if t.endswith(".KS") or t.endswith(".KQ"):
        return t[:-3]
    return t


KOSPI200_UNIVERSE: Final[frozenset[str]] = load_kospi200()


def is_kospi200(ticker: str) -> bool:
    return kr_canonical(ticker) in KOSPI200_UNIVERSE


__all__ = [
    "KOSPI200_UNIVERSE",
    "is_kospi200",
    "is_kr_ticker",
    "kr_canonical",
    "load_kospi200",
]
