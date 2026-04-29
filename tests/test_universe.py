from __future__ import annotations

from pathlib import Path

import pytest

from glostat.core.errors import ConfigError
from glostat.data.universe import (
    Universe,
    list_active_universes,
    list_universes,
    load_universe,
)

# Sprint 1 PR #4 — universe loader tests.

_REPO_ROOT = Path(__file__).resolve().parents[1]
_REAL_YAML = _REPO_ROOT / "configs" / "universes.yaml"


def test_load_universe_default() -> None:
    u = load_universe("US_LARGE_SAMPLE")
    assert u.name == "US_LARGE_SAMPLE"
    assert u.size == 50
    assert len(u.tickers) == 50
    assert "AAPL" in u.tickers
    assert "BRK.B" in u.tickers
    assert u.markets == ("XNAS", "XNYS")


def test_universe_immutable_frozen() -> None:
    u = load_universe("US_LARGE_SAMPLE")
    with pytest.raises((AttributeError, Exception)):
        u.tickers = ()  # type: ignore[misc]


def test_universe_tickers_unique() -> None:
    u = load_universe("US_LARGE_SAMPLE")
    assert len(u.tickers) == len(set(u.tickers))


def test_deferred_universe_raises_configerror() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_universe("US_LARGE_500")
    assert "deferred" in str(exc_info.value).lower()
    assert "phase_2" in str(exc_info.value)


def test_deferred_phase_3_universe_raises_configerror() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_universe("US_RUSSELL_3000")
    assert "deferred" in str(exc_info.value).lower()
    assert "phase_3" in str(exc_info.value)


def test_unknown_universe_name_raises() -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_universe("DOES_NOT_EXIST")
    assert "not found" in str(exc_info.value).lower()


def test_list_universes_includes_phase2_and_phase3() -> None:
    names = list_universes()
    assert "US_LARGE_SAMPLE" in names
    assert "US_LARGE_500" in names
    assert "US_RUSSELL_3000" in names


def test_list_active_universes_excludes_deferred() -> None:
    active = list_active_universes()
    assert "US_LARGE_SAMPLE" in active
    assert "US_LARGE_500" not in active
    assert "US_RUSSELL_3000" not in active


def test_universe_size_mismatch_raises(tmp_path: Path) -> None:
    yaml_path = tmp_path / "universes.yaml"
    ticker_file = tmp_path / "small.txt"
    ticker_file.write_text("AAPL\nMSFT\n")
    yaml_path.write_text(
        "schema_version: 1\n"
        "universes:\n"
        "  TINY:\n"
        "    name: 'Tiny'\n"
        "    markets: [XNAS]\n"
        f"    source_file: {ticker_file.relative_to(tmp_path)}\n"
        "    size: 50\n"
    )
    # WHY: the loader resolves source_file relative to repo root, so plant the
    # ticker file at that resolved path for this test.
    spec_file_path = _REPO_ROOT / ticker_file.relative_to(tmp_path)
    spec_file_path.parent.mkdir(parents=True, exist_ok=True)
    spec_file_path.write_text("AAPL\nMSFT\n")
    try:
        with pytest.raises(ConfigError) as exc_info:
            load_universe("TINY", yaml_path=yaml_path)
        assert "size=50" in str(exc_info.value)
    finally:
        spec_file_path.unlink(missing_ok=True)


def test_universe_invalid_market_raises(tmp_path: Path) -> None:
    yaml_path = tmp_path / "universes.yaml"
    ticker_file = tmp_path / "tiny.txt"
    ticker_file.write_text("AAPL\nMSFT\nNVDA\n")
    yaml_path.write_text(
        "schema_version: 1\n"
        "universes:\n"
        "  KR_TEST:\n"
        "    name: 'KR'\n"
        "    markets: [XKRX]\n"
        f"    source_file: {ticker_file.relative_to(tmp_path)}\n"
        "    size: 3\n"
    )
    spec_file_path = _REPO_ROOT / ticker_file.relative_to(tmp_path)
    spec_file_path.parent.mkdir(parents=True, exist_ok=True)
    spec_file_path.write_text("AAPL\nMSFT\nNVDA\n")
    try:
        with pytest.raises(ConfigError) as exc_info:
            load_universe("KR_TEST", yaml_path=yaml_path)
        assert "XKRX" in str(exc_info.value)
    finally:
        spec_file_path.unlink(missing_ok=True)


def test_universe_dataclass_fields() -> None:
    u = load_universe("US_LARGE_SAMPLE")
    assert isinstance(u, Universe)
    assert u.snapshot_date == "2026-04-28"
    assert u.refresh_cadence == "quarterly"


def test_ticker_file_strips_whitespace_and_comments(tmp_path: Path) -> None:
    yaml_path = tmp_path / "universes.yaml"
    ticker_file = tmp_path / "tiny.txt"
    ticker_file.write_text("# header comment\n\nAAPL\n  MSFT  \n# footer\nNVDA\n")
    yaml_path.write_text(
        "schema_version: 1\n"
        "universes:\n"
        "  TINY:\n"
        "    name: 'Tiny'\n"
        "    markets: [XNAS]\n"
        f"    source_file: {ticker_file.relative_to(tmp_path)}\n"
        "    size: 3\n"
    )
    spec_file_path = _REPO_ROOT / ticker_file.relative_to(tmp_path)
    spec_file_path.parent.mkdir(parents=True, exist_ok=True)
    spec_file_path.write_text("# header comment\n\nAAPL\n  MSFT  \n# footer\nNVDA\n")
    try:
        u = load_universe("TINY", yaml_path=yaml_path)
        assert u.tickers == ("AAPL", "MSFT", "NVDA")
    finally:
        spec_file_path.unlink(missing_ok=True)
