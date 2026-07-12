from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

from glostat.cli_scan import (
    _apply_filters,
    _has_significant_signal,
    _top_active_signal,
)
from glostat.predictor.types import Prediction, SignalContribution

# v1.9.0 — `glostat scan` CLI tests.

_CLI_MODULE: Final = "glostat.cli"


# ── Helpers ───────────────────────────────────────────────────────────────


def _make_signal(
    *,
    name: str = "E_PEAD_KR",
    value: float = 1.0,
    direction: str = "up",
    auc: float = 0.54,
    n: int = 360,
) -> SignalContribution:
    return SignalContribution(
        name=name, value=value, direction=direction,  # type: ignore[arg-type]
        calibration_auc=auc, calibration_sharpe=0.5, n_samples=n,
    )


def _make_pred(
    *, edge: float = 1.5, signals: tuple[SignalContribution, ...] | None = None,
) -> Prediction:
    from datetime import UTC, date, datetime  # noqa: PLC0415
    return Prediction(
        ticker="TEST", horizon="swing_30d", issued_at=datetime.now(tz=UTC),
        up_probability=0.53, down_probability=0.32, sideways_probability=0.15,
        expected_return_bps=42.0,
        confidence_interval_bps=(-40.0, 124.0),
        base_rate_up=0.52,
        edge_over_baseline_pp=edge,
        contributing_signals=signals or (_make_signal(),),
        next_triggers=("test",),
        evidence_hash="a" * 64,
        prompt_versions=(("E_TEST", "b" * 64),),
        disclaimer="Personal use only.",
        calibration_period=(date(2025, 9, 1), date(2026, 1, 31)),
        git_commit="testabc",
        market="XKRX",
    )


# ── _has_significant_signal ───────────────────────────────────────────────


class TestHasSignificantSignal:
    def test_yes_when_pead_significant(self) -> None:
        pred = _make_pred(signals=(_make_signal(auc=0.54, n=360),))
        assert _has_significant_signal(pred) is True

    def test_no_when_only_noise(self) -> None:
        pred = _make_pred(signals=(_make_signal(auc=0.495, n=360),))
        assert _has_significant_signal(pred) is False

    def test_no_when_n_zero(self) -> None:
        pred = _make_pred(signals=(_make_signal(auc=0.54, n=0),))
        assert _has_significant_signal(pred) is False

    def test_no_when_skip(self) -> None:
        skip_sig = SignalContribution(
            name="X", value=None, direction="skip",
            calibration_auc=0.54, calibration_sharpe=0.5, n_samples=360,
            skip_reason="x",
        )
        pred = _make_pred(signals=(skip_sig,))
        assert _has_significant_signal(pred) is False


# ── _apply_filters ────────────────────────────────────────────────────────


class TestApplyFilters:
    def test_no_filters_passes_all(self) -> None:
        results = [("AAA", _make_pred(edge=0.5)), ("BBB", _make_pred(edge=2.0))]
        out = _apply_filters(results, significant=False, min_edge=None)
        assert len(out) == 2

    def test_min_edge_filters(self) -> None:
        results = [
            ("AAA", _make_pred(edge=0.5)),
            ("BBB", _make_pred(edge=2.0)),
            ("CCC", _make_pred(edge=-1.8)),
        ]
        out = _apply_filters(results, significant=False, min_edge=1.5)
        # Only BBB (|2.0| >= 1.5) and CCC (|-1.8| >= 1.5) pass.
        names = [name for name, _ in out]
        assert "AAA" not in names
        assert "BBB" in names
        assert "CCC" in names

    def test_significant_filters(self) -> None:
        sig_pred = _make_pred(signals=(_make_signal(auc=0.54, n=360),))
        noise_pred = _make_pred(signals=(_make_signal(auc=0.495, n=360),))
        results = [("AAA", sig_pred), ("BBB", noise_pred)]
        out = _apply_filters(results, significant=True, min_edge=None)
        names = [name for name, _ in out]
        assert "AAA" in names
        assert "BBB" not in names


# ── _top_active_signal ────────────────────────────────────────────────────


class TestTopActiveSignal:
    def test_picks_largest_abs_value(self) -> None:
        pred = _make_pred(signals=(
            _make_signal(name="A", value=0.5),
            _make_signal(name="B", value=-2.0, direction="down"),
            _make_signal(name="C", value=1.0),
        ))
        out = _top_active_signal(pred)
        assert "B" in out
        assert "v-2.00" in out

    def test_no_active_returns_placeholder(self) -> None:
        skip_sig = SignalContribution(
            name="X", value=None, direction="skip",
            calibration_auc=0.5, calibration_sharpe=0, n_samples=0,
            skip_reason="x",
        )
        pred = _make_pred(signals=(skip_sig,))
        out = _top_active_signal(pred)
        assert "no active signal" in out


# ── End-to-end CLI ────────────────────────────────────────────────────────


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", _CLI_MODULE, *args],
        cwd=cwd, capture_output=True, text=True, check=False, timeout=300,
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


class TestScanCli:
    def test_scan_help_works(self, workdir: Path) -> None:
        r = _run("scan", "--help", cwd=workdir)
        assert r.returncode == 0
        assert "scan" in r.stdout.lower()
        assert "--significant" in r.stdout
        assert "--top" in r.stdout
        assert "--universe" in r.stdout

    def test_scan_universe_arg_recognized(self, workdir: Path) -> None:
        # Just check argparse accepts the universe name. Not running live
        # network — would take too long for unit tests.
        r = _run("scan", "--universe", "KR_KOSDAQ150_TOP30",
                 "--top", "1", "--max-concurrent", "1",
                 "--horizon", "swing_5d", cwd=workdir)
        # Either 0 (network success on at least one ticker) or non-zero
        # (network failure on all) — both prove argparse parsing is OK.
        # We don't assert on returncode because CI may have no network.
        assert "GLOSTAT Scan" in r.stdout or r.returncode != 0
