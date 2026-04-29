from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

# v1.0 — `glostat predict` outputs Prediction (probability format), not Verdict.

_CLI_MODULE: Final = "glostat.cli"


def _run(*args: str, cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", _CLI_MODULE, *args],
        cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout,
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    return tmp_path


# ── predict (Prediction format) ───────────────────────────────────────────


def test_predict_v1_mock_exits_zero(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr


def test_predict_v1_emits_prediction_header(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "GLOSTAT Prediction" in r.stdout
    # No more action keyword:
    assert "action            :" not in r.stdout


def test_predict_v1_prints_probability_line(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    # Looks like "up / down / sideways: 53.2% / 31.8% / 15.0%"
    assert "up / down / sideways" in r.stdout
    assert re.search(r"\d+\.\d+%\s*/\s*\d+\.\d+%\s*/\s*\d+\.\d+%", r.stdout)


def test_predict_v1_prints_expected_return_with_ci(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    assert "expected return" in r.stdout
    assert "CI:" in r.stdout
    assert "bps" in r.stdout


def test_predict_v1_prints_base_rate_and_edge(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    assert "base rate up" in r.stdout
    assert "edge over baseline" in r.stdout
    assert "pp" in r.stdout


def test_predict_v1_lists_contributing_signals(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    assert "Contributing signals" in r.stdout
    assert "AUC" in r.stdout


def test_predict_v1_includes_all_eleven_theses(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    for thesis in (
        "E_FUNDAMENTAL", "E_TIME", "E_FUND_FLOW",
        "E_SECTOR_ROTATION", "E_PEAD", "E_FOMC_DRIFT",
        "E_INSIDER_CLUSTER", "E_COMMODITY_TS", "E_FX_CARRY",
        "E_FUNDING_CARRY", "E_FOREIGN_REVERSAL",
    ):
        assert thesis in r.stdout, f"{thesis} missing from predict output"


def test_predict_v1_disclaimer_printed(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    assert "Personal use" in r.stdout or "personal use" in r.stdout
    assert "Not investment advice" in r.stdout or "not investment advice" in r.stdout


def test_predict_v1_evidence_hash_present(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    assert "evidence_hash" in r.stdout
    assert "sha256:" in r.stdout


def test_predict_v1_calibration_period_printed(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    assert "Calibration period" in r.stdout
    assert "2024" in r.stdout


def test_predict_v1_horizon_default_swing30d(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    assert "swing_30d" in r.stdout


def test_predict_v1_horizon_long3y(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", "--horizon", "long_3y", cwd=workdir)
    assert r.returncode == 0
    assert "long_3y" in r.stdout


def test_predict_v1_json_emits_canonical(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", "--json", cwd=workdir)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["ticker"] == "AAPL"
    assert "up_probability" in payload
    assert "down_probability" in payload
    assert "sideways_probability" in payload
    assert "expected_return_bps" in payload
    assert "edge_over_baseline_pp" in payload
    assert "contributing_signals" in payload
    assert payload["evidence_hash"]


def test_predict_v1_json_probabilities_sum_to_one(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", "--json", cwd=workdir)
    assert r.returncode == 0
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    total = (
        payload["up_probability"]
        + payload["down_probability"]
        + payload["sideways_probability"]
    )
    assert abs(total - 1.0) < 1e-6


def test_predict_v1_json_contains_disclaimer(workdir: Path) -> None:
    r = _run("predict", "AAPL", "--mock", "--json", cwd=workdir)
    assert r.returncode == 0
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert "disclaimer" in payload
    assert "investment advice" in payload["disclaimer"].lower()


def test_predict_v1_writes_snapshots(workdir: Path) -> None:
    _run("predict", "AAPL", "--mock", cwd=workdir)
    db = workdir / "cache" / "snapshots" / "index.sqlite"
    assert db.exists()


# ── calibrate ────────────────────────────────────────────────────────────


def test_calibrate_mock_runs(workdir: Path) -> None:
    r = _run("calibrate", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "GLOSTAT Calibrate" in r.stdout
    assert "synthetic_mock" in r.stdout


def test_calibrate_mock_lists_twelve_theses(workdir: Path) -> None:
    # v1.1 K1: 11 v1.0 + E_FUNDAMENTAL_KR.
    r = _run("calibrate", "--mock", cwd=workdir)
    assert r.returncode == 0
    assert "theses found : 12" in r.stdout


def test_calibrate_mock_writes_output(workdir: Path) -> None:
    out_path = workdir / "out.parquet"
    r = _run("calibrate", "--mock", "--out", str(out_path), cwd=workdir)
    assert r.returncode == 0, r.stderr
    # write_parquet may downgrade to .json fallback when polars is unavailable
    assert out_path.exists() or out_path.with_suffix(".json").exists()


def test_calibrate_no_mock_runs_with_repo_cache(workdir: Path) -> None:
    # WHY: when run from a fresh cwd without cache/, load_calibration returns empty
    # — the command should still succeed.
    r = _run("calibrate", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "GLOSTAT Calibrate" in r.stdout


# ── deprecated verdict surface still works ───────────────────────────────


def test_verdict_legacy_command_still_works(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "GLOSTAT Verdict" in r.stdout
    assert "action" in r.stdout
