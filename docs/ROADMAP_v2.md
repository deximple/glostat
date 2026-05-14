# GLOSTAT v2.x.x Roadmap

> **Status:** ACTIVE as of 2026-05-13. Synthesis of 10 parallel sub-agent
> reviews (planner, architect, python-reviewer, database-reviewer,
> security-reviewer, tdd-guide, refactor-cleaner, performance-optimizer,
> market-researcher, earnings-reviewer) conducted immediately after the
> v2.0.0 publish.
>
> Authoritative spec for thesis development: `docs/ssot/PLAN_v1.0.md`.
> This file is a living roadmap; supersede with `ROADMAP_v3.md` when v3.0
> ships.

## v2.0.1 — hotfix (this week)

### CRITICAL — security

- `data/dart_client.py` lines 147, 201, 229, 259, 288 — strip `crtfc_key`
  from `HTTPStatusError` URLs before they reach error messages and logs.
- `data/ecos_client.py` lines 142, 199 — ECOS key embedded in path; replace
  `f"... url={url}: {exc}"` with status-code-only message.
- `data/snapshot_broker.py` lines 294, 357 — assert resolved parquet path
  is `relative_to(self.root)` before reading; blocks `../../etc/...` path
  traversal via SQLite tampering.
- `data/dart_client.py` line 317 — replace `xml.etree.ElementTree.parse`
  with `defusedxml.ElementTree.parse` (billion-laughs guard).
- `data/kis_client.py` `__init__` — `logging.getLogger("httpx").setLevel(WARNING)`
  to prevent header logging at debug.

### HIGH — build green

- `tests/test_invariants.py:329` — version assertion `1.9.1` → `2.0.0`.
- `tests/test_cli.py:183, 193` — two failing mock-pipeline assertions.
- `src/glostat/predictor/__init__.py` — add `prediction_sha256`,
  `PredictionIn`, `SignalContributionIn` to imports + `__all__`.
- `configs/invariants.yaml` — header `v1_4_active_count: 3 → 2`; reconcile
  `total` against actual map size.

### Regression guards

- `tests/test_ip_boundary.py` (NEW) — fail if sibling-internal-project
  names or sibling-internal-project filesystem paths appear in src, docs,
  configs after a future commit. The forbidden token list is defined
  inside the test itself.
- `tests/test_invariants_v20.py` (NEW) — assert `Prediction` has no
  `dca_sizing` field; INV-GS-111 is marked `deprecated: true` and
  `deprecated_in: v2.0`; no `from glostat.predictor.dca_sizing` import
  exists anywhere in `src/`.

### Stale doc residue

- `docs/QUICKSTART.md` lines 137, 182 — drop "Sizing tier" row and the
  INV-GS-111 sentence.
- `CLAUDE.md` line 169 — "DCA sizing are unchanged" sentence stale.

## v2.1 — single low-cost signals + infra abstraction

Theme: ship the two cheapest measurable signals + introduce the abstraction
that makes future thesis growth a one-file PR.

| # | Feature | LOC | Cost | Signal AUC |
|---|---------|----:|:----:|------------|
| 1 | `experts/e_52w_high_proximity.py` — within-5%-of-52w-high anchor (George & Hwang 2004, JoF) | ~120 | S | 0.54–0.57 |
| 2 | `experts/e_kr_short_interest.py` — KR short-interest anomaly (Wang & Yu 2024, PBFJ); reuses `krx_short_client.py` | ~140 | S | 0.55–0.58 |
| 3 | `data/openkrx_client.py` — wraps already-connected `mcp__openkrx__*`, 10 req/sec, Snapshot Broker integrated | ~180 | S | infra |
| 4 | `predictor/registry.py` — `@thesis` decorator, self-register, replaces 16-arg orchestrator switch | ~200 | M | infra |
| 5 | Two-level storage in `snapshot_broker.py` — keep raw shard + emit `tiles/{edge_type}/date={YYYYMMDD}/data.parquet` for cross-section reads | ~140 | M | perf |
| 6 | `@functools.lru_cache(maxsize=8)` on `predictor/calibration.py:load_calibration` — 50–200x hindcast speedup | ~20 | S | perf |

INV-GS additions: `INV-GS-115` (openkrx ToS, 08:00 KST freshness),
`INV-GS-116` (`@thesis` decorator self-registration mandatory for new
experts), `INV-GS-117` (tile materialization).

## v2.2 — measurement gate enforcement

Pre-registered ΔAUC gate: a new thesis cannot land at weight ≥ 0.5 unless
the hindcast shows ΔAUC ≥ +0.02 over the prior baseline with a 95%
bootstrap CI strictly above 0. This is the gate that converts honest
post-mortem ("v0.6 8 FAIL") into a permanent project posture.

| # | Feature | LOC | Cost |
|---|---------|----:|:----:|
| 1 | `predictor/measurement_gate.py` — `assert_thesis_promotable(thesis, hindcast)` | ~180 | S |
| 2 | `glostat calibrate --gate-check` subcommand + `cache/promotion_log.parquet` | ~80 | S |
| 3 | Retro-apply gate to all 18 experts; auto-degrade non-passers to ≤ 0.49 weight | ~40 | S |
| 4 | `INV-GS-133` — pre-registered gate is mandatory before weight ≥ 0.5 | ~10 | S |
| 5 | `snapshot_broker.py` write batching (transaction-scoped) — 10–100x perf | ~80 | M |
| 6 | `predictor/composite.py` — fold `_attach_confidence_v2` into `_compute_masses` (2x perf) | ~40 | S |
| 7 | Deprecate `replay/sprint4_gate.py` (INV-GS-033 already deprecated v1.0) | -300 | S |

## v2.3 — 4-source flow fusion + earnings layer

Goal: lift the KR megacap noise-floor (AUC ≤ 0.51 measured on n=3,510) by
adding a 1st-party investor-flow source AND start the earnings extensions.

| # | Feature | LOC | Cost | Signal AUC |
|---|---------|----:|:----:|------------|
| 1 | `data/krx_investor_client.py` — 투자자별 매매대금 free AJAX (no key) | ~180 | S | infra |
| 2 | `fuse_three_source_flows` → `fuse_four_source_flows` (KIS+Toss+Naver+KRX); median when ≥ 3 agree | ~80 | S | infra |
| 3 | `INV-GS-134` — 4-source quorum + disagreement gate | ~10 | S | — |
| 4 | `experts/e_earnings_sue.py` — standardized unexpected earnings (Bernard-Thomas 1989) | ~160 | M | 0.58 |
| 5 | `experts/e_earnings_revision_velocity.py` — analyst revision count post-print (Womack 1996) | ~140 | M | 0.56 |
| 6 | `predictor/calibration_events.ndjson` — append-only calibration event log (incremental updates between quarters) | ~120 | M | infra |
| 7 | Re-run Phase KR M1 hindcast with 4-source provenance; promote via v2.2 gate (likely fails the gate — ship as logging-only weight=0 if so) | — | — | — |

## v2.4 — quality factor + global macro

| # | Feature | LOC | Cost | Signal AUC |
|---|---------|----:|:----:|------------|
| 1 | `experts/e_qmj_quality.py` — Quality-Minus-Junk composite (Asness, Frazzini, Pedersen 2019) | ~280 | M | 0.58–0.62 |
| 2 | `experts/e_earnings_accrual_quality.py` — Sloan 1996 accruals anomaly | ~180 | M | 0.55 |
| 3 | `data/fred_client.py` — US macro symmetric with ECOS | ~200 | M | infra |
| 4 | Snapshot Broker incremental Merkle root (1000x+ perf at 100k+ records) | ~120 | M | perf |
| 5 | Per-universe Brier weights (XNAS / XKRX / XKOS / crypto) | ~160 | M | calib |

## v2.5 — options + on-chain + KR-specific

| # | Feature | LOC | Cost | Signal AUC |
|---|---------|----:|:----:|------------|
| 1 | `experts/e_ivol_skew.py` — Xing-Zhang-Zhao 2010 put-call IV skew (yfinance options) | ~220 | M-L | 0.56–0.59 |
| 2 | `experts/e_onchain_flow.py` — Glassnode/Etherscan free tier; replaces dead `E_FUNDING_CARRY` | ~180 | M | 0.55–0.60 |
| 3 | `experts/e_earnings_kr_prelim_final_gap.py` — Choi-Kim 2015, 잠정실적 vs 확정실적 divergence | ~160 | M | 0.61 |
| 4 | Correlation penalty in `predictor/composite.py` — rolling 4-quarter pairwise ρ; warn if max > 0.5 | ~80 | M | calib |

## v3.0.0 — multi-horizon

`Prediction.up_probability` becomes `{p_up_1d, p_up_5d, p_up_21d}`; per-horizon calibration tables; CLI output stable (JSON gains fields, text adds 2 lines). Gated entirely on v2.5 stable.

## Top risks (must guard against)

1. **v2.3 ΔAUC fails the v2.2 gate.** Most likely outcome at KR noise floor. Mitigation: pre-register failure path; ship as logging-only weight=0; do not relax the gate. The gate failing is the gate working.
2. **Earnings cluster correlation.** 4 earnings signals active simultaneously can hit ρ > 0.6 in macro-driven seasons; composite must add correlation penalty (v2.5 #4) before all 4 are active, otherwise effective independent-signal count collapses to ~1.5.
3. **Per-universe calibration in v2.4 overfits thin KR samples.** Phase KR M1 had n=3,510 across 200 tickers × 17 days. Per-universe weights with quarterly refresh chase noise. Require `n ≥ 1,000 per universe` minimum before activating; otherwise fall back to global weights.

## Performance backlog (perf-optimizer top 5, sequenced into roadmap)

| # | Fix | Speedup | In release |
|---|-----|---------|------------|
| 1 | `@lru_cache` on `load_calibration` | 50–200x | v2.1 |
| 2 | `save_snapshot` batching | 10–100x | v2.2 |
| 3 | `_attach_confidence_v2` collapse | 2x | v2.2 |
| 4 | SQLite `payload_sha` column for dedup | 5–20x | v2.4 |
| 5 | Incremental Merkle root | 1000x+ at 100k | v2.4 |

## Architecture follow-ups (architect findings)

- **B1 — `predictor/thesis_wrappers.py`** 16-arg orchestrator → replaced by `ThesisRegistry` in v2.1.
- **B2 — `predictor/calibration.py`** dual-source loader (3 shape parsers + markdown scraping for phase1d) → replaced by `calibration_events.ndjson` in v2.3.
- **B3 — KR universe gating duplicated** across 10 wrappers → centralised inside `@thesis` decorator's `universe=` arg in v2.1.
- Deprecate `replay/sprint4_gate.py` (v2.2) — INV-GS-033 has been deprecated since v1.0.
- MCP boundary: direct httpx clients remain the primary integration path (deterministic replay, INV-GS-010, INV-GS-022). MCP servers (openkrx, bigdata) stay read-only for interactive exploration only.

## Coverage / TDD follow-ups (tdd-guide findings)

- `replay/` and `risk/` are coverage-WEAK areas; v2.2 should add ≥ 1 dedicated test file each.
- 5 invariants with no named regression test: `INV-GS-101` (no BUY/SELL), `INV-GS-102` (citation required), `INV-GS-104` (disclaimer required), `INV-GS-106` (KR ticker normalization), `INV-GS-109` (3-source fusion). Each needs a `tests/test_invariants_*.py` entry that references the ID by string.
- All async client tests should carry `@pytest.mark.network` even when mock-transported, so `-m "not network"` runs are deterministic. Apply consistently in v2.0.1.
