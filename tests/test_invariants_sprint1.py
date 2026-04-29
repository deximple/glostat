from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from glostat.cli_mocks import MockSecEdgarClient, MockYFinanceClient
from glostat.core.types import ExpertSignal, MarketMeta, SessionWindow
from glostat.data.data_router import DataRouter
from glostat.data.snapshot_broker import SnapshotBroker
from glostat.experts import EFundamentalExpert
from glostat.verdict_builder import build_verdict
from tests.fixtures import load_fixture

# Sprint 1 PR #1 invariants — assertions exercised end-to-end on AAPL mock pipeline.

_NOW = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _xnas() -> MarketMeta:
    return MarketMeta(
        mic="XNAS", name="NASDAQ", country="US", currency="USD",
        tz="America/New_York",
        sessions=(SessionWindow("regular", "09:30", "16:00", "14:30", "21:00"),),
        settlement_days=1, fee_bps=0.6, tax_bps_buy=0.0, tax_bps_sell=0.24,
        tick_size="1c", holidays_calendar="us_2026.yaml",
        bigdata_mcp_coverage="HIGH", foreign_access="open",
    )


def _build_aapl_pipeline(broker: SnapshotBroker) -> tuple[EFundamentalExpert, dict]:
    fixture = load_fixture("aapl_mock.json")
    yf = MockYFinanceClient(broker=broker, fixture=fixture)
    sec = MockSecEdgarClient(broker=broker, fixture=fixture)
    router = DataRouter()
    router.register_client("yfinance", yf)
    router.register_client("sec_edgar", sec)
    return EFundamentalExpert(router=router), fixture


# ── INV-GS-001: Cost gate enforced on real verdict ─────────────────────────


@pytest.mark.invariant
def test_inv_gs_001_cost_gate_enforced_on_aapl_mock(tmp_path: Path) -> None:
    broker = SnapshotBroker(root=tmp_path / "snap")
    expert, fixture = _build_aapl_pipeline(broker)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    v = build_verdict(
        ticker="AAPL",
        signals=[sig],
        market_meta=_xnas(),
        ts=_NOW,
        prompt_versions={},
        current_price=fixture.get("current_price"),
    )
    if v.action == "BUY":
        # When BUY emitted, cost-gate must have passed (INV-GS-001 asserted in __post_init__).
        assert v.cost_passed is True
        assert v.edge_bps >= 1.5 * v.all_in_bps
    broker.close()


@pytest.mark.invariant
def test_inv_gs_001_demotion_when_edge_too_small(tmp_path: Path) -> None:
    broker = SnapshotBroker(root=tmp_path / "snap")
    weak = ExpertSignal(
        expert_name="E_FUNDAMENTAL", ticker="AAPL",
        direction="LONG", net_score=0.005, confidence=0.05,
        archetype="continuation", basis="weak",
        sources=("yfinance.info#xx",),
        expires_at=_NOW + timedelta(days=30),
    )
    v = build_verdict(
        ticker="AAPL", signals=[weak], market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    assert v.cost_passed is False
    assert v.action == "HOLD"
    broker.close()


# ── INV-GS-022: evidence_hash matches snapshot Merkle leaves ───────────────


@pytest.mark.invariant
def test_inv_gs_022_evidence_hash_uses_snapshot_leaves(tmp_path: Path) -> None:
    broker = SnapshotBroker(root=tmp_path / "snap")
    expert, _ = _build_aapl_pipeline(broker)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    v = build_verdict(
        ticker="AAPL", signals=[sig], market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    # Re-running the pipeline + builder with the same fixtures yields the same hash.
    broker2 = SnapshotBroker(root=tmp_path / "snap2")
    expert2, _ = _build_aapl_pipeline(broker2)
    sig2 = asyncio.run(expert2.compute("AAPL", _NOW))
    v2 = build_verdict(
        ticker="AAPL", signals=[sig2], market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    assert v.evidence_hash == v2.evidence_hash
    assert len(v.evidence_hash) == 64
    broker.close()
    broker2.close()


# ── INV-GS-023: prompt_versions populated even for non-LLM expert ──────────


@pytest.mark.invariant
def test_inv_gs_023_prompt_versions_populated(tmp_path: Path) -> None:
    broker = SnapshotBroker(root=tmp_path / "snap")
    expert, _ = _build_aapl_pipeline(broker)
    sig = asyncio.run(expert.compute("AAPL", _NOW))
    v = build_verdict(
        ticker="AAPL", signals=[sig], market_meta=_xnas(), ts=_NOW,
        prompt_versions={},
    )
    pv = dict(v.prompt_versions)
    assert "E_FUNDAMENTAL" in pv
    assert len(pv["E_FUNDAMENTAL"]) == 64
    broker.close()


# ── INV-GS-024: CLI output contains personal-use disclaimer ────────────────


@pytest.mark.invariant
def test_inv_gs_024_cli_disclaimer_in_output(tmp_path: Path) -> None:
    r = subprocess.run(
        [sys.executable, "-m", "glostat.cli", "predict", "AAPL", "--mock"],
        cwd=tmp_path,
        capture_output=True, text=True, check=False, timeout=30,
    )
    assert r.returncode == 0
    assert "personal use" in r.stdout.lower()
    assert "INV-GS-024" in r.stdout


# ── INV-GS-010: same inputs → same verdict ─────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_010_pipeline_deterministic(tmp_path: Path) -> None:
    # Two predict --mock invocations against the same fixture must produce the
    # same evidence_hash. issued_at differs (datetime.now()) but evidence_hash
    # is a function of source snapshot ids, which are deterministic.
    parsed = []
    for i in range(2):
        rundir = tmp_path / f"run_{i}"
        rundir.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(
            [sys.executable, "-m", "glostat.cli", "predict", "AAPL", "--mock", "--json"],
            cwd=rundir,
            capture_output=True, text=True, check=False, timeout=30,
        )
        assert r.returncode == 0, r.stderr
        parsed.append(json.loads(r.stdout.strip().splitlines()[-1]))
    assert parsed[0]["evidence_hash"] == parsed[1]["evidence_hash"]
