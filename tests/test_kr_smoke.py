from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

# Live KR smoke tests — exercises `glostat predict` end-to-end against yfinance
# and Naver. Skipped unless NETWORK_TESTS=1 to keep CI/local default fast.

_REPO_ROOT: Path = Path(__file__).resolve().parents[1]


def _network_required() -> bool:
    return os.environ.get("NETWORK_TESTS") == "1"


pytestmark = pytest.mark.skipif(
    not _network_required(),
    reason="set NETWORK_TESTS=1 for live KR smoke tests",
)


def _run_predict(ticker: str, *extra_args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.setdefault("GLOSTAT_SEC_USER_AGENT", "GLOSTAT (deximple@gmail.com)")
    env["NETWORK_TESTS"] = "1"
    args = [sys.executable, "-m", "glostat.cli", "predict", ticker, *extra_args]
    return subprocess.run(
        args, cwd=str(_REPO_ROOT), env=env,
        capture_output=True, text=True, timeout=180, check=False,
    )


def _count_active_signals(stdout: str) -> int:
    # WHY: parse the header `Contributing signals (active N / total M):` line
    # so the smoke test asserts on what the user actually sees.
    for line in stdout.splitlines():
        if "Contributing signals (active" in line:
            try:
                left = line.split("(active")[1].split("/")[0].strip()
                return int(left)
            except (IndexError, ValueError):
                continue
    return 0


def test_predict_sk_innovation_has_three_active_signals() -> None:
    r = _run_predict("096770")
    assert r.returncode == 0, f"stderr: {r.stderr}"
    active = _count_active_signals(r.stdout)
    # E_FUNDAMENTAL_KR + E_FOREIGN_REVERSAL + E_TIME → 3.
    assert active >= 3, (
        f"expected ≥3 active signals, got {active}\n--- stdout ---\n{r.stdout}"
    )
    assert "096770" in r.stdout
    assert "XKRX" in r.stdout
    # Edge_pp must be non-zero (signals are influencing the prediction).
    assert "edge over baseline:  +0.0pp" not in r.stdout


def test_predict_samsung_has_three_active_signals() -> None:
    r = _run_predict("005930")
    assert r.returncode == 0, f"stderr: {r.stderr}"
    active = _count_active_signals(r.stdout)
    assert active >= 3, (
        f"expected ≥3 active signals, got {active}\n--- stdout ---\n{r.stdout}"
    )
    assert "005930" in r.stdout
    assert "XKRX" in r.stdout


def test_predict_sk_innovation_does_not_show_baseline_fallback() -> None:
    # The baseline fallback (no contributing signals → 50/50) used to print
    # "active 0 / total 11". v1.1 K1 must avoid that for KOSPI 200 megacaps.
    r = _run_predict("096770")
    assert "active 0" not in r.stdout, (
        f"baseline fallback path still triggered:\n{r.stdout}"
    )


def test_predict_aapl_still_works_after_kr_changes() -> None:
    # Regression: K1 changes should not break the v1.0 US prediction surface.
    r = _run_predict("AAPL")
    assert r.returncode == 0, f"stderr: {r.stderr}"
    assert "AAPL" in r.stdout
    assert "XNAS" in r.stdout
