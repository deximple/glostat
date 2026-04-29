# GLOSTAT — Validation Framework for Swing-Horizon Equity Alpha Theses

> Deterministic hindcast harness, snapshot-broker replay, kill criteria automation,
> and a free-stack data layer. Test your alpha thesis honestly before you trust it.

---

## Status

> **v0.6 archived (2026-04-29) — alpha absent on US megacaps.**
> Sprint 5 gate FAIL triggered automatic shutdown per `INV-GS-033`.
> See [`docs/post_mortem/SPRINT5_FAIL_post_mortem.md`](docs/post_mortem/SPRINT5_FAIL_post_mortem.md)
> for the honest diagnosis.
>
> **v0.7 in development** — the v0.6 alpha thesis (3-Expert formulaic composite on
> US megacaps) failed three sequential live evaluations with Sharpe ≤ 0 and AUC
> ~0.5. The infrastructure that *enabled* that honest finding — Snapshot Broker,
> Hindcast harness, Kill Criteria, Compliance Gate, DataRouter, free-stack
> clients — is independently sound and is now reframed as a public research-grade
> validation framework. v0.7 will run a 9-thesis empirical screen on top of it.

---

## What this project IS

- A **deterministic hindcast harness** that turns an alpha thesis into a
  PASS / AMBIGUOUS / FAIL gate with explicit Sharpe, AUC, OOS-degradation, and
  cost-pass-rate thresholds.
- A **snapshot broker** that persists every external data response (parquet
  shards + SQLite index + Merkle leaves) so any verdict can be replayed
  bit-for-bit months later.
- A **kill-criteria automation layer** that shuts a strategy down on its own
  invariant violations rather than waiting for hindsight.
- A **free-stack data router** (yfinance + SEC EDGAR) with phase-gated
  upgrades to paid sources, blocked at the code level until explicit user
  consent.
- A **compliance gate** that makes broadcast permanently impossible
  (`ComplianceError` on Telegram / mass-email entry points) and stamps a
  jurisdiction-aware personal-use disclaimer on every verdict.
- A **prompt registry** that pins each LLM call to a `sha256` so the prompt
  graph is auditable across versions.
- **40 numbered invariants** (`INV-GS-001..040`) with a 1:1 unit-test mapping
  and a machine-readable `configs/invariants.yaml`.

## What this project IS NOT

- **Not investment advice.** Use at your own risk. Read the post-mortem first.
- **Not a black-box predictor.** Every Verdict is an audit trail with sources.
- **Not a broadcast tool.** `broadcast_telegram` and `mass_email` are inert
  sentinels that always raise (`INV-GS-024`).
- **Not a multi-user product.** Personal use only.
- **Not a profitable trading system.** v0.6 was honestly tested and failed.
  The framework's value is the testing discipline, not a guaranteed alpha.

---

## Why use it

If you have an alpha thesis (technical, fundamental, behavioural,
event-driven, cross-asset, options-implied, anything), this framework lets you:

1. **Express the thesis as a small Python class** (an `Expert`) that returns a
   typed `ExpertSignal`.
2. **Plug it into a deterministic 90-day hindcast** with IS/OOS split,
   per-day verdict generation, and snapshot-replay reproducibility.
3. **Apply hard pass criteria** (Sharpe ≥ 0.8, AUC ≥ 0.62, OOS degradation ≤
   30%, cost-passed rate in [40%, 60%]) that you can tune but not silently
   weaken.
4. **Audit a verdict months later** by replaying the exact snapshot bytes and
   prompt versions used to issue it.
5. **Get an honest answer** — including the "your alpha is absent" answer.
   That answer is what cost the v0.6 plan its life cycle, and it is exactly
   what the framework is supposed to surface.

---

## Architecture overview

```
                ┌─────────────────────────────────────────────────────┐
                │                CLI / library entry                   │
                └─────────────────────────────────────────────────────┘
                                       │
                ┌──────────────────────┼──────────────────────┐
                ▼                      ▼                      ▼
        DataRouter             Compliance Gate         PromptRegistry
        (phase-gated)          (broadcast=ERROR)       (sha256 per call)
                │
   ┌────────────┼────────────┐
   ▼            ▼            ▼
yfinance    SEC EDGAR    Bigdata MCP   ← Phase 2+, blocked in MVP
   │            │            │
   └────────────┴────────────┘
                │
                ▼
        Snapshot Broker  ───►  parquet shards + SQLite index + Merkle leaves
                │
                ▼
            Experts  ───►  ExpertSignal (typed, immutable)
                │
                ▼
         Verdict Builder  ───►  Verdict (cost-gated, prompt-pinned)
                │
                ▼
          Hindcast Harness  ───►  HindcastReport
                │
                ▼
           Pass Criteria  ───►  PASS / AMBIGUOUS / FAIL
                │                       │
                └──────────► Kill Criteria (shutdown on violation)
```

### Snapshot broker

Every data response is hashed into a Merkle leaf, persisted as a parquet
shard, and indexed in SQLite:

```python
from datetime import UTC, datetime
from pathlib import Path

from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

broker = SnapshotBroker(root=Path("./snapshots"))
key = SnapshotKey(
    uaid="XNAS.AAPL",
    edge_type="tearsheet",
    ts_utc=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
    tool="yfinance.fundamentals",
    params_canon='{"period":"quarter"}',
)
record = broker.save_snapshot(key, {"per": 28.4, "roe": 0.21})
print(record.leaf.leaf_hash[:12], broker.audit_root()[:12])
broker.close()
```

### Hindcast + pass criteria

```python
from datetime import date

from glostat.replay.validation_harness import Hindcast, HindcastSplit, PassCriteria

split = HindcastSplit.from_range(date(2026, 1, 1), date(2026, 4, 1), ratio=0.7)
report = Hindcast(pipeline=my_pipeline, universe=("AAPL", "MSFT", "NVDA")).run(
    start_date=split.in_sample_start,
    end_date=split.out_sample_end,
)
print(PassCriteria().evaluate(report))  # → "PASS" | "AMBIGUOUS" | "FAIL"
```

### Compliance gate (cannot be bypassed)

```python
from glostat.risk.compliance_gate import broadcast_telegram, ComplianceContext

broadcast_telegram(
    ctx=ComplianceContext(user_profile_hash="0" * 64, jurisdiction="US"),
    chat_ids=["@anyone"], message="BUY AAPL",
)
# → glostat.risk.compliance_gate.ComplianceError: INV-GS-024 …
```

---

## Quickstart

Requires **Python 3.14**.

```bash
# clone + install (uv preferred)
git clone https://github.com/<you>/glostat.git
cd glostat
uv sync --extra dev

# verify
uv run pytest -q                                   # all unit tests
uv run python -c "import glostat; print(glostat.__version__)"

# issue a mock verdict (no network, bundled fixtures)
uv run glostat predict AAPL --mock

# canonical JSON output for downstream tooling
uv run glostat predict AAPL --mock --json
```

Live mode requires `GLOSTAT_SEC_USER_AGENT="Your Name your.email@yourdomain.com"`
(SEC EDGAR mandates a contactable User-Agent — `INV-GS-038`).

---

## Reusable for your own alpha thesis

The infrastructure is independent of the v0.6 thesis. To screen a new thesis:

1. **Write an Expert.** Subclass the Expert protocol in
   `src/glostat/experts/`, return an `ExpertSignal` with `direction`,
   `confidence`, `score`, `sources`. See
   [`docs/EXAMPLES.md`](docs/EXAMPLES.md) for a working template.
2. **Register a data source if needed.** Add a routing entry in
   `src/glostat/data/data_router.py` and a thin client in `src/glostat/data/`.
   The DataRouter enforces phase gating so paid sources stay blocked until
   you explicitly opt in.
3. **Define an invariant.** Add `INV-GS-NNN` to `configs/invariants.yaml` and
   a `@pytest.mark.invariant` test under `tests/`. The invariant is what
   keeps you honest when the strategy starts to drift.
4. **Run the hindcast.** Configure `PassCriteria`, point `Hindcast` at a
   universe, and let the gate decide. If it returns `FAIL`, your thesis is
   gone — that is the point.

Full walkthrough: [`docs/EXAMPLES.md`](docs/EXAMPLES.md).

---

## Repo layout

```
src/glostat/
  core/         # Verdict, ExpertSignal, MarketMeta, seeded RNG, errors
  data/         # snapshot broker, prompt registry, free-stack clients,
                # phase-gated DataRouter, entity map
  experts/     # E_FUNDAMENTAL, E_FUND_FLOW, E_TIME (+ extension template)
  gating/      # cost gate, anti-herd, regime gate
  replay/      # hindcast harness, kill criteria, sprint-4 gate, metrics
  risk/        # compliance gate (broadcast permanently forbidden)

configs/
  invariants.yaml    # 40 numbered invariants, 29 active in MVP
  budget.yaml        # phase-gated budget caps (mvp $0)
  markets.yaml       # XNAS + XNYS in MVP
  gating.yaml        # cost / regime / anti-herd parameters
  kill_criteria.yaml # automatic-shutdown thresholds
  universes/         # named universe definitions

tests/                    # pytest suite, INV-GS-* coverage, ~500 tests
docs/
  ssot/                   # immutable plan history v0.1 → v0.6
  post_mortem/            # honest Sprint 5 FAIL diagnosis
  research/               # design notes (snapshot broker, kill criteria, …)
  EXAMPLES.md             # extending the framework
```

---

## Honest reading order

If you are evaluating whether to adopt or fork this:

1. [`docs/post_mortem/SPRINT5_FAIL_post_mortem.md`](docs/post_mortem/SPRINT5_FAIL_post_mortem.md)
   — start here. Read the whole thing. The framework worked; the alpha didn't.
2. `docs/ssot/PLAN_v0.6.md` — the spec the v0.6 implementation honoured.
3. `configs/invariants.yaml` — the contract the framework enforces.
4. `docs/EXAMPLES.md` — practical extension recipes.

---

## Contributing

Issues and pull requests are welcome. Useful directions:

- **New Expert implementations** that screen a *different* thesis (event-driven,
  cross-asset momentum, options-implied, factor-based).
- **New data source clients** (Polygon free tier, Tiingo, Stooq, FRED) routed
  through `DataRouter` with phase gating.
- **New `PassCriteria` profiles** for thesis classes the v0.6 defaults
  (Sharpe-tilted, megacap-tuned) are wrong for.
- **Fixes / hardening** of the snapshot broker, hindcast harness, or
  compliance gate.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for coding style, INV-GS conventions,
and templates.

---

## License

MIT — see [`LICENSE`](LICENSE). Use it commercially, fork it, embed it, port
it, just keep the copyright notice.

---

## Citing

If this framework helps your research or post-mortem write-up, a link back to
this repository is appreciated.
