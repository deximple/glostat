# ARK-Transfer Handoff — GLOSTAT

**Date:** 2026-06-16 · **Branch:** `ark-transfer-advancements` · **PR:** #1 (base `main`)
**Status:** code landed + feature-flagged OFF; activation & signal-sourcing are blocked on
data access (API keys) + measurement harnesses, NOT on code.

This is a self-contained handoff. It records what shipped, what was validated on real
data, and exactly what unblocks the predictive-power gap. Read top to bottom.

---

## 1. What landed (all additive / flag-gated — live behavior unchanged by default)

| File | Change | Invariant |
|---|---|---|
| `src/glostat/predictor/independence.py` (NEW) | `effective_rank`, `resume_gate` (count≥3 **AND** eff-rank≥2.5, fail-closed on disjoint dates), `returns_matrix_from_records` adapter | — |
| `src/glostat/replay/thesis_returns.py` (NEW) | hindcast trades → resume-gate adapter: `trades_to_records` / `persist_trades` / `load_records` / `resume_gate_from_records` | — |
| `src/glostat/predictor/composite.py` | `fragility_veto` (oos_degradation≥1.0 → weight 0); noise gate in `predict()` (all-noise → base rate) | INV-GS-132, INV-GS-133 |
| `src/glostat/replay/phase_kr_hindcast.py` | `PhaseKrHindcastResult.thesis_trades` (additive field exposing raw per-thesis trades) | — |
| `configs/invariants.yaml` | INV-GS-132, INV-GS-133 registered | — |
| `tests/test_{independence,fragility_veto,noise_gate,thesis_returns}.py` | 43 new tests, all pass | — |

Adversarial pre-commit review: **0 CRITICAL/HIGH**. One MEDIUM fixed
(`effective_rank_from_returns` now returns `0.0` — fail-closed — when <2 common-date rows,
instead of optimistic full rank).

### Feature flags (both default OFF → predictions byte-identical to pre-change)
- `GLOSTAT_FRAGILITY_VETO` — promotes `oos_degradation` from a strength discount to an
  orthogonal **veto** axis (kills a thesis whose OOS edge collapsed, regardless of strength).
- `GLOSTAT_NOISE_GATE` — when every active thesis is statistically indistinguishable from
  random, `predict()` collapses to base rate (`edge_pp=0`, "no usable signal").

---

## 2. Real-data validation (2026-06-16, local calibration — no network)

Loaded the real calibration table (23 theses) and measured what the gates would actually do.

**Fragility veto would KILL 9/23 theses** (oos_degradation ≥ 1.0), including:
- `E_PEAD` — auc **0.586** (the single best AUC) + statistically significant, BUT
  oos_deg **1.156** (OOS Sharpe reversed below zero). The veto kills it.

→ **Flag-on is NOT a free win.** It trades away the best IS-AUC signal on OOS-failure grounds.
At n=298 that OOS death is probably real, but **enabling the veto is an operator decision.**
Options: enable as-is · raise `OOS_DEGRADATION_VETO_THRESHOLD` · add a per-thesis override.

**OOS-robust + significant KR signals are thin:** `E_PEAD_KR` (auc 0.540, oos_deg 0),
`E_TIME_KR` (sharpe 1.53, inverted edge), `E_FOREIGN_REVERSAL` (sharpe 2.15 but n=28). This
thinness is the core problem — see §4.

---

## 3. Activation path for the gates (validation-gated)

- **#1 fragility veto** — impact validated above; flag-on = operator call on E_PEAD.
- **#2-live effective-rank gate** — `thesis_trades` is now exposed on the hindcast Result.
  To activate: wire `persist_trades(result.thesis_trades, path)` into the `kr-hindcast` CLI,
  run a hindcast, then `resume_gate_from_records(trades_to_records(result.thesis_trades), p_values)`.
  **Blocked:** most theses are n=0 without API keys (§4), so a current run can't produce a
  meaningful independence matrix.
- **#3 ρ_vs_ranker** — TITANUT-side; already validated there (convexity 0.95 / regime_switch
  0.92 / conditional_threshold 0.26). Cross-system ρ (GLOSTAT thesis vs TITANUT ranker) needs
  aligned return series from both repos.

---

## 4. Signal sourcing — the real predictive-power gap (and why it's blocked)

The whole machine (resume gate, fragility veto, adapter) points at one empty slot: **a signal
orthogonal to the ranker (ρ<0.4) that is statistically significant (p<0.05).** None of the
code in this PR raises predictive power; it only measures and disciplines around the signals
you already have.

**The missing 'A' is most likely among 6 already-built but NEVER-MEASURED (n=0) non-price
producers:** `E_SHORT_SELLING_KR`, `E_INTRADAY_FLOW_KR`, `E_INSIDER_KR`,
`E_INSIDER_VELOCITY_KR`, `E_MACRO_KR`, `E_ANALYST_REVISION`. Non-price ⇒ plausibly orthogonal
to a price/momentum ranker — but unproven until hindcast.

**Two hard blockers:**
1. **No API keys** — `GLOSTAT_DART_API_KEY`, `GLOSTAT_ECOS_API_KEY`, `GLOSTAT_SEC_USER_AGENT`,
   KIS keys are all absent in `.env`. Without them the non-price data can't be fetched.
   DART (`dart.fss.or.kr`) and ECOS (`ecos.bok.or.kr`) keys are **free** (~30 min to register).
2. **No hindcast harness** — `E_SHORT_SELLING_KR` / `E_INTRADAY_FLOW_KR` / `E_MACRO_KR` /
   `E_ANALYST_REVISION` are experts but are wired into **no runnable hindcast** (only the 7
   phase_kr theses have an evaluator). `E_SHORT_SELLING_KR` is the one **keyless** path (public
   KRX AJAX) but still needs an evaluator built.

**Acceptance criterion for the 'A':** a thesis with `p < 0.05` **AND** `|ρ_vs_ranker| < 0.4`.
That is exactly the GLOSTAT resume-gate predicate and the TITANUT `kelly_stacking_sleeves`
unlock precondition (`dependencies.yaml` enablement edge in ATLAS).

---

## 5. Recommended next steps (priority order)

0. **(operator, free, ~30 min)** Register DART + ECOS API keys → set in `.env`. Unlocks
   insider / macro hindcast measurement.
1. **Build a phase_kr-style hindcast evaluator for `E_SHORT_SELLING_KR`** (keyless) →
   measure AUC + p over the KOSPI 200 universe. First concrete shot at the 'A' with no key.
2. **Once keys set:** run `glostat kr-hindcast` to lift the n=0 non-price theses to measured
   AUC; add the `persist_trades` wiring so the independence gate has real data.
3. **For any significant thesis:** compute ρ vs the TITANUT ranker (cross-system; align return
   series by date). If `p<0.05 AND |ρ|<0.4` → that is the missing 'A'.
4. **Then** decide fragility / noise flag-on (the gates discipline the book; they don't
   create alpha).

---

## 6. Honest bottom line

Predictive power (signal IC) is **unchanged** by this work — by design. It is blocked on
**data access (API keys) + measurement engineering**, not on code cleverness. The fastest
real progress is step 0 (free keys) + step 1 (keyless short-selling harness).

Cross-references: workspace memory `project_ark_transfer_audit.md`; PR #1; ATLAS
`dependencies.yaml` enablement edge `glostat-titanut-uncorrelated-sleeve`.
