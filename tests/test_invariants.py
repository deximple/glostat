from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from glostat import __version__
from glostat.core.types import (
    ExpertSignal,
    Verdict,
    VerdictIn,
    verdict_sha256,
    verdict_to_canonical_json,
)
from glostat.data.bigdata_client import (
    BigdataBudget,
    BigdataClient,
    BudgetExceededError,
)
from glostat.data.prompt_versioning import (
    PromptCollisionError,
    PromptRegistry,
    with_prompt_version,
)
from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey
from glostat.replay.validation_harness import Hindcast, HindcastSplit, PassCriteria
from glostat.risk.compliance_gate import (
    ComplianceContext,
    ComplianceError,
    assert_personal_use,
    broadcast_telegram,
    disclaimer_for,
    mass_email,
)

# ── Helpers ────────────────────────────────────────────────────────────────


_FIXED_NOW = datetime(2026, 4, 28, 14, 30, tzinfo=UTC)


def _signal(expert: str = "E_FUNDAMENTAL", ticker: str = "AAPL") -> ExpertSignal:
    return ExpertSignal(
        expert_name=expert,            # type: ignore[arg-type]
        ticker=ticker,
        direction="LONG",
        net_score=1.2,
        confidence=0.8,
        archetype="continuation",
        basis="EPS surprise +12%, fwd estimate trend up",
        sources=("rp:1234",),
        expires_at=_FIXED_NOW + timedelta(hours=4),
    )


def _verdict(
    *, action: str = "BUY", cost_passed: bool = True, ticker: str = "AAPL"
) -> Verdict:
    return Verdict(
        ticker=ticker,
        action=action,                 # type: ignore[arg-type]
        conviction_w=2.1,
        target_price=240.0,
        stop_price=210.0,
        suggested_size_pct=4.5,
        horizon_days=14,
        edge_bps=4.2,
        all_in_bps=0.59,
        cost_passed=cost_passed,
        expected_pnl_bps=3.6,
        disagreement_weight=0.78,
        contributing_signals=(_signal(),),
        next_trigger="institutional 5d net buy > +0.5σ",
        evidence_hash="a" * 64,
        prompt_versions=(("E_FUNDAMENTAL", "b" * 64),),
        git_commit="abc1234",
        user_profile_hash="c" * 64,
        issued_at=datetime(2026, 4, 28, 14, 30, tzinfo=UTC),
    )


# ── INV-GS-001: Cost gate ──────────────────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_001_cost_gate_rejects_buy_when_not_passed() -> None:
    """INV-GS-001: BUY/STRONG_BUY with cost_passed=False must raise."""
    with pytest.raises(ValueError, match="INV-GS-001"):
        _verdict(action="BUY", cost_passed=False)


@pytest.mark.invariant
def test_inv_gs_001_hold_allowed_when_cost_not_passed() -> None:
    v = _verdict(action="HOLD", cost_passed=False)
    assert v.cost_passed is False
    assert v.action == "HOLD"


@pytest.mark.invariant
def test_inv_gs_001_edge_ratio_threshold() -> None:
    """edge_bps must be ≥ 1.5 × all_in_bps; encoded by call sites, asserted here."""
    v = _verdict()
    assert v.edge_bps >= 1.5 * v.all_in_bps


# ── INV-GS-022: Snapshot determinism ───────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_022_snapshot_determinism(tmp_path: Path) -> None:
    """INV-GS-022: same (UAID, edge_type, ts, payload) → same Merkle leaf hash."""
    broker = SnapshotBroker(root=tmp_path / "snap")
    key = SnapshotKey(
        uaid="XNAS.AAPL",
        edge_type="tearsheet",
        ts_utc=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
        tool="bigdata_company_tearsheet",
        params_canon='{"period":"quarter","rp_entity_id":"4A6F00"}',
    )
    payload = {"per": 28.4, "roe": 0.21, "fwd_eps_trend": "up"}

    rec1 = broker.save_snapshot(key, payload)
    rec2 = broker.save_snapshot(key, payload)

    assert rec1.leaf.leaf_hash == rec2.leaf.leaf_hash
    replayed = broker.read_snapshot(rec1.leaf.leaf_hash)
    assert replayed == payload

    # Audit root over a single leaf is deterministic.
    root_a = broker.audit_root([rec1.leaf.leaf_hash])
    root_b = broker.audit_root([rec1.leaf.leaf_hash])
    assert root_a == root_b
    broker.close()


@pytest.mark.invariant
def test_inv_gs_022_payload_change_changes_leaf(tmp_path: Path) -> None:
    broker = SnapshotBroker(root=tmp_path / "snap2")
    key = SnapshotKey(
        uaid="XNAS.AAPL",
        edge_type="tearsheet",
        ts_utc=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
        tool="bigdata_company_tearsheet",
        params_canon='{"period":"quarter"}',
    )
    rec1 = broker.save_snapshot(key, {"per": 28.4})
    rec2 = broker.save_snapshot(key, {"per": 28.5})
    assert rec1.leaf.leaf_hash != rec2.leaf.leaf_hash
    broker.close()


# ── INV-GS-024: Compliance — broadcast prohibition ─────────────────────────


@pytest.mark.invariant
def test_inv_gs_024_broadcast_telegram_raises() -> None:
    """INV-GS-024: telegram broadcast must raise ComplianceError."""
    ctx = ComplianceContext(user_profile_hash="d" * 64, jurisdiction="US")
    with pytest.raises(ComplianceError, match="INV-GS-024"):
        broadcast_telegram(ctx=ctx, chat_ids=["@alice", "@bob"], message="BUY AAPL")


@pytest.mark.invariant
def test_inv_gs_024_mass_email_raises() -> None:
    ctx = ComplianceContext(user_profile_hash="d" * 64, jurisdiction="EU")
    with pytest.raises(ComplianceError, match="INV-GS-024"):
        mass_email(ctx=ctx, recipients=["a@x.com", "b@y.com"], subject="GLOSTAT verdict")


@pytest.mark.invariant
def test_inv_gs_024_personal_use_assertion() -> None:
    personal = ComplianceContext(user_profile_hash="e" * 64, jurisdiction="KR")
    assert_personal_use(personal)  # no exception

    commercial = ComplianceContext(
        user_profile_hash="e" * 64, jurisdiction="KR",
        personal_use_only=False, license_tier="commercial",
    )
    with pytest.raises(ComplianceError, match="INV-GS-024"):
        assert_personal_use(commercial)


@pytest.mark.invariant
def test_inv_gs_024_disclaimer_per_jurisdiction() -> None:
    for jx in ("KR", "US", "EU", "JP", "TW", "HK", "DEFAULT"):
        tmpl = disclaimer_for(jx)             # type: ignore[arg-type]
        rendered = tmpl.render(ticker="AAPL", action="BUY", issued_at="2026-04-28T14:30:00Z")
        assert "GLOSTAT" in rendered
        assert "INV-GS-024" in rendered


# ── INV-GS-023: Prompt versioning ──────────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_023_prompt_registry_immutable_versions() -> None:
    reg = PromptRegistry()
    reg.register(name="E_FUND.system", version="1.0.0", template="prompt v1")
    with pytest.raises(PromptCollisionError):
        reg.register(name="E_FUND.system", version="1.0.0", template="prompt v1 modified")


@pytest.mark.invariant
def test_inv_gs_023_decorator_stamps_prompt_versions() -> None:
    reg = PromptRegistry()
    reg.register(name="E_FUND.system", version="1.0.0", template="prompt v1")

    @with_prompt_version(registry=reg, expert="E_FUND", name="E_FUND.system", version="1.0.0")
    def call(*, prompt_versions: dict[str, str] | None = None) -> dict[str, str]:
        assert prompt_versions is not None
        return prompt_versions

    pv = call()
    assert "E_FUND" in pv
    assert len(pv["E_FUND"]) == 64


@pytest.mark.invariant
def test_inv_gs_023_verdict_rejects_empty_prompt_versions() -> None:
    good = _verdict()
    with pytest.raises(ValueError, match="INV-GS-023"):
        dataclasses.replace(good, prompt_versions=())


# ── INV-GS-010: Verdict determinism ────────────────────────────────────────


@pytest.mark.invariant
def test_inv_gs_010_verdict_canonical_json_stable() -> None:
    v1 = _verdict()
    v2 = _verdict()
    assert verdict_to_canonical_json(v1) == verdict_to_canonical_json(v2)
    assert verdict_sha256(v1) == verdict_sha256(v2)


# ── Bigdata client stubs raise as documented ───────────────────────────────


def test_bigdata_stubs_raise_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    # WHY: phase guard (INV-GS-036) blocks MVP — flip to phase_2 to reach the stub.
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_2")
    budget = BigdataBudget(monthly_call_cap=100, monthly_smart_cap=30)
    client = BigdataClient(budget=budget)
    with pytest.raises(NotImplementedError, match="MCP wired in S1"):
        asyncio.run(client.find_companies(query="Apple"))
    assert budget.used_calls == 1


def test_bigdata_budget_exhaustion(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GLOSTAT_PHASE", "phase_2")
    budget = BigdataBudget(monthly_call_cap=1, monthly_smart_cap=0)
    client = BigdataClient(budget=budget)
    with pytest.raises(NotImplementedError):
        asyncio.run(client.find_companies(query="Apple"))
    with pytest.raises(BudgetExceededError):
        asyncio.run(client.find_companies(query="Microsoft"))


# ── Pydantic boundary validator ────────────────────────────────────────────


def test_verdict_in_to_dataclass_roundtrip() -> None:
    payload = VerdictIn(
        ticker="AAPL",
        action="BUY",
        conviction_w=2.1,
        target_price=240.0,
        stop_price=210.0,
        suggested_size_pct=4.5,
        horizon_days=14,
        edge_bps=4.2,
        all_in_bps=0.59,
        cost_passed=True,
        expected_pnl_bps=3.6,
        disagreement_weight=0.78,
        contributing_signals=[
            {
                "expert_name": "E_FUNDAMENTAL",
                "ticker": "AAPL",
                "direction": "LONG",
                "net_score": 1.2,
                "confidence": 0.8,
                "archetype": "continuation",
                "basis": "EPS surprise +12%",
                "sources": ["rp:1234"],
                "expires_at": "2026-04-28T18:30:00Z",
                "metadata": {},
            }
        ],
        next_trigger="institutional 5d net buy > +0.5σ",
        evidence_hash="a" * 64,
        prompt_versions={"E_FUNDAMENTAL": "b" * 64},
        git_commit="abc1234",
        user_profile_hash="c" * 64,
        issued_at=datetime(2026, 4, 28, 14, 30, tzinfo=UTC),
    )
    v = payload.to_dataclass()
    assert v.ticker == "AAPL"
    assert v.action == "BUY"
    assert v.cost_passed is True


# ── Validation harness skeleton ────────────────────────────────────────────


def test_hindcast_split_70_30_invariant() -> None:
    s = HindcastSplit.from_range(date(2026, 1, 1), date(2026, 4, 1), ratio=0.7)
    assert s.in_sample_days >= s.out_sample_days
    total = s.in_sample_days + s.out_sample_days
    # ±2-day tolerance because of ceiling on integer days
    assert abs(total - 90) <= 2


def test_hindcast_stub_report_is_deterministic() -> None:
    h = Hindcast(pipeline=None, universe=("AAPL", "MSFT", "NVDA"))
    r1 = h.run(start_date=date(2026, 1, 1), end_date=date(2026, 4, 1))
    r2 = h.run(start_date=date(2026, 1, 1), end_date=date(2026, 4, 1))
    assert r1.seed == r2.seed
    assert PassCriteria().evaluate(r1) == "FAIL"


# ── Smoke ───────────────────────────────────────────────────────────────────


def test_version_string() -> None:
    assert __version__ == "1.5.0"

# v0.6 INV-GS-036..040 live in tests/test_invariants_v06.py to keep this file ≤ 400 lines.
