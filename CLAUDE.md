# GLOSTAT — Claude Code Project Context

> **🛑 STATUS: ARCHIVED (2026-04-29) — INV-GS-033 SHUTDOWN per Sprint 5 FAIL.**
> See `docs/post_mortem/SPRINT5_FAIL_post_mortem.md` for honest diagnosis.
> Reusable infrastructure (Snapshot Broker, Gating, Hindcast, Kill Criteria, prompt versioning, compliance gate) intact for future plans.
>
> **Authoritative spec:** `docs/ssot/PLAN_v0.6.md` (Option B applied, archived). Plan history: v0.1..v0.5 + v0.3.1 alt all preserved in `docs/ssot/`. This file is a working summary; if anything here disagrees with the SSOT, the SSOT wins.

## What this is (v0.6 — one paragraph)

GLOSTAT is a personal-use **global cascade intelligence engine** for swing-horizon (1d–30d) stock verdicts on US equities (XNAS + XNYS only in MVP). v0.4 was a **dramatic refactor** of v0.3 driven by three RECONSIDER votes (E3 Behavioral, E6 Red Team, E10 Contrarian) converging on: v0.3 was scope-creep + backtest theatre. **v0.6 (Option B) further demotes Bigdata MCP (RavenPack paid) to optional Phase 2+ enrichment** — MVP runs entirely on **free stack** (yfinance + SEC EDGAR), **MVP cost = $0/mo**. Direction: **validation-first, scope-disciplined, bottom-up** — 3 Experts (E_FUNDAMENTAL, E_FUND_FLOW, E_TIME) on US-only swing horizon, hard kill criteria at the Sprint 4 gate, Cascade Graph deferred to Phase 3 research mode (gated on A/B Sharpe lift > 0.2). Sprint 0 infrastructure: Snapshot Broker (Merkle-leaf parquet shards + SQLite index), prompt versioning (sha256 per LLM call), compliance gate (broadcast permanently forbidden), validation harness (90-day hindcast, IS/OOS split), **free stack clients (yfinance + SEC EDGAR + DataRouter phase-gating)**. **Bigdata MCP calls blocked at code level in MVP** (INV-GS-036, `ConfigError` if `GLOSTAT_PHASE=mvp`). No new feature ships before Sprint 0 scaffolding lands.

## Invariants — INV-GS-001..035 (compact)

| ID | Summary | Sprint |
|----|---------|--------|
| INV-GS-001 | edge_bps ≥ 1.5 × all_in_bps; else BUY → HOLD | 0 |
| INV-GS-002 | find_companies result is permanent cache; no re-call | 0 |
| INV-GS-003 | E_NARRATIVE weight ≤ 15% (LLM blowout guard) | phase 2 |
| INV-GS-004 | regime ∈ {CRASH} demotes all new LONG | 1 |
| INV-GS-005 | ≥ 4 experts agreeing → 0.80× anti-herd discount | 2 |
| INV-GS-006 | All verdicts append to hash-chained NDJSON | 0 |
| INV-GS-007 | DEFCON STOP blocks STRONG_BUY/BUY (sticky) | 1 |
| INV-GS-008 | T ≥ 1.5 + V ≥ 1.0 → conviction × 1.2 | 2 |
| INV-GS-009 | bigdata_search results: ≤ 14d freshness, polar sentiment only | 1 |
| INV-GS-010 | (ticker, date, seed) → identical verdict (deterministic) | 0 |
| INV-GS-011 | Cascade Graph edges must have non-empty sources[] | phase 3 |
| INV-GS-012 | Propagation MAX_HOP=4, THRESHOLD=10bps | phase 3 |
| INV-GS-013 | E_CASCADE weight ≤ 20% | phase 3 |
| INV-GS-014 | CascadeVerdict missing triggering_event.sources → reject | phase 3 |
| INV-GS-015 | TZ delays from markets.yaml only (no estimation) | phase 3 |
| INV-GS-016 | Multi-path aggregation = signed sum (not abs) | phase 3 |
| INV-GS-017 | All ticker inputs in UAID form (MIC.LOCAL or RP:eid) | phase 2 |
| INV-GS-018 | Verdict carries target_uaid + target_market_meta | phase 2 |
| INV-GS-019 | Cross-market hop carries tz_delay, feasibility, FX | phase 3 |
| INV-GS-020 | Cost gate looks up all_in_bps in target_market entry | 0 (XNAS/XNYS) |
| INV-GS-021 | foreign_access ≠ open → executable_for_user flagged | phase 2 |
| INV-GS-022 | Bigdata MCP responses persisted to Snapshot Broker | 0 |
| INV-GS-023 | LLM calls record prompt_versions[expert] = sha256 | 0 |
| INV-GS-024 | Telegram broadcast permanently forbidden; personal-use disclaimer per verdict | 0 |
| INV-GS-025 | Portfolio CVaR_95 ≤ 3.5%, Herfindahl ≤ 0.12, single ≤ 8% | 1.5 |
| INV-GS-026 | New expert: 90d hindcast, IS/OOS, AUC ≥ 0.60 | 0 |
| INV-GS-027 | Regime: 5 → 6 states (TRANSITION + 21d confirmation) | phase 2 |
| INV-GS-028 | expected_pnl_bps = upside − current_loss | 1 |
| INV-GS-029 | Verdict.disagreement_weight required; < 0.5 → UX warn | 3 |
| INV-GS-030 | E_NARRATIVE: 60d lookback + crystallization + contrarian | phase 2 |
| INV-GS-031 | BETASTRIKE inherited calibration weight ≤ 10% | 1 |
| INV-GS-032 | Edge multipliers must be validated; "never tested" → weight=0 | phase 3 |
| INV-GS-033 | Sprint 4 gate FAIL → automatic shutdown (no override) | 4 |
| INV-GS-034 | Cascade Graph isolated to research/; zero impact on production | phase 3 |
| INV-GS-035 | RavenPack ToS verified + monthly re-check | 0 |
| INV-GS-036 | MVP blocks bigdata_client (ConfigError if GLOSTAT_PHASE=mvp) | 0 |
| INV-GS-037 | yfinance_client: 5 req/sec self-throttle | 0 |
| INV-GS-038 | sec_edgar_client: User-Agent required (rejects example.com) | 0 |
| INV-GS-039 | data_router: phase-gated source activation | 0 |
| INV-GS-040 | Bigdata activation requires user consent + budget config | phase 2 |

Source: `docs/ssot/PLAN_v0.1.md` … `PLAN_v0.6.md`. Machine-readable: `configs/invariants.yaml` (40 entries, 29 active in MVP, 11 deferred). Budget policy: `configs/budget.yaml`.

## Scope discipline — what v0.4 explicitly does NOT do

- No cross-market cascade (Phase 2/3)
- No 9-Expert simultaneous build (3 → 6 → 9 staged)
- No 80+ markets (US 2 → US+KR 4 → …)
- **No Telegram broadcast — ever** (compliance gate, INV-GS-024)
- No order execution (verdict only)
- No intraday horizon (BETASTRIKE territory)
- No long-term (3–5y) horizon (TITAN territory)
- No multi-user deployment (personal use only)
- No macOS Menubar in Phase 1 (CLI + localhost dashboard only)
- No Cascade Graph in production until Phase 3 A/B test
- **No Bigdata MCP calls in MVP** — all paid sources gated behind Phase 2+ user consent + `configs/budget.yaml` activation (INV-GS-036, INV-GS-040)

## Kill criteria

| Trigger | Action |
|---------|--------|
| Sprint 4 validation gate FAIL | Immediate shutdown |
| 6mo live Sharpe < 0.8 | Immediate shutdown |
| Hindcast OOS degradation > 30% | Freeze + 90d additional hindcast |
| Compliance issue | Pause + legal review |
| Bigdata MCP price 3× hike | Diversify (SEC EDGAR + Polygon parallel) |
| Scope-creep attempt to v0.5 mid-Sprint | **Reject**; finish v0.4 first |

## Dev commands

```bash
# environment (uv preferred — pyproject.toml is uv-native)
uv sync                              # install + lock
uv sync --extra dev                  # plus dev tools

# alternative: stdlib venv
python3.14 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# verify
python -c "import glostat; print(glostat.__version__)"   # → 0.4.0

# tests
uv run pytest -q                     # all tests, quiet
uv run pytest -q -m invariant        # only INV-GS unit tests
uv run pytest -q tests/test_invariants.py::test_inv_gs_022_snapshot_determinism

# lint / type
uv run ruff check .
uv run ruff format --check .
uv run mypy

# Sprint 0 acceptance: import + tests pass
uv run python -c "import glostat" && uv run pytest -q
```

## Layout

```
src/glostat/
  __init__.py                 # version string
  core/
    types.py                  # Verdict, ExpertSignal, MarketMeta (dataclass + pydantic)
    seeded_rng.py             # MOET A7 — SHA256-derivable seeds (INV-GS-010)
    errors.py                 # ConfigError, GlostatError
  data/
    bigdata_client.py         # 6 MCP tool wrappers — BLOCKED in MVP (INV-GS-036)
    yfinance_client.py        # Yahoo Finance free wrapper (INV-GS-037, 5 req/sec)
    sec_edgar_client.py       # SEC EDGAR free API (INV-GS-038, User-Agent required)
    data_router.py            # Phase-gated routing — MVP={yfinance, sec_edgar} (INV-GS-039)
    entity_map.py             # find_companies cache (parquet, INV-GS-002)
    snapshot_broker.py        # Merkle leaf + SQLite index + parquet shards (INV-GS-022)
    prompt_versioning.py      # PromptRegistry + decorator (INV-GS-023)
  risk/
    compliance_gate.py        # ComplianceError + assert_personal_use (INV-GS-024)
  replay/
    validation_harness.py     # Hindcast + IS/OOS split + PassCriteria (INV-GS-026)

configs/
  markets.yaml                # XNAS + XNYS only (MVP scope)
  invariants.yaml             # INV-GS-001..040 machine-readable
  budget.yaml                 # Phase-gated budget caps (mvp $0, phase_2 $50, phase_3 $200)

tests/
  conftest.py                 # @pytest.mark.network skip unless NETWORK_TESTS=1
  test_invariants.py          # INV-GS-001, 010, 022, 023, 024 + boundaries
  test_invariants_v06.py      # INV-GS-036..040 (8 tests)
  test_yfinance_client.py     # 8 tests (1 network-skip)
  test_sec_edgar_client.py    # 10 tests (MockTransport)
  test_data_router.py         # 15 tests (phase gating)
docs/ssot/                    # immutable plan history v0.1..v0.6 + v0.3.1 alt
docs/research/                # E_NARRATIVE / snapshot_broker / kill_criteria designs
```

## Sprint 0 → Sprint 1 handoff (v0.6 Option B)

Stubbed in Sprint 0 (raise `NotImplementedError("MCP wired in S1")` or carry `TODO(Sprint N)`):
- `BigdataClient` MCP methods — **BLOCKED in MVP** by `assert_phase_2_or_later()` (INV-GS-036). Sprint 2+ wires for Phase 2 consent.
- `yfinance_client` async fetch paths — wrapper structure complete, Sprint 1 robustifies pandas DataFrame handling for unstable yfinance fields (`earnings/calendar`, `holders`).
- `sec_edgar_client.get_13f_holdings` — list endpoint complete, `infotable.xml` parsing pending (Sprint 1 XBRL).
- `Hindcast.run()` with a real pipeline — Sprint 1 plumbs the 3 experts; Sprint 4 runs the gate.
- `EntityMap` only loads/saves; production `find_companies`-equivalent (`sec_edgar_client.ticker_to_cik`) loop happens during Sprint 1 universe bootstrap.

Live in Sprint 0:
- Verdict v1 dataclass with INV-GS-001/022/023 enforcement at construction time.
- SnapshotBroker save/read/replay/audit — SQLite + parquet (no S3 yet).
- Compliance gate — `broadcast_telegram` and `mass_email` are inert sentinels that always raise.
- PromptRegistry — register/lookup with sha collision detection.
- HindcastSplit + PassCriteria — pure data + math, no pipeline dependency.
- **DataRouter phase gating** — MVP routes to {yfinance, sec_edgar} only; Bigdata raises `ConfigError`.
- **YFinanceClient + SecEdgarClient class skeletons** — async + rate-limited (5/10 req/sec); ready for Sprint 1 wiring.

Sprint 1 first PR: yfinance/SEC EDGAR live wiring → universe bootstrap (S&P 500 ticker → CIK) → E_FUNDAMENTAL via yfinance + EDGAR → AAPL verdict $0 cost validation. **Cost audit step removed** (was v0.5 #1; MVP has no Bigdata calls so $0 by construction).

## Required env vars before Sprint 1

- `GLOSTAT_SEC_USER_AGENT="Your Name your.email@yourdomain.com"` — SEC mandates a real contact in User-Agent. Default `"GLOSTAT research@example.com"` raises `ConfigError(INV-GS-038)`.
- `GLOSTAT_PHASE=mvp` (default if unset). Set to `phase_2` only after Sprint 4 gate PASS + `configs/budget.yaml` consent flag.

## House rules for assistant edits

- Keep files ≤ 400 lines. Split before they grow.
- No docstrings or inline comments unless WHY is non-obvious.
- Pydantic only at boundaries (CLI / MCP / API). Internal types are frozen dataclasses.
- `from __future__ import annotations` at the top of every module.
- New invariants → add to `configs/invariants.yaml` AND a unit test in `tests/test_invariants.py`.
- Never commit `.env`, `cache/`, `snapshots/`, or generated parquet shards.
- Never weaken a kill criterion silently — surface it in PR description.
