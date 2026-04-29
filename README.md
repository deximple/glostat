# GLOSTAT

> **Global Cascade Intelligence Engine — Validation-First MVP (v0.4)**
> Personal-use swing-horizon equity verdicts on US markets (XNAS + XNYS), grounded in Bigdata MCP signals, gated by a deterministic Merkle-leaf-keyed snapshot broker, and held to explicit kill criteria.

---

## What it is

GLOSTAT consumes Bigdata MCP fundamentals + fund-flow + events for a US equity, fuses three Experts (E_FUNDAMENTAL, E_FUND_FLOW, E_TIME) into a single `Verdict` (BUY / HOLD / SELL) with horizon 1–30 days, and emits it through a hard cost-gate (INV-GS-001) and compliance-gate (INV-GS-024). Every Bigdata response is persisted to a snapshot broker so any verdict can be replayed bit-for-bit (INV-GS-022). LLM calls carry a sha256 prompt fingerprint (INV-GS-023). The engine ships with a 90-day hindcast harness; Sprint 4 is a hard PASS-or-shutdown gate.

What it is **not**:
- not investment advice,
- not a broadcast tool (Telegram broadcast is permanently forbidden — it raises `ComplianceError`),
- not a multi-user deployment, and
- not a cascade-graph engine in MVP (Cascade Graph is Phase 3 research-mode).

Authoritative spec lives in `docs/ssot/PLAN_v0.4.md`. Plan revisions v0.1 → v0.4 are preserved verbatim for audit and learning.

---

## Install

GLOSTAT requires **Python 3.14**.

### with `uv` (preferred)

```bash
uv sync                              # production deps
uv sync --extra dev                  # plus pytest, ruff, mypy
```

### with `python -m venv`

```bash
python3.14 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Verify:

```bash
python -c "import glostat; print(glostat.__version__)"   # → 0.4.0
pytest -q
```

---

## Run example

Sprint 0 ships scaffolding — actual signal generation lands in Sprint 1. The harness pieces are usable today:

```python
from datetime import date
from pathlib import Path

from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey
from glostat.replay.validation_harness import Hindcast, PassCriteria
from glostat.risk.compliance_gate import ComplianceContext, assert_personal_use, disclaimer_for

# 1. compliance-gate a session (INV-GS-024)
ctx = ComplianceContext(user_profile_hash="0" * 64, jurisdiction="US")
assert_personal_use(ctx)
print(disclaimer_for(ctx.jurisdiction).render(
    ticker="AAPL", action="BUY", issued_at="2026-04-28T14:30:00Z",
))

# 2. write + replay a snapshot (INV-GS-022)
broker = SnapshotBroker(root=Path("./snapshots"))
key = SnapshotKey(
    uaid="XNAS.AAPL", edge_type="tearsheet",
    ts_utc=__import__("datetime").datetime(2026, 4, 28, 12, 0,
        tzinfo=__import__("datetime").timezone.utc),
    tool="bigdata_company_tearsheet",
    params_canon='{"period":"quarter","rp_entity_id":"4A6F00"}',
)
rec = broker.save_snapshot(key, {"per": 28.4, "roe": 0.21})
print("leaf:", rec.leaf.leaf_hash[:12], "audit_root:", broker.audit_root()[:12])
broker.close()

# 3. dry-run hindcast skeleton (real pipeline arrives Sprint 1)
report = Hindcast(pipeline=None, universe=("AAPL", "MSFT", "NVDA")).run(
    start_date=date(2026, 1, 1), end_date=date(2026, 4, 1),
)
print("gate:", PassCriteria().evaluate(report))    # FAIL while pipeline=None
```

Trying to broadcast raises immediately:

```python
from glostat.risk.compliance_gate import broadcast_telegram, ComplianceContext
broadcast_telegram(
    ctx=ComplianceContext(user_profile_hash="0"*64, jurisdiction="US"),
    chat_ids=["@anyone"], message="BUY AAPL",
)   # → glostat.risk.compliance_gate.ComplianceError: INV-GS-024: ...
```

---

## Scope discipline (read before contributing)

v0.4 is intentionally narrow. The following are **out of scope** until their gating decision is reached (see `CLAUDE.md` and `PLAN_v0.4.md` §8):

- Cascade Graph (Phase 3 only, gated on A/B Sharpe lift > 0.2)
- Markets beyond XNAS + XNYS (Phase 2 adds XKRX + XKOS)
- Cross-market cascade, intraday horizon, long-term horizon
- Telegram broadcast — **permanently forbidden**, INV-GS-024 enforced via `ComplianceError`
- Order execution — GLOSTAT emits verdicts only
- Multi-user deployment — personal use only

If a change appears to expand scope, stop and read `PLAN_v0.4.md` §8 ("v0.4가 명시적으로 NOT 하는 것") and §9 (kill criteria) first.

---

## Repo layout

```
src/glostat/{core,data,risk,replay}/   # production code
configs/{markets,invariants}.yaml      # MVP scope: 2 markets, 35 invariants
tests/test_invariants.py               # INV-GS unit coverage
docs/ssot/PLAN_v0.{1,2,3,4}.md         # immutable plan history
```

License: proprietary, personal use only.
