from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Final

import pytest

# Tests invoke `glostat` via the same Python interpreter so coverage tracking and
# uv-managed deps stay consistent across the harness.
_CLI_MODULE: Final = "glostat.cli"
_REPO_ROOT: Final = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", _CLI_MODULE, *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    # WHY: cwd-isolated cache directory so tests don't pollute each other.
    return tmp_path


# ── predict --mock ─────────────────────────────────────────────────────────


# v1.0 reframe: legacy `predict → Verdict` tests migrated to `verdict` subcommand.
# `predict` now outputs Prediction (probability + evidence) — covered by
# tests/test_cli_predict_v1.py.


def test_predict_aapl_mock_exits_zero(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "AAPL" in r.stdout
    assert re.search(r"action\s*:\s*(BUY|HOLD|SELL)", r.stdout)


def test_predict_disclaimer_printed(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0
    assert "personal use" in r.stdout.lower()
    assert "INV-GS-024" in r.stdout


def test_predict_writes_snapshots(workdir: Path) -> None:
    _run("verdict", "AAPL", "--mock", cwd=workdir)
    db = workdir / "cache" / "snapshots" / "index.sqlite"
    assert db.exists()
    shards_dir = workdir / "cache" / "snapshots" / "shards"
    assert shards_dir.exists()
    assert any(shards_dir.iterdir())


def test_predict_json_emits_canonical(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", "--json", cwd=workdir)
    assert r.returncode == 0
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["ticker"] == "AAPL"
    assert payload["action"] in {"BUY", "HOLD", "SELL"}
    assert len(payload["evidence_hash"]) == 64


def test_predict_records_verdict_for_replay(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", "--json", cwd=workdir)
    assert r.returncode == 0
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    replay = _run("replay", payload["evidence_hash"], cwd=workdir)
    assert replay.returncode == 0, replay.stderr
    assert "True" in replay.stdout


# ── replay ─────────────────────────────────────────────────────────────────


def test_replay_unknown_hash_returns_nonzero(workdir: Path) -> None:
    r = _run("replay", "0" * 64, cwd=workdir)
    assert r.returncode != 0


# ── audit ──────────────────────────────────────────────────────────────────


def test_audit_returns_merkle_root_string(workdir: Path) -> None:
    _run("verdict", "AAPL", "--mock", cwd=workdir)
    r = _run("audit", "2026-04-28", cwd=workdir)
    assert r.returncode == 0
    output = r.stdout.strip()
    # 64-char hex Merkle root expected.
    assert re.fullmatch(r"[0-9a-f]{64}", output)


# ── status ─────────────────────────────────────────────────────────────────


def test_status_prints_version_and_phase(workdir: Path) -> None:
    r = _run("status", cwd=workdir)
    assert r.returncode == 0
    assert "1.4.1" in r.stdout
    assert "phase" in r.stdout.lower()
    assert "snapshots" in r.stdout.lower()


def test_status_phase_default_mvp(workdir: Path) -> None:
    r = _run("status", cwd=workdir)
    assert r.returncode == 0
    assert "mvp" in r.stdout


# ── error paths ────────────────────────────────────────────────────────────


def test_no_command_prints_help(workdir: Path) -> None:
    r = _run(cwd=workdir)
    assert r.returncode == 2  # argparse convention for missing command
    assert "predict" in r.stdout or "predict" in r.stderr


def test_unknown_ticker_in_mock_mode_errors(workdir: Path) -> None:
    r = _run("verdict", "NOTAREALSTOCK", "--mock", cwd=workdir)
    assert r.returncode != 0


# ── Sprint 1 PR #2: --expert flag + multi-signal aggregation (legacy verdict) ─


def test_predict_expert_time_only(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", "--expert", "time", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "E_TIME" in r.stdout
    assert "E_FUNDAMENTAL" not in r.stdout


def test_predict_expert_fundamental_only(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", "--expert", "fundamental", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "E_FUNDAMENTAL" in r.stdout
    assert "E_TIME" not in r.stdout


def test_predict_all_experts_default_two_signals(workdir: Path) -> None:
    # Default --expert all → both Experts contribute.
    r = _run("verdict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "E_FUNDAMENTAL" in r.stdout
    assert "E_TIME" in r.stdout


def test_predict_all_experts_records_more_snapshots(workdir: Path) -> None:
    _run("verdict", "AAPL", "--mock", cwd=workdir)
    db = workdir / "cache" / "snapshots" / "index.sqlite"
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        n = int(conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
    finally:
        conn.close()
    # Expect ≥ 5: 2 from E_FUNDAMENTAL (fundamentals + ticker_cik + facts = 3)
    # + 2 from E_TIME (ohlcv + earnings_calendar) = 5 minimum.
    assert n >= 5


def test_predict_inv_gs_008_metadata_visible_in_json(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", "--expert", "time", "--json", cwd=workdir)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    sigs = payload["contributing_signals"]
    e_time = next(s for s in sigs if s["expert_name"] == "E_TIME")
    md = dict(e_time["metadata"])
    assert "bonus_eligible_T" in md
    assert md["bonus_eligible_T"] == "True"  # fixture engineered for T=2.0


# ── Sprint 1 PR #3: E_FUND_FLOW Expert (legacy verdict) ───────────────────


def test_predict_expert_fund_flow_only_skips_in_mock(workdir: Path) -> None:
    # Sprint 5 PR #1: E_FUND_FLOW alone in fresh-broker mock mode skips
    # (no prior snapshot) so build_verdict raises and the CLI exits non-zero.
    r = _run("verdict", "AAPL", "--mock", "--expert", "fund_flow", cwd=workdir)
    assert r.returncode != 0


def test_predict_all_experts_includes_at_least_two(workdir: Path) -> None:
    # Sprint 5 PR #1: E_FUND_FLOW skips on fresh-broker mock; E_FUNDAMENTAL +
    # E_TIME still produce signals so the verdict prints at least two experts.
    r = _run("verdict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "E_FUNDAMENTAL" in r.stdout
    assert "E_TIME" in r.stdout


def test_predict_two_or_three_expert_signal_count(workdir: Path) -> None:
    # Sprint 5 PR #1: E_FUND_FLOW now needs a prior holders snapshot (cross-day
    # delta), so single-shot mock verdict skips that expert. E_FUNDAMENTAL and
    # E_TIME still emit, so a 2-signal verdict is the new mock baseline.
    r = _run("verdict", "AAPL", "--mock", "--json", cwd=workdir)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert len(payload["contributing_signals"]) >= 2
    names = {s["expert_name"] for s in payload["contributing_signals"]}
    assert {"E_FUNDAMENTAL", "E_TIME"}.issubset(names)


def test_predict_records_snapshots(workdir: Path) -> None:
    _run("verdict", "AAPL", "--mock", cwd=workdir)
    db = workdir / "cache" / "snapshots" / "index.sqlite"
    assert db.exists()
    conn = sqlite3.connect(db)
    try:
        n = int(conn.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0])
    finally:
        conn.close()
    # Sprint 5 PR #1: 3 from E_FUNDAMENTAL + 2 from E_TIME + 1 from
    # E_FUND_FLOW (holders fetch, even though that expert ultimately skips).
    assert n >= 6


# ── Sprint 1 PR #4: universe + screen subcommands ──────────────────────────


def test_universe_list_command(workdir: Path) -> None:
    r = _run("universe", "list", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "US_LARGE_SAMPLE" in r.stdout


def test_universe_build_mock_command(workdir: Path) -> None:
    r = _run("universe", "build", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "tickers indexed: 50" in r.stdout
    assert "Technology" in r.stdout
    assert "PER med=" in r.stdout


def test_universe_build_writes_entity_map(workdir: Path) -> None:
    _run("universe", "build", "--mock", cwd=workdir)
    em = workdir / "cache" / "entity_map.parquet"
    assert em.exists()


def test_universe_build_writes_sector_stats_cache(workdir: Path) -> None:
    _run("universe", "build", "--mock", cwd=workdir)
    cache = workdir / "cache" / "sector_stats.parquet"
    assert cache.exists()


def test_screen_us_large_sample_mock(workdir: Path) -> None:
    r = _run("screen", "US_LARGE_SAMPLE", "--mock", "--top", "10", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "US_LARGE_SAMPLE" in r.stdout
    assert "processed=50" in r.stdout
    # Expect personal-use disclaimer (INV-GS-024)
    assert "personal use" in r.stdout.lower()
    assert "INV-GS-024" in r.stdout


def test_screen_top_5_returns_at_most_5(workdir: Path) -> None:
    r = _run("screen", "US_LARGE_SAMPLE", "--mock", "--top", "5", cwd=workdir)
    assert r.returncode == 0, r.stderr
    row_lines = re.findall(r"^\s+\d+\s+[A-Z]", r.stdout, flags=re.MULTILINE)
    assert len(row_lines) <= 5


def test_screen_json_output(workdir: Path) -> None:
    r = _run("screen", "US_LARGE_SAMPLE", "--mock", "--top", "3", "--json", cwd=workdir)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert payload["universe"] == "US_LARGE_SAMPLE"
    assert "rows" in payload
    assert len(payload["rows"]) <= 3


def test_screen_unknown_universe_errors(workdir: Path) -> None:
    r = _run("screen", "DOES_NOT_EXIST", "--mock", cwd=workdir)
    assert r.returncode != 0


def test_screen_deferred_universe_errors(workdir: Path) -> None:
    r = _run("screen", "US_LARGE_500", "--mock", cwd=workdir)
    assert r.returncode != 0
    assert "deferred" in (r.stderr + r.stdout).lower()


# ── Sprint 1 PR #5: Gating breakdown printed in legacy verdict ────────────


def test_predict_displays_gating_breakdown(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "Gating:" in r.stdout
    assert "E_FUND" in r.stdout
    assert "E_TIME" in r.stdout
    assert "anti_herd" in r.stdout
    assert "minority_premium" in r.stdout


def test_predict_gating_anti_herd_off_at_3_experts(workdir: Path) -> None:
    r = _run("verdict", "AAPL", "--mock", cwd=workdir)
    assert r.returncode == 0, r.stderr
    # 3 experts in MVP — anti-herd doesn't trigger (threshold = 4).
    assert "anti_herd        : OFF" in r.stdout


# ── Sprint 4 PR #1: hindcast + gate-status subcommands ────────────────────


def test_hindcast_command_mock_runs(workdir: Path) -> None:
    r = _run(
        "hindcast", "--start", "2026-01-29", "--end", "2026-04-28", "--mock",
        cwd=workdir, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert "Sprint 4 Gate" in r.stdout
    assert "PASS" in r.stdout


def test_hindcast_outputs_metrics_table(workdir: Path) -> None:
    r = _run(
        "hindcast", "--start", "2026-01-29", "--end", "2026-04-28", "--mock",
        cwd=workdir, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert "Hindcast Metrics" in r.stdout
    assert "sharpe" in r.stdout
    assert "auc" in r.stdout
    assert "oos_degradation" in r.stdout
    assert "cost_passed pct" in r.stdout


def test_hindcast_outputs_gate_decision(workdir: Path) -> None:
    r = _run(
        "hindcast", "--start", "2026-01-29", "--end", "2026-04-28", "--mock",
        cwd=workdir, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert "Sprint 4 Gate" in r.stdout
    assert "Kill Criteria Decision" in r.stdout
    assert "v0.3.1 pivot eligible" in r.stdout


def test_hindcast_writes_report_files(workdir: Path) -> None:
    r = _run(
        "hindcast", "--start", "2026-01-29", "--end", "2026-04-28", "--mock",
        cwd=workdir, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    cache_dir = workdir / "cache" / "hindcast"
    assert cache_dir.exists()
    json_files = list(cache_dir.glob("sprint4_*_report.json"))
    md_files = list(cache_dir.glob("sprint4_*_report.md"))
    assert len(json_files) >= 1
    assert len(md_files) >= 1
    # Verify JSON is well-formed.
    payload = json.loads(json_files[0].read_text("utf-8"))
    assert payload["report"]["n_verdicts"] > 0
    assert payload["gate"]["pass_status"] in {"PASS", "FAIL", "AMBIGUOUS"}


def test_hindcast_json_output(workdir: Path) -> None:
    r = _run(
        "hindcast", "--start", "2026-01-29", "--end", "2026-04-28", "--mock",
        "--json", cwd=workdir, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout.strip().splitlines()[-1])
    assert "report" in payload
    assert "gate" in payload
    assert "kill" in payload


def test_hindcast_invalid_dates_exits_two(workdir: Path) -> None:
    r = _run(
        "hindcast", "--start", "2026-04-28", "--end", "2026-01-29", "--mock",
        cwd=workdir,
    )
    assert r.returncode == 2


def test_hindcast_live_mode_is_wired_pr2(workdir: Path) -> None:
    # Sprint 4 PR #2 wires live mode. Without NETWORK_TESTS we still don't
    # exercise yfinance; SEC EDGAR will reject the placeholder User-Agent and
    # the run aborts with a non-zero exit. Either way: not the PR #1 stub
    # message, and the live path is reachable.
    r = _run(
        "hindcast", "--start", "2026-04-25", "--end", "2026-04-27",
        "--tickers", "AAPL", "--max-concurrent", "1",
        cwd=workdir, timeout=120,
    )
    assert r.returncode != 0
    combined = r.stderr + r.stdout
    assert "Sprint 4 PR #2" not in combined
    # Live mode either aborts (no real User-Agent → ConfigError surfaced via
    # `hindcast.run unexpected: ...`) or runs and emits a SHUTDOWN gate.
    assert "ABORTED" in combined or "SHUTDOWN" in combined or "FAIL" in combined


def test_gate_status_reads_cached_report(workdir: Path) -> None:
    _run("hindcast", "--start", "2026-01-29", "--end", "2026-04-28", "--mock",
         cwd=workdir, timeout=120)
    r = _run("gate-status", cwd=workdir)
    assert r.returncode == 0, r.stderr
    assert "pass_status" in r.stdout
    assert "decision" in r.stdout


def test_gate_status_no_reports_returns_nonzero(workdir: Path) -> None:
    r = _run("gate-status", cwd=workdir)
    assert r.returncode != 0


def test_hindcast_balanced_profile_runs(workdir: Path) -> None:
    r = _run(
        "hindcast", "--start", "2026-01-29", "--end", "2026-04-28",
        "--mock", "--profile", "balanced", cwd=workdir, timeout=120,
    )
    assert r.returncode == 0, r.stderr
    assert "balanced" in r.stdout
