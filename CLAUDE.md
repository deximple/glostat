
Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

Tradeoff: These guidelines bias toward caution over speed. For trivial tasks, use judgment.

1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.

Before implementing:

State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.
2. Simplicity First
Minimum code that solves the problem. Nothing speculative.

No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

3. Surgical Changes
Touch only what you must. Clean up only your own mess.

When editing existing code:

Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:

Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

4. Goal-Driven Execution
Define success criteria. Loop until verified.

Transform tasks into verifiable goals:

"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:

1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.


# GLOSTAT — Claude Code Project Context

> **STATUS: ACTIVE v1.6.2 — Option A wave 2: cyclical + commodity hindcast wired (full 6-thesis KR calibration).**
> Previous: v1.6.1 — Option A wave 1 (E_PEAD_KR hindcast wired);
> v1.6 — P5 Event-Driven panel absorption (calendar awareness);
> v1.5 — P6 sector-aware cyclicals; v1.4.1 — X+W honesty patch
> (P8+P10 panel synthesis); v1.4 — N1+N2+N3+N4 (KR multi-source + experts +
> sizing + confidence); v1.3 — M2 (ECOS BoK macro overlay); v1.2 — KR
> calibration (L1) + DART API (L2); v1.1 (2026-04-29) — KR (KOSPI 200)
> production support; v1.0 (2026-04-29) Prediction Tool reframe of v0.7.
> v1.6.2 delta (Option A wave 2 — INV-GS-123):
> - **commodity_client point-in-time refactor** — cache stores FULL fetched
>   series; `get_cycle(key, as_of=...)` slices to bars on/before `as_of` for
>   percentile + momentum. New `prefetch(keys, earliest_as_of)` helper for
>   hindcast callers (single fetch per commodity per run). New helpers:
>   `_bars_on_or_before`, `_closes_on_or_before`, `_cache_covers_window`.
> - **evaluate_fundamental_kr_cyclical** in `phase_kr_eval.py` — point-in-time
>   evaluator: cyclical-sector gate via `cycle_class_of()`, commodity cycle
>   via `commodity_client.get_cycle(as_of=day)` or `get_crack_spread(as_of=day)`,
>   EV/EBITDA z-score from yfinance Fundamentals.raw, score = `-W_VALUE *
>   ev_ebitda_z + W_CYCLE * (-cycle_term * 2)`.
> - **evaluate_commodity_index_kr** in `phase_kr_eval.py` — refining-only
>   gate via `is_refining()`, WTI + crack-spread 30-day momentum.
> - **phase_kr_hindcast** is now 6-thesis (was 4) — adds `fundamental_kr_cyclical`
>   + `commodity_index_kr` to Result/Config + accumulators + report build.
>   Single commodity prefetch at start of run.
> - **`calibration._PHASE_SOURCES`** loads 2 new reports so the next predict
>   after a kr-hindcast run lifts both new theses from n=0 bootstrap to real
>   measured AUC/Sharpe.
> - 2 new commodity_client point-in-time tests; full suite 1069 pass.
> v1.6.1 delta (Option A wave 1 — INV-GS-122):
> v1.6.1 delta (Option A wave 1 — INV-GS-122):
> - **evaluate_pead_kr** in `phase_kr_eval.py` — point-in-time T+5..T+30
>   OHLCV drift evaluator for E_PEAD_KR. For each (ticker, day) sample,
>   computes most-recent expected KIFRS earnings filing (Q-end + 45d),
>   measures drift, records signal with forward_return. Skip cleanly when
>   days_since < 30 or OHLCV bars missing.
> - **phase_kr_hindcast** wired: 4-thesis run (was 3) — adds pead_kr field
>   to Result + horizon_pead to Config + accumulator + report build.
> - **persist_phase_kr_reports** + **render_phase_kr_comparison** — 4-column
>   comparison MD; 4 thesis JSONs.
> - **calibration._PHASE_SOURCES** — adds E_PEAD_KR loader so the next
>   `glostat predict` after running `glostat kr-hindcast` lifts E_PEAD_KR
>   from n=0 bootstrap to real measured AUC/Sharpe.
> - **17 new + updated tests**; full suite 1067 pass.
> - Cyclical (E_FUNDAMENTAL_KR_CYCLICAL) + commodity-momentum
>   (E_COMMODITY_INDEX_KR) hindcast deferred to wave 2 — needs historical
>   commodity OHLCV with point-in-time semantics (commodity_client cache key
>   needs (key, as_of) tuple, not just key).
> v1.6 delta (P5 calendar absorption — INV-GS-119/120/121):
> v1.6 delta (P5 calendar absorption — INV-GS-119/120/121):
> - **kr_calendar_client.py** (~250 lines) — surfaces upcoming KR-relevant
>   events: KR earnings (KIFRS Q-end + 45d heuristic), BoK 금통위
>   (hardcoded 2026 schedule, 8 meetings), OPEC 장관급 (auto-scrape
>   opec.org/40.htm with 30d cache + hardcoded 2026 fallback), OPEC JMMC
>   (first-Wednesday monthly heuristic). Snapshot writes mandatory.
> - **E_PEAD_KR** (~250 lines) — KR Post-Earnings Announcement Drift.
>   Computes T+5 → T+30 OHLCV drift after most-recent expected filing
>   date (Q-end + 45d). archetype=continuation. KOSPI 200 universe gate.
>   Bootstrap n=0; weight=0 until KR PEAD hindcast measures real AUC.
> - **composite CI calendar widening** — `predict()` accepts
>   `days_to_imminent_event`; sigma scales ×1.5 (D-day < 7) or ×2.0
>   (D-day < 3). Reflects option-implied vol expansion approaching
>   scheduled events.
> - **next_triggers populated from calendar** — concrete D-day countdowns
>   ("BoK 금통위 2026-05-30 (D-28)") replace generic
>   "horizon expires in ~30 days". KR ticker only; non-KR falls back to
>   the original auto-derived list.
> - 34 new tests; full suite green.
> v1.5 delta (P6 sector cycle absorption — INV-GS-115/116/117/118):
> - **commodity_client.py** (~200 lines) — wraps yfinance commodity futures
>   (CL=F WTI, BZ=F Brent, RB=F gasoline, TIO=F iron ore, HG=F copper, BDRY)
>   with cycle-percentile + 30d-momentum metrics. Computes crack spread
>   (42·gas − WTI) for refining margin. 6h per-process cache; mandatory
>   snapshot broker writes (INV-GS-022).
> - **sector_classifier_kr.py** (~120 lines) — KOSPI 200 ticker → KrSector +
>   CycleClass. Hard-coded ~40 ticker roster covering 정유 5, 철강 4, 화학 5,
>   운송 4, 건설 5, 자동차 3 cyclical + defensive/growth slots.
> - **E_FUNDAMENTAL_KR_CYCLICAL** (~280 lines) — gates to cyclical sectors
>   only. Score formula: −0.6·EV/EBITDA_z + 0.4·(−cycle_term·2). Trough
>   percentile + cheap valuation → LONG (mean-reversion archetype, contrarian).
>   Directly addresses P6 panel finding: "정유주는 사이클 저점에서 PER 상승 =
>   healthy, not bearish". E_FUNDAMENTAL_KR for SK이노베이션 (-1.78 SHORT)
>   was structurally wrong; the cyclical version produces correct LONG when
>   EV/EBITDA cheap + crack spread at trough.
> - **E_COMMODITY_INDEX_KR** (~180 lines) — refining-only universe gate;
>   30d momentum on WTI + crack spread, archetype=continuation (momentum-
>   following). Refining tickers: 010950 S-Oil, 096770 SK이노베이션, 078930
>   GS, 267250 HD현대.
> - All 4 modules cleanly integrate with thesis_wrappers + cli_predictor +
>   calibration backfill. 81 new tests; 1029 total pass.
> v1.4.1 delta (X+W honesty patch — INV-GS-113 + INV-GS-114):
> - **X1 — CI 1-sigma label clarity.** `confidence_interval_bps` is a 1-sigma
>   (~68%) interval, not 95%. Output now reads `CI 1-sigma (68%): ...`.
> - **X2 — CI-includes-0 visual flag.** When low <= 0 <= high, output appends
>   `*** includes 0 -> no clear direction`.
> - **X3 — AUC z-score / p-value annotation.** Each active signal line carries
>   a `p=X, n.s.` or `p<0.001` tag computed via SE ≈ 1/sqrt(12·n).
> - **X4 — Round-trip cost subtraction.** `expected return` line now shows
>   gross AND net (after `round_trip_bps(market)` cost from markets.yaml).
>   KR: ~23 bps round-trip, US: ~1.4 bps.
> - **X5 — n=0 thesis 'no data' explicit display.** Replaces the silent
>   `+0.00` contribution with `no data (n=0, weight=0)`.
> - **X6 — Statistical-significance composite disclaimer.** When every active
>   signal has p > 0.05, surface "*** Statistical note: every active signal's
>   AUC is statistically indistinguishable from random...".
> - **W1 — KR megacap universe-specific honesty footer.** XKRX/XKOS markets
>   emit a Phase-KR-M1-derived disclosure: "AUC <= 0.51 on n=3,510 KOSPI 200
>   samples — discrimination is at the edge of statistical noise".
> - All changes are **presentation-layer only** — composite predictor logic,
>   Brier weights, confidence_v2, and DCA sizing are unchanged. New module
>   `predictor.honesty` (~150 lines) carries the math; `cli_predict_print`
>   wires it into the rendered output.
> v1.4 delta:
> v1.4 delta:
> - **N1 — KR 3-source investor flows.** New `kis_client.KisClient` (KIS Open
>   API read-only paths, 20 req/sec, OAuth token managed; order-execution
>   endpoints intentionally NOT wrapped per INV-GS-101). New
>   `toss_client.TossClient` (TITAN local-parquet pattern, no live API). New
>   `fuse_three_source_flows()` helper (KIS + Toss + Naver merged by date,
>   median when ≥ 2 sources agree, warn on > 50% disagreement).
>   `EForeignReversalExpert` consumes 3-source provenance.
> - **N2 — KR 공매도 + intraday flow experts.** New `krx_short_client.KrxShortClient`
>   (free public KRX AJAX endpoint, 5 req/sec). New `EShortSellingKrExpert`
>   (TITAN E5++ port — short-balance change + squeeze candidate detection).
>   New `EIntradayFlowKrExpert` (TITAN E5+ port — Naver baseline + KIS
>   overlay, foreign-flow acceleration). Both bootstrapped at AUC=0.50, n=0;
>   weight=0 until a dedicated KR hindcast measures predictive strength.
> - **N3 + N4** — TITAN-derived sizing & confidence: new `predictor.dca_sizing`
>   module porting TITAN L4 W = 0.30·R + 0.25·T + 0.25·V + 0.20·S to GLOSTAT
>   prediction-tool framing as INFORMATION ONLY; new `predictor.confidence_v2`
>   module implementing TITAN chart_pattern.py 5-component confidence (sample_
>   quality, effective_size_factor, score_stability, return_consistency,
>   recency_quality), used as a Brier-weight modulator in
>   `predictor.composite._compute_masses`.
> - New invariants `INV-GS-109` (3-source fusion + disagreement guard),
>   `INV-GS-110` (short-selling expert universe + scrape-fail skip),
>   `INV-GS-111` (dca_sizing INFORMATION ONLY), `INV-GS-112` (confidence_v2
>   weight modulation).
> v1.3 delta (M2): new `ecos_client.EcosClient` (한국은행 OpenAPI; free 10k/day)
> + new `EMacroKrExpert` aggregating BoK base rate Δ, KRW/USD trend, CPI
> surprise, KOSPI momentum into a single KR macro signal; gracefully skipped
> when `GLOSTAT_ECOS_API_KEY` is unset. New invariant `INV-GS-108` (ECOS
> graceful skip + 10 req/sec rate limit + Snapshot Broker integration).
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

| ID              | Summary                                                                                                                           | Status                                                            |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| INV-GS-001      | edge_bps ≥ 1.5 × all_in_bps; else BUY → HOLD                                                                                      | **DEPRECATED v1.0** (decision-engine artifact)                    |
| INV-GS-002      | find_companies result is permanent cache; no re-call                                                                              | active                                                            |
| INV-GS-003      | E_NARRATIVE weight ≤ 15% (LLM blowout guard)                                                                                      | deferred phase 2                                                  |
| INV-GS-004      | regime ∈ {CRASH} demotes all new LONG                                                                                             | deferred (decision-engine artifact)                               |
| INV-GS-005      | ≥ 4 experts agreeing → 0.80× anti-herd discount                                                                                   | **DEPRECATED v1.0** (decision-engine artifact)                    |
| INV-GS-006      | All verdicts/predictions append to hash-chained NDJSON                                                                            | active                                                            |
| INV-GS-007      | DEFCON STOP blocks STRONG_BUY/BUY (sticky)                                                                                        | deferred (decision-engine artifact)                               |
| INV-GS-008      | T ≥ 1.5 + V ≥ 1.0 → conviction × 1.2                                                                                              | deferred                                                          |
| INV-GS-009      | bigdata_search results: ≤ 14d freshness, polar sentiment only                                                                     | deferred phase 2                                                  |
| INV-GS-010      | (ticker, date, seed) → identical prediction (deterministic)                                                                       | active                                                            |
| INV-GS-011..016 | Cascade Graph invariants                                                                                                          | deferred phase 3                                                  |
| INV-GS-017..021 | UAID + market boundary                                                                                                            | deferred phase 2                                                  |
| INV-GS-020      | Cost gate per-market all_in_bps lookup                                                                                            | active for hindcast (calibration cost mask)                       |
| INV-GS-022      | All MCP / API responses persisted to Snapshot Broker                                                                              | active                                                            |
| INV-GS-023      | LLM calls record prompt_versions[expert] = sha256                                                                                 | active                                                            |
| INV-GS-024      | Telegram broadcast permanently forbidden; personal-use disclaimer per prediction                                                  | active (reinforced by INV-GS-104)                                 |
| INV-GS-025      | Portfolio CVaR_95 ≤ 3.5%, Herfindahl ≤ 0.12, single ≤ 8%                                                                          | deferred (no portfolio mode in v1.0)                              |
| INV-GS-026      | New thesis: 90d hindcast, IS/OOS, AUC ≥ 0.60 (was 0.60; v1.0 uses 0.50 as table-entry minimum, 0.60 as weight ≥ 0.5 prerequisite) | active                                                            |
| INV-GS-027      | Regime: 5 → 6 states (TRANSITION + 21d confirmation)                                                                              | deferred                                                          |
| INV-GS-028      | expected_pnl_bps = upside − current_loss                                                                                          | deferred (decision-engine artifact)                               |
| INV-GS-029      | Verdict.disagreement_weight required; < 0.5 → UX warn                                                                             | superseded by Prediction CI                                       |
| INV-GS-030      | E_NARRATIVE: 60d lookback + crystallization + contrarian                                                                          | deferred phase 2                                                  |
| INV-GS-031      | BETASTRIKE inherited calibration weight ≤ 10%                                                                                     | deferred                                                          |
| INV-GS-032      | Edge multipliers must be validated; "never tested" → weight=0                                                                     | active (now Brier-derived)                                        |
| INV-GS-033      | Sprint 4 gate FAIL → automatic shutdown (no override)                                                                             | **DEPRECATED v1.0** (project not bound to per-thesis Sharpe gate) |
| INV-GS-034      | Cascade Graph isolated to research/; zero impact on production                                                                    | deferred phase 3                                                  |
| INV-GS-035      | RavenPack ToS verified + monthly re-check                                                                                         | active                                                            |
| INV-GS-036      | MVP blocks bigdata_client (ConfigError if GLOSTAT_PHASE=mvp)                                                                      | active                                                            |
| INV-GS-037      | yfinance_client: 8 req/sec self-throttle + retry                                                                                  | active                                                            |
| INV-GS-038      | sec_edgar_client: User-Agent required (rejects example.com)                                                                       | active                                                            |
| INV-GS-039      | data_router: phase-gated source activation                                                                                        | active                                                            |
| INV-GS-040      | Bigdata activation requires user consent + budget config                                                                          | active                                                            |
| **INV-GS-101**  | **Output is probability + CI. BUY/SELL action output forbidden**                                                                  | **active v1.0**                                                   |
| **INV-GS-102**  | **Every prediction must cite source signals + calibration window + n_samples**                                                    | **active v1.0**                                                   |
| **INV-GS-103**  | **Composite probability uses Brier-score-weighted ensemble (not simple mean)**                                                    | **active v1.0**                                                   |
| **INV-GS-104**  | **Per-prediction disclaimer: personal use, not investment advice (extends INV-GS-024)**                                           | **active v1.0**                                                   |
| **INV-GS-105**  | **Quarterly recalibration: full thesis hindcast re-run + calibration_table.parquet update**                                       | **active v1.0**                                                   |
| **INV-GS-106**  | **KR tickers normalize to 6-digit format internally; yfinance fetch auto-appends .KS suffix**                                     | **active v1.1**                                                   |
| **INV-GS-107**  | **DART API key required for KR insider/fundamentals enhancement; absence skips cleanly**                                          | **active v1.2**                                                   |
| **INV-GS-108**  | **ECOS API key required for KR macro signal (E_MACRO_KR); 10 req/sec rate limit; Snapshot Broker integration mandatory**          | **active v1.3**                                                   |
| **INV-GS-111**  | **Prediction.dca_sizing field is INFORMATION ONLY (calibration-derived sizing tier %); does NOT constitute a BUY/SELL recommendation. INV-GS-101 preserved** | **active v1.4**                                                   |
| **INV-GS-112**  | **confidence_v2 uses 5-component geometric mean (TITAN chart_pattern pattern); composite weight = brier_weight × confidence_v2_factor**         | **active v1.4**                                                   |
| **INV-GS-113**  | **Output honesty: CI label = '1-sigma (~68%)'; CI-includes-0 flag; n=0 thesis 'no data' line; AUC z-score / p-value annotation; composite all-noise statistical disclaimer** | **active v1.4.1**                                                |
| **INV-GS-114**  | **Universe-specific honesty: KR megacap (XKRX/XKOS) predictions surface a Phase KR M1 measured-AUC <= 0.51 disclosure footer**                                              | **active v1.4.1**                                                |
| **INV-GS-115**  | **commodity_client wraps yfinance commodity futures with cycle-percentile + 30d-momentum metrics; mandatory snapshot writes**                                               | **active v1.5**                                                  |
| **INV-GS-116**  | **sector_classifier_kr maps KOSPI 200 → KrSector + CycleClass; hard-coded ~40 cyclical/defensive/growth roster**                                                            | **active v1.5**                                                  |
| **INV-GS-117**  | **E_FUNDAMENTAL_KR_CYCLICAL gates to cyclical sectors; EV/EBITDA z-score + commodity-cycle term where trough → LONG (contrarian)**                                          | **active v1.5**                                                  |
| **INV-GS-118**  | **E_COMMODITY_INDEX_KR gates to refining tickers; 30d WTI + crack spread momentum (continuation)**                                                                          | **active v1.5**                                                  |
| **INV-GS-119**  | **kr_calendar_client surfaces KR earnings (KIFRS heuristic) + BoK 금통위 (hardcoded 2026) + OPEC 장관급 (auto-scrape + fallback) + OPEC JMMC (first-Wed monthly)**            | **active v1.6**                                                  |
| **INV-GS-120**  | **next_triggers populated with concrete D-day countdowns from calendar; KR ticker only, falls back to auto-derived list otherwise**                                          | **active v1.6**                                                  |
| **INV-GS-121**  | **CI sigma calendar widening: D-day < 7 → ×1.5σ, D-day < 3 → ×2.0σ; reflects option-implied vol expansion near scheduled events**                                            | **active v1.6**                                                  |
| **INV-GS-122**  | **kr-hindcast wires E_PEAD_KR via point-in-time T+5..T+30 OHLCV drift; calibration loader picks up the real report so n=0 bootstrap is replaced with measured AUC/Sharpe**     | **active v1.6.1**                                                |
| **INV-GS-123**  | **kr-hindcast wires E_FUNDAMENTAL_KR_CYCLICAL + E_COMMODITY_INDEX_KR via commodity_client point-in-time slicing; full 6-thesis KR calibration in one hindcast run**             | **active v1.6.2**                                                |

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

| Trigger                                                  | Action                                |
| -------------------------------------------------------- | ------------------------------------- |
| Compliance breach (broadcast attempt)                    | freeze + user review                  |
| Snapshot Broker integrity broken (Merkle root mismatch)  | freeze + reconstruct                  |
| Calibration table not updated > 2 quarters               | warn + auto-degrade weights to 0.5×   |
| All thesis weights = 0 (composite predictor meaningless) | warn + emit "no usable signal" output |
| INV-GS-024 / INV-GS-104 bypass attempt                   | reject PR + auto-close                |

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
