# GLOSTAT — Claude Code Project Context

> **STATUS: ACTIVE v1.2 — KR calibration (L1) + DART API (L2) landed.**
> Previous: v1.1 (2026-04-29) — KR (KOSPI 200) production support; v1.0 (2026-04-29)
> Prediction Tool reframe of v0.7.
> v1.2 delta: `glostat kr-hindcast` produces real Phase KR calibration
> (replaces n=0 bootstraps for E_FUNDAMENTAL_KR / E_TIME_KR / E_FOREIGN_REVERSAL);
> new `dart_client.DartClient` + `EInsiderKrExpert` (KR equivalent of SEC Form 4
> insider cluster); E_FUNDAMENTAL_KR DART overlay when `GLOSTAT_DART_API_KEY`
> is set. New invariant `INV-GS-107` (DART graceful skip).
>
> Honest reading order remains: post-mortem first.
> See `docs/post_mortem/SPRINT5_FAIL_post_mortem.md` for the v0.6 diagnosis;
> see `docs/ssot/PLAN_v1.0.md` for the canonical v1.0 spec;
> see `docs/KR_SUPPORT.md` for the v1.2 KR support guide;
> see `docs/DART_API_SETUP.md` for the v1.2 L2 DART API setup guide;
> see `docs/MIGRATION_v0.7_TO_v1.0.md` for the developer migration guide.
>
> **Authoritative spec:** `docs/ssot/PLAN_v1.0.md` (active for prediction
> framework). v1.1 KR delta: `docs/KR_SUPPORT.md` is the operational guide;
> SSOT plan v1.1 not yet authored (small additive change, no framework
> reframe). Plan history: v0.1..v0.7 + v0.3.1 alt + v1.0 all preserved in
> `docs/ssot/`. If anything in this file disagrees with the SSOT, the SSOT wins.

## What this is (v1.0 — one paragraph)

GLOSTAT v1.0 is an **open-source, evidence-based probability predictor** for
global equities (US + KR + FX + commodities + crypto), framed as the open-source
evolution of TITAN (KR-only 7-engine verdict orchestrator). It does **not**
output BUY / SELL actions; it outputs a `Prediction` containing `p_up` (forward
probability of positive return), a 90% confidence interval, a per-thesis
contribution table with **Brier-weighted** ensemble weights, calibration window
metadata, n_samples per thesis, and a Merkle-leaf evidence hash. The 8 thesis
FAIL outcomes from v0.6/v0.7 (E_FUNDAMENTAL, E_FUND_FLOW, E_TIME, E_PEAD,
E_FOREIGN_REVERSAL, E_INSIDER_CLUSTER, E_SECTOR_ROTATION, E_FOMC_DRIFT,
E_FX_CARRY, E_COMMODITY_TS, E_FUNDING_CARRY) are **calibration data**, not
project failures: AUC 0.587 (E_PEAD), Sharpe 1.46 OOS (E_FOREIGN_REVERSAL), and
the anti-predictors (E_FOMC_DRIFT AUC 0.357) all enter the calibration table
with sigmoid-derived weights. The 506+ existing tests, Snapshot Broker, prompt
registry, compliance gate, hindcast harness, kill criteria automation, and
free-stack data clients (yfinance + SEC EDGAR + CFTC + CCXT + Naver KR) all
remain intact and underpin the v1.0 prediction pipeline. **Compliance gate
remains absolute** — `broadcast_telegram` and `mass_email` are inert sentinels
that always raise `ComplianceError` (INV-GS-024, reinforced by INV-GS-104
per-prediction disclaimer). v1.0 is **information tool**, never investment
advice.

## Invariants — INV-GS-001..105 (compact)

| ID | Summary | Status |
|----|---------|--------|
| INV-GS-001 | edge_bps ≥ 1.5 × all_in_bps; else BUY → HOLD | **DEPRECATED v1.0** (decision-engine artifact) |
| INV-GS-002 | find_companies result is permanent cache; no re-call | active |
| INV-GS-003 | E_NARRATIVE weight ≤ 15% (LLM blowout guard) | deferred phase 2 |
| INV-GS-004 | regime ∈ {CRASH} demotes all new LONG | deferred (decision-engine artifact) |
| INV-GS-005 | ≥ 4 experts agreeing → 0.80× anti-herd discount | **DEPRECATED v1.0** (decision-engine artifact) |
| INV-GS-006 | All verdicts/predictions append to hash-chained NDJSON | active |
| INV-GS-007 | DEFCON STOP blocks STRONG_BUY/BUY (sticky) | deferred (decision-engine artifact) |
| INV-GS-008 | T ≥ 1.5 + V ≥ 1.0 → conviction × 1.2 | deferred |
| INV-GS-009 | bigdata_search results: ≤ 14d freshness, polar sentiment only | deferred phase 2 |
| INV-GS-010 | (ticker, date, seed) → identical prediction (deterministic) | active |
| INV-GS-011..016 | Cascade Graph invariants | deferred phase 3 |
| INV-GS-017..021 | UAID + market boundary | deferred phase 2 |
| INV-GS-020 | Cost gate per-market all_in_bps lookup | active for hindcast (calibration cost mask) |
| INV-GS-022 | All MCP / API responses persisted to Snapshot Broker | active |
| INV-GS-023 | LLM calls record prompt_versions[expert] = sha256 | active |
| INV-GS-024 | Telegram broadcast permanently forbidden; personal-use disclaimer per prediction | active (reinforced by INV-GS-104) |
| INV-GS-025 | Portfolio CVaR_95 ≤ 3.5%, Herfindahl ≤ 0.12, single ≤ 8% | deferred (no portfolio mode in v1.0) |
| INV-GS-026 | New thesis: 90d hindcast, IS/OOS, AUC ≥ 0.60 (was 0.60; v1.0 uses 0.50 as table-entry minimum, 0.60 as weight ≥ 0.5 prerequisite) | active |
| INV-GS-027 | Regime: 5 → 6 states (TRANSITION + 21d confirmation) | deferred |
| INV-GS-028 | expected_pnl_bps = upside − current_loss | deferred (decision-engine artifact) |
| INV-GS-029 | Verdict.disagreement_weight required; < 0.5 → UX warn | superseded by Prediction CI |
| INV-GS-030 | E_NARRATIVE: 60d lookback + crystallization + contrarian | deferred phase 2 |
| INV-GS-031 | BETASTRIKE inherited calibration weight ≤ 10% | deferred |
| INV-GS-032 | Edge multipliers must be validated; "never tested" → weight=0 | active (now Brier-derived) |
| INV-GS-033 | Sprint 4 gate FAIL → automatic shutdown (no override) | **DEPRECATED v1.0** (project not bound to per-thesis Sharpe gate) |
| INV-GS-034 | Cascade Graph isolated to research/; zero impact on production | deferred phase 3 |
| INV-GS-035 | RavenPack ToS verified + monthly re-check | active |
| INV-GS-036 | MVP blocks bigdata_client (ConfigError if GLOSTAT_PHASE=mvp) | active |
| INV-GS-037 | yfinance_client: 8 req/sec self-throttle + retry | active |
| INV-GS-038 | sec_edgar_client: User-Agent required (rejects example.com) | active |
| INV-GS-039 | data_router: phase-gated source activation | active |
| INV-GS-040 | Bigdata activation requires user consent + budget config | active |
| **INV-GS-101** | **Output is probability + CI. BUY/SELL action output forbidden** | **active v1.0** |
| **INV-GS-102** | **Every prediction must cite source signals + calibration window + n_samples** | **active v1.0** |
| **INV-GS-103** | **Composite probability uses Brier-score-weighted ensemble (not simple mean)** | **active v1.0** |
| **INV-GS-104** | **Per-prediction disclaimer: personal use, not investment advice (extends INV-GS-024)** | **active v1.0** |
| **INV-GS-105** | **Quarterly recalibration: full thesis hindcast re-run + calibration_table.parquet update** | **active v1.0** |
| **INV-GS-106** | **KR tickers normalize to 6-digit format internally; yfinance fetch auto-appends .KS suffix** | **active v1.1** |
| **INV-GS-107** | **DART API key required for KR insider/fundamentals enhancement; absence skips cleanly** | **active v1.2** |

Source: `docs/ssot/PLAN_v0.1.md` … `PLAN_v0.7.md` (history) + `PLAN_v1.0.md` (canonical) + `docs/KR_SUPPORT.md` (v1.1 KR addendum). Machine-readable: `configs/invariants.yaml`. Budget policy: `configs/budget.yaml`.

## Scope discipline — what v1.0 explicitly does NOT do

- **No BUY/SELL action output** (INV-GS-101) — only `Prediction.p_up` + CI
- **No target/stop price output** — derivative of prohibited action output
- **No suggested position sizing** — derivative of prohibited action output
- **No Telegram broadcast — ever** (compliance gate, INV-GS-024 + INV-GS-104)
- No order execution
- No multi-user deployment (personal use only)
- No backtester framing — calibration is the byproduct, not the product
- No Cascade Graph in production until Phase 3 A/B test
- **No Bigdata MCP calls in MVP** — paid sources gated behind Phase 2+ user consent + `configs/budget.yaml` activation (INV-GS-036, INV-GS-040)
- No "alpha engine" / "trading signal" framing in any user-facing copy

## Kill criteria (v1.0 — narrowed)

| Trigger | Action |
|---------|--------|
| Compliance breach (broadcast attempt) | freeze + user review |
| Snapshot Broker integrity broken (Merkle root mismatch) | freeze + reconstruct |
| Calibration table not updated > 2 quarters | warn + auto-degrade weights to 0.5× |
| All thesis weights = 0 (composite predictor meaningless) | warn + emit "no usable signal" output |
| INV-GS-024 / INV-GS-104 bypass attempt | reject PR + auto-close |

(v0.6's "Sprint 4 gate FAIL → shutdown" is **deprecated**. Weak thesis gets weight ↓; project does not shutdown.)

## Dev commands

```bash
# environment (uv preferred)
uv sync                              # install + lock
uv sync --extra dev                  # plus dev tools

# verify
uv run python -c "import glostat; print(glostat.__version__)"   # → 1.0.0

# tests
uv run pytest -q                     # all tests, quiet
uv run pytest -q -m invariant        # only INV-GS unit tests
uv run pytest -q -m calibration      # quarterly calibration check

# lint / type
uv run ruff check .
uv run ruff format --check .
uv run mypy

# v1.0 user flow
uv run glostat predict AAPL --horizon 5d
uv run glostat predict AAPL --horizon 5d --json
uv run glostat calibrate --all-thesis --window 365d
uv run glostat calibrate --update-table
```

## Layout

```
src/glostat/
  __init__.py                 # version string (1.0.0)
  core/
    types.py                  # Prediction (v1.0), ThesisContribution (NEW),
                              # Verdict (v0.6, kept for back-compat — deprecated)
    seeded_rng.py             # SHA256-derivable seeds (INV-GS-010)
    errors.py                 # ConfigError, GlostatError, ComplianceError
  data/
    bigdata_client.py         # 6 MCP tool wrappers — BLOCKED in MVP (INV-GS-036)
    yfinance_client.py        # Yahoo Finance free wrapper (INV-GS-037)
    sec_edgar_client.py       # SEC EDGAR free API (INV-GS-038)
    cftc_client.py            # CFTC COT (free)
    ccxt_client.py            # crypto OHLCV (free)
    naver_kr_client.py        # Naver KR price/flow (free)
    data_router.py            # Phase-gated routing (INV-GS-039)
    entity_map.py             # find_companies cache (INV-GS-002)
    snapshot_broker.py        # Merkle leaf + parquet shards (INV-GS-022)
    prompt_versioning.py      # PromptRegistry + decorator (INV-GS-023)
  experts/                    # 11 thesis modules (PEAD, INSIDER_CLUSTER, FX_CARRY, …)
  predictor/                  # NEW v1.0 — composite predictor + Brier weighting
    composite.py              # composite_p_up(), thesis_weight() (INV-GS-103)
    calibration.py            # Brier score, AUC, calibration_table I/O
  risk/
    compliance_gate.py        # ComplianceError + assert_personal_use
                              # (INV-GS-024 + INV-GS-104)
  replay/
    validation_harness.py     # Hindcast + IS/OOS split + PassCriteria
                              # (INV-GS-026 — now table-entry minimum, not shutdown gate)
    sprint4_gate.py           # kept as calibration check, no longer shuts down

configs/
  markets.yaml                # XNAS + XNYS (MVP) + XKRX (Phase 1D)
  invariants.yaml             # INV-GS-001..105 machine-readable
  budget.yaml                 # Phase-gated budget caps (mvp $0)
  kill_criteria.yaml          # v1.0-narrowed kill triggers

cache/
  calibration_table.parquet   # NEW v1.0 — quarterly-updated weights per thesis

tests/
  conftest.py
  test_invariants.py          # INV-GS-001..035
  test_invariants_v06.py      # INV-GS-036..040
  test_invariants_v10.py      # NEW — INV-GS-101..105 (to add during impl)
  test_calibration.py         # NEW — Brier weighting, composite p_up

docs/
  ssot/                       # immutable plan history v0.1 → v0.7 + PLAN_v1.0.md
  post_mortem/                # honest Sprint 5 FAIL diagnosis (v0.6)
  research/                   # design notes
  CALIBRATION.md              # NEW — per-thesis empirical predictive strength
  MIGRATION_v0.7_TO_v1.0.md   # NEW — developer migration guide
  EXAMPLES.md                 # extending the framework (now: thesis + calibration)
```

## v1.0 prediction module path (Sprint structure replaced)

The v0.6 Sprint plan (Sprint 0..5) is deprecated. v1.0 work is organized into
**modules** rather than sprints:

1. **predictor/composite.py + predictor/calibration.py** — implement
   `Prediction`, `ThesisContribution`, `composite_p_up()`, `thesis_weight()`,
   `Brier`. New tests in `tests/test_calibration.py`. (Largest module — adds INV-GS-101..103.)
2. **CLI: `glostat predict <ticker>`** — output Prediction JSON. Replace
   `cli_predict_print.py` to render Prediction (probability + CI + contributions
   + disclaimer) instead of Verdict (BUY/SELL action).
3. **CLI: `glostat calibrate --all-thesis`** — quarterly recalibration command.
   Reads existing hindcast reports (phase1b, phase1c, phase1d) → writes
   `cache/calibration_table.parquet`. (INV-GS-105.)
4. **Compliance gate hardening** — add `assert_disclaimer_present(prediction)`
   helper, wire into `Prediction.__post_init__`. (INV-GS-104.)
5. **Doc regeneration** — `glostat calibrate --regenerate-docs` recreates
   `docs/CALIBRATION.md` from the parquet.

Live in v0.6 (kept):
- Snapshot Broker save/read/replay/audit
- Compliance gate (`broadcast_telegram` raises)
- PromptRegistry register/lookup with sha collision detection
- Hindcast harness (Sprint 4 gate now used as calibration check, not shutdown trigger)
- DataRouter phase gating + free-stack clients
- 506+ tests pass under v1.0 reframe (semantics of `Verdict` deprecated but back-compat preserved)

## Required env vars (unchanged from v0.6)

- `GLOSTAT_SEC_USER_AGENT="Your Name your.email@yourdomain.com"` — SEC mandates a real contact in User-Agent.
- `GLOSTAT_PHASE=mvp` (default if unset). Set to `phase_2` only with explicit `configs/budget.yaml` consent flag.

## House rules for assistant edits

- Keep files ≤ 400 lines. Split before they grow.
- No docstrings or inline comments unless WHY is non-obvious.
- Pydantic only at boundaries (CLI / MCP / API). Internal types are frozen dataclasses.
- `from __future__ import annotations` at the top of every module.
- New invariants → add to `configs/invariants.yaml` AND a unit test in `tests/test_invariants*.py`.
- Never commit `.env`, `cache/`, `snapshots/`, or generated parquet shards.
- **Never weaken INV-GS-024 / INV-GS-104** — broadcast permanently forbidden, disclaimer permanently required.
- **Never re-introduce action output (BUY/SELL/target/stop)** — INV-GS-101 violation.
- Every new thesis PR must include calibration data (n ≥ 50, AUC, Sharpe, OOS deg) and a `calibration_table.parquet` row.
