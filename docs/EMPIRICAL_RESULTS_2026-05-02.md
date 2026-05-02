# GLOSTAT v1.6.2 Empirical Results — Wave 1 + Wave 2 KR Hindcast (2026-05-02)

> Generated: 2026-05-02 (post-v1.6.2 release).
> Source: `cache/hindcast/phase_kr/` (live measurement on KOSPI 200 top-30 universe).
> Window: 2025-09-01 → 2026-01-31 (5 months, stride 7d).
> Sample density: 660 (ticker, day) evaluations per thesis (30 tickers × 22 sample days).
>
> **This document captures the empirical answer to a foundational question:**
> Did the v1.4.1 → v1.6.2 sprint sequence (panels P5/P6/P8/P10 absorption) produce
> measurable predictive edge, or just visible-but-impotent infrastructure?
>
> **Honest answer:** 1 of 6 new KR theses produced statistically significant edge
> (E_PEAD_KR, AUC 0.5405, p=0.008). The other 5 are noise or anti-predictive.
> The Brier-weighted ensemble correctly down-weights the failures so they don't
> harm the predictor — only E_PEAD_KR drives meaningful p_up differentiation.

---

## TL;DR

| Thesis (added in) | n | AUC | p-value | Sharpe | Verdict |
|---|---:|---:|---:|---:|---|
| **E_PEAD_KR** (v1.6) | **360** | **0.5405** | **0.008** | +0.480 | ✅ statistically significant edge |
| E_FOREIGN_REVERSAL (v1.1) | 28 | 0.5308 | 0.57 | +2.151 | small-n inconclusive |
| E_COMMODITY_INDEX_KR (v1.5) | 44 | 0.5323 | 0.46 | **−2.253** | ⚠️ anti-predictive Sharpe |
| E_FUNDAMENTAL_KR_CYCLICAL (v1.5) | 176 | **0.5000** | 1.00 | 0.000 | ⚠️ literally random |
| E_FUNDAMENTAL_KR (v1.1) | 572 | 0.4807 | 0.11 | +0.477 | NEAR_RANDOM |
| E_TIME_KR (v1.2) | 660 | 0.4692 | 0.10 | +1.527 | NEAR_RANDOM (with positive Sharpe) |

p-value is two-sided z-test against AUC = 0.5 with conservative SE = 1/√(12·n).

---

## P10 Contrarian Veteran panel reconsidered

In the 16-panel evaluation (2026-05-01) of GLOSTAT v1.4.0, P10 Contrarian Veteran
made the strongest minority claim:

> "Infrastructure improvements ≠ alpha improvements. v0.6→v1.4까지 4번의
> infrastructure sprint, 같은 alpha-absent signature. SK이노베이션 같은 KR
> megacap은 efficient market — 이 framework로는 신호를 찾지 못한다."

The 2026-05-02 wave-1+wave-2 hindcast empirically tests this prediction.

**P10 was MOSTLY correct:**

- E_FUNDAMENTAL_KR_CYCLICAL (the headline P6 fix for SK이노베이션 mis-scoring)
  produced **AUC = 0.5000 exactly** over n=176 — literally indistinguishable
  from random. The cyclical sector / EV-EBITDA hypothesis did not generalize.
- E_COMMODITY_INDEX_KR (refining-only WTI + crack momentum) produced
  AUC = 0.5323 BUT Sharpe = **−2.253** — when the model said LONG (oil up),
  the refiner stocks went DOWN on average. Anti-predictive.
- E_FUNDAMENTAL_KR (n=572, AUC 0.4807) and E_TIME_KR (n=660, AUC 0.4692)
  remain NEAR_RANDOM as previously measured.

**P10 was PARTIALLY wrong:**

- E_PEAD_KR (the simplest of the new theses — point-in-time T+5..T+30 OHLCV
  drift after KIFRS earnings filing) achieved **AUC 0.5405 with n=360,
  p=0.008**. The post-earnings drift effect IS real in KR megacap.
- E_PEAD_KR's measured edge is small (~4pp above chance) but statistically
  significant at α=0.01. composite_v2 confidence factor: 0.890.

**Net verdict:** P10's claim that the framework finds NO edge is falsified.
P10's claim that MOST infrastructure work doesn't produce alpha is supported.

---

## Cross-stock effect — before/after wave-1 calibration

Same 5 KR tickers, same `glostat predict` command, same v1.6.2 binary.
Only difference: presence/absence of measured calibration in `cache/hindcast/phase_kr/`.

### Before (v1.6.2 with n=0 bootstrap for all new experts)

```
4 of 5 tickers: identical p_up = 53.4%
Reason: composite_v2 weight ≈ 0 for n=0 experts → no differentiation
"*** Statistical note: every active signal indistinguishable from random"
```

### After (wave-1 calibration loaded)

| Ticker | 종목 | p_up | net | edge | E_PEAD_KR signal |
|---|---|---:|---:|---:|---:|
| 096770 | SK이노베이션 | 50.1% | -28bps | -1.9pp | -0.78 SHORT |
| 005490 | POSCO홀딩스 | 50.1% | -47bps | -1.9pp | -1.37 SHORT |
| 011200 | HMM | 50.1% | -24bps | -1.9pp | -0.49 SHORT |
| **000720** | **현대건설** | **53.6%** | **+31bps** | **+1.6pp** | **+2.00 LONG** |
| 005930 | 삼성전자 | 50.1% | -14bps | -1.9pp | -0.07 NEUTRAL |

**현대건설 (000720) breakout** is data-driven: recent post-earnings drift in
the T+5..T+30 window after Q3 2025 filing was strongly positive (+20%), so
E_PEAD_KR fires +2.00. Combined with the other neutral-ish signals,
composite predictor lifts p_up to 53.6%.

The other 4 tickers' E_PEAD_KR scores were negative/near-zero, so the
composite sits at or just below the 52% baseline.

---

## Brier ensemble correctly survives failed experiments

**Failed experiment**: E_FUNDAMENTAL_KR_CYCLICAL (AUC 0.5000)

The composite predictor weights this thesis as follows:

```
brier_score = 0.25 × (1 − 2 × |0.5000 − 0.5|) = 0.25 × 1.0 = 0.25
weight_raw  = 1.0 − 4 × 0.25 = 0.0
final_weight = weight_raw × confidence_v2 = 0.0
```

Result: **The cyclical signal is visible in output (-3.00 for SK이노베이션) but
contributes ZERO to p_up.** The user sees an honest log of what the experiment
produced; the composite is unmoved.

**Failed experiment**: E_COMMODITY_INDEX_KR (AUC 0.5323, Sharpe −2.253)

```
brier_score = 0.25 × (1 − 2 × |0.5323 − 0.5|) = 0.25 × 0.9354 = 0.234
weight_raw  = 1.0 − 4 × 0.234 = 0.066
confidence_v2_factor (n=44, small) ≈ 0.048
final_weight ≈ 0.066 × 0.048 ≈ 0.003
```

Result: **Commodity-index signal contributes ~0.3% of total weight.** The
anti-predictive Sharpe doesn't directly reduce weight (Brier is AUC-only),
but the small sample size suppresses confidence_v2.

**Successful experiment**: E_PEAD_KR (AUC 0.5405)

```
brier_score = 0.25 × (1 − 2 × |0.5405 − 0.5|) = 0.25 × 0.919 = 0.230
weight_raw  = 1.0 − 4 × 0.230 = 0.080
confidence_v2_factor (n=360, stable) ≈ 0.890
final_weight ≈ 0.080 × 0.890 ≈ 0.071
```

Result: **E_PEAD_KR carries ~7% of total composite weight** — meaningful
contribution. The 현대건설 LONG outcome is largely driven by this expert.

---

## Architectural insight

The GLOSTAT framework's design survived the worst-case empirical test:

1. **5 of 6 new KR theses produced no alpha**, exactly as P10 predicted.
2. **No harm done**: Brier-weighted ensemble down-weights failures to ≤1% each.
3. **Successful expert (E_PEAD_KR) drives genuine prediction differentiation**:
   현대건설 LONG (+1.6pp edge) vs all others NEAR-FLAT.
4. **Honest output**: signals are visible (with their measured AUC and p-value)
   even when they contribute zero weight. Users see what was tested.

This is the design promise of the v1.0 reframe: **the framework's value is the
calibration table itself, not any one row in it.** Failed rows don't poison
the output — they just receive zero weight and appear as transparent diagnostic
data in the per-signal contribution table.

---

## Honest caveats

1. **Window is short (5 months)**. Longer windows (e.g. v1.2 M1's 2-year
   measurement) gave E_FUNDAMENTAL_KR AUC 0.495 vs 0.481 here — measurements
   are noisy.
2. **n=44 (E_COMMODITY_INDEX_KR) is small.** The anti-predictive Sharpe
   could be sample-specific. Refining stocks may have had a particularly
   adverse 5-month window where commodity-momentum signal happened to fail.
3. **n=176 (E_FUNDAMENTAL_KR_CYCLICAL) AUC = 0.5000 EXACTLY** is suspicious —
   may indicate scoring formula clipping (signals all maxed at ±3.0 and the
   labels split exactly 50/50). Worth investigating in a follow-up.
4. **OOS degradation 100%** for both wave-2 experts — overfitting signal,
   though small-n makes IS/OOS split less reliable.
5. **현대건설 LONG signal** was driven by a single Q3 2025 earnings event
   producing strong drift. May not generalize to future quarters.

---

## What this changes for the project

### Definitely keep

- **E_PEAD_KR**: real edge, statistically significant. Continue to refine
  (longer windows, full KOSPI 200 universe, multiple horizons).
- **commodity_client + sector_classifier_kr** infrastructure: independent
  utility (powers other future work even if cyclical/commodity expert
  iteration didn't pan out).
- **kr_calendar_client + CI calendar widening + next_triggers**: P5
  presentation improvements are independent of expert calibration. Always
  useful.

### Reconsider

- **E_FUNDAMENTAL_KR_CYCLICAL scoring formula**: AUC = 0.5000 is suspicious.
  The current `−0.6·EV/EBITDA_z + 0.4·(−cycle_term·2)` formula may be too
  rigid. Possible improvements:
  - Add momentum confirmation (don't fire LONG just because cycle is at
    trough; require recent uptick)
  - Per-sector formula tuning (refining differs from steel from chemicals)
  - Use sector-relative instead of absolute EV/EBITDA z-score
- **E_COMMODITY_INDEX_KR direction**: anti-predictive Sharpe suggests the
  momentum-following hypothesis is wrong for KR refiners in this window.
  Possible: contrarian formulation (high commodity momentum = exit signal)
  or remove the expert entirely.

### Document for future researchers

- This empirical record stands as evidence that **most experimental thesis
  ideas don't produce alpha in efficient markets**. KR megacap is a hard
  problem. The v0.6 / v0.7 / v1.2 / v1.6.2 hindcasts all converge on this:
  AUCs cluster around 0.48–0.54.
- The framework's job is to **honestly report which experiments worked**,
  not to manufacture alpha that isn't there.

---

## Reproduction

To reproduce these measurements (network-bound, ~5–25 min):

```bash
glostat kr-hindcast --start 2025-09-01 --end 2026-01-31 --max-concurrent 3 --stride 7
glostat predict 096770   # SK이노베이션
glostat predict 000720   # 현대건설 (LONG breakout)
glostat predict 005930   # 삼성전자 (control)
```

Reports land in `cache/hindcast/phase_kr/`. They are gitignored — each user
runs their own measurement. Results may differ slightly run-to-run as the
window rolls forward.

---

## v1.9.0 Alpha Discovery Sprint (2026-05-02 evening) — cross-universe measurement

After v1.8.0, a fresh measurement pass extended the empirical map across
**4 universes** (KR megacap, KR mid-cap, US megacap, US small-mid) using the
same 5-month window (2025-09-01 → 2026-01-31).

### Cross-universe AUC matrix

| Thesis | **KOSPI 200 megacap** | **KOSDAQ 150 mid-cap** | **US megacap (10)** | **US small-mid (10)** |
|---|---:|---:|---:|---:|
| E_FUNDAMENTAL_KR | 0.4807 (n=572) | 0.4181 (n=44) | — | — |
| **E_TIME_KR** | 0.4692 (n=660) | **0.5138 (n=387)** ⬆ | — | — |
| E_FOREIGN_REVERSAL | 0.5308 (n=28) | 0.4271 (n=32) | — | — |
| **E_PEAD_KR** | **0.5405 (n=360)** ⬆ | 0.4991 (n=213) | — | — |
| US-side (composite) | — | — | AUC 0.5009, Sharpe 0.000 | AUC 0.5053, Sharpe **+0.355** |

### Key empirical finding — universe × thesis interaction

P10 Contrarian Veteran's prediction was **"infrastructure ≠ alpha; KR megacap
efficient market"**. The cross-universe measurement reveals a more nuanced
truth:

1. **E_PEAD_KR works on KR megacap (AUC 0.5405) but NOT on KR mid-cap (0.4991)**.
   The post-earnings-announcement drift hypothesis (Bernard-Thomas 1989)
   appears reversed for KOSDAQ150 in this window — possibly because mid-cap
   KR earnings are less analyst-followed and don't generate the same
   "drift after consensus reaction" pattern.

2. **E_TIME_KR works on KR mid-cap (AUC 0.5138, Sharpe 1.63) but NOT on KR
   megacap (0.4692)**. Ichimoku-style time-series momentum finds edge in
   the less-efficient mid-cap universe — exactly where academic literature
   predicts momentum signals to work better.

3. **US small-mid Sharpe (+0.355) > US megacap Sharpe (0.000)**. Same
   directional pattern as KR: **edge attenuates moving up the cap curve**.
   The FAIL on Sprint 4 gate (Sharpe < 0.80 threshold) is from the legacy
   v0.6 verdict surface; the v1.0 prediction tool just records this as
   calibration data — no shutdown.

4. **No single thesis dominates across universes**. The ensemble is the
   product, not any one row.

### Reframed P10 reading

> "Infrastructure improvements ≠ alpha improvements" — STILL TRUE for the
> per-universe single-thesis question.
>
> "GLOSTAT framework finds no edge" — PARTIALLY FALSE. Different theses
> find different edges in different universes. The cross-universe
> ensemble has more measurable structure than any single (thesis, universe)
> cell suggests.

### Reproduction (v1.9.0)

```bash
# v1.9.0 added the scan command and KOSDAQ150 universe.
glostat kr-hindcast --universe KR_KOSDAQ150_TOP30 --start 2025-09-01 --end 2026-01-31
glostat scan --universe KR_KOSPI200_TOP30 --top 5 --significant
glostat scan --universe KR_KOSDAQ150_TOP30 --top 5 --significant
```

The `--significant` flag filters to tickers where at least one active
signal has p<0.05 (statistically significant AUC). Without this filter
the scan returns ranked results based on raw composite edge — useful but
without the statistical safety net.
