from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import structlog
import yaml

from glostat.core.errors import ConfigError

# Universe loader (Sprint 1 PR #4).
# Reads configs/universes.yaml + the ticker file referenced by `source_file`.
# MVP active universe: US_LARGE_SAMPLE (50 megacap tickers, snapshot 2026-04-28).
# Phase 2/3 universes are listed in the YAML but raise ConfigError on load().

log: Final = structlog.get_logger(__name__)

_UNIVERSES_YAML: Final = Path(__file__).resolve().parents[3] / "configs" / "universes.yaml"
_REPO_ROOT: Final = Path(__file__).resolve().parents[3]

_ALLOWED_MARKETS: Final[frozenset[str]] = frozenset({"XNAS", "XNYS", "XKRX", "XKOS"})


@dataclass(frozen=True, slots=True)
class Universe:
    name: str
    description: str
    markets: tuple[str, ...]
    tickers: tuple[str, ...]
    size: int
    snapshot_date: str | None = None
    refresh_cadence: str | None = None

    def __post_init__(self) -> None:
        # WHY: declared size in YAML must match the actual ticker file rows.
        if len(self.tickers) != self.size:
            raise ConfigError(
                f"Universe {self.name!r}: declared size={self.size} "
                f"but ticker file contains {len(self.tickers)} rows."
            )
        for mic in self.markets:
            if mic not in _ALLOWED_MARKETS:
                raise ConfigError(
                    f"Universe {self.name!r}: market {mic!r} not in MVP scope "
                    f"(allowed: {sorted(_ALLOWED_MARKETS)})."
                )


def load_universe(name: str, *, yaml_path: Path | None = None) -> Universe:
    spec = _load_spec(name, yaml_path=yaml_path)
    deferred = spec.get("deferred_to")
    if deferred:
        raise ConfigError(
            f"Universe {name!r} is deferred to {deferred}. "
            f"MVP only supports US_LARGE_SAMPLE. "
            f"Activate after Sprint 4 gate PASS + explicit phase consent."
        )
    source_rel = spec.get("source_file")
    if not source_rel:
        raise ConfigError(f"Universe {name!r}: missing source_file in YAML.")
    source_path = _REPO_ROOT / str(source_rel)
    tickers = _read_ticker_file(source_path)
    markets_raw = spec.get("markets") or []
    markets = tuple(str(m).upper() for m in markets_raw)
    return Universe(
        name=name,
        description=str(spec.get("name", name)),
        markets=markets,
        tickers=tickers,
        size=int(spec.get("size", len(tickers))),
        snapshot_date=str(spec["snapshot_date"]) if "snapshot_date" in spec else None,
        refresh_cadence=str(spec.get("refresh_cadence", "")) or None,
    )


def list_universes(*, yaml_path: Path | None = None) -> list[str]:
    data = _load_yaml(yaml_path or _UNIVERSES_YAML)
    table = data.get("universes", {}) or {}
    return sorted(str(k) for k in table)


def list_active_universes(*, yaml_path: Path | None = None) -> list[str]:
    # WHY: separate from list_universes — useful for CLI to show only loadable names.
    data = _load_yaml(yaml_path or _UNIVERSES_YAML)
    table = data.get("universes", {}) or {}
    return sorted(
        str(k) for k, v in table.items() if not (v or {}).get("deferred_to")
    )


def _load_spec(name: str, *, yaml_path: Path | None) -> Mapping[str, object]:
    data = _load_yaml(yaml_path or _UNIVERSES_YAML)
    table = data.get("universes", {}) or {}
    spec = table.get(name)
    if spec is None:
        available = sorted(table.keys())
        raise ConfigError(
            f"Universe {name!r} not found in {yaml_path or _UNIVERSES_YAML}. "
            f"Available: {available}."
        )
    return spec


def _load_yaml(path: Path) -> Mapping[str, object]:
    if not path.exists():
        raise ConfigError(f"universes.yaml not found at {path}")
    try:
        return yaml.safe_load(path.read_text("utf-8")) or {}
    except yaml.YAMLError as exc:
        raise ConfigError(f"failed to parse {path}: {exc}") from exc


def _read_ticker_file(path: Path) -> tuple[str, ...]:
    if not path.exists():
        raise ConfigError(f"ticker file not found: {path}")
    rows: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text("utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        ticker = line.upper()
        if ticker in seen:
            log.warning("universe.duplicate_ticker", ticker=ticker, file=str(path))
            continue
        seen.add(ticker)
        rows.append(ticker)
    if not rows:
        raise ConfigError(f"ticker file is empty: {path}")
    return tuple(rows)


__all__ = [
    "Universe",
    "list_active_universes",
    "list_universes",
    "load_universe",
]
