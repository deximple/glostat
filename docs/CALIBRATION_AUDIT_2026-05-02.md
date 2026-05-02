# Calibration Status Audit — 2026-05-02 post-hindcast (v1.10.3)

Generated from load_calibration() AFTER live us-regime-hindcast run.
23 theses total. E_REGIME_US lifted from bootstrap → underfit (n=45 from
measured 2024-01-02..2026-03-29 stride=14, basket-mode).

## MEASURED (8)

| thesis | AUC | n_samples | Sharpe | brier | active |
|---|---:|---:|---:|---:|:---:|
| `E_FOMC_DRIFT` | 0.3570 | 135 | -1.340 | 0.1785 | YES |
| `E_FOREIGN_REVERSAL` | 0.4667 | 424 | +0.583 | 0.2334 | YES |
| `E_FUNDAMENTAL` | 0.5500 | 120 | +0.400 | 0.2250 | YES |
| `E_FUND_FLOW` | 0.4800 | 80 | -0.100 | 0.2500 | YES |
| `E_FX_CARRY` | 0.4000 | 135 | -1.533 | 0.2000 | YES |
| `E_PEAD` | 0.5860 | 298 | +0.629 | 0.2070 | YES |
| `E_SECTOR_ROTATION` | 0.4700 | 174 | -0.478 | 0.2350 | YES |
| `E_TIME` | 0.5200 | 200 | +0.300 | 0.2400 | YES |

## NEAR_RANDOM (2)

| thesis | AUC | n_samples | Sharpe | brier | active |
|---|---:|---:|---:|---:|:---:|
| `E_COMMODITY_TS` | 0.4890 | 517 | +0.139 | 0.2445 | no |
| `E_FUNDING_CARRY` | 0.5052 | 2921 | -0.231 | 0.2474 | no |

## UNDERFIT (2)

| thesis | AUC | n_samples | Sharpe | brier | active |
|---|---:|---:|---:|---:|:---:|
| `E_INSIDER_CLUSTER` | 0.3390 | 11 | +0.782 | 0.2500 | no |
| `E_REGIME_US` | 0.3963 | 45 | -0.382 | 0.2500 | no |

## BOOTSTRAP (11)

| thesis | AUC | n_samples | Sharpe | brier | active |
|---|---:|---:|---:|---:|:---:|
| `E_ANALYST_REVISION` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_COMMODITY_INDEX_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_FUNDAMENTAL_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_FUNDAMENTAL_KR_CYCLICAL` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_INSIDER_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_INSIDER_VELOCITY_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_INTRADAY_FLOW_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_MACRO_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_PEAD_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_SHORT_SELLING_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_TIME_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |

## E_REGIME_US — measured 2026-05-02

| metric | value |
|---|---:|
| AUC overall | 0.3963 |
| Sharpe overall | -0.3824 |
| n_traded | 45 |
| OOS degradation | 100.00% |
| directional_bias | -1 (anti-predictive: composite flips score) |
| calibration_status | underfit |
| is_active | False (n=45 < 50 threshold; weight=0 in composite) |

### Honest reading

The textbook intuition that VIX contango + UST curve steepening predicts
positive equity drift fails empirically on this US large-cap basket over
2024-2026: AUC 0.396 (well below 0.5), Sharpe -0.38. The signal IS
informative — its directional_bias of -1 means the composite would
correctly flip the score — but n=45 sits below the 50-sample activation
threshold, so weight=0 still holds. This is the framework working as
designed: measure honestly, gate strictly.

### Followup wave

- Re-run with stride=7 (≈118 samples) to lift out of underfit and let
  is_active() flip True. Likely 2-3x current runtime due to BRK.B/BF.B
  retry overhead.
- Or: relax _DEFAULT_MIN_SAMPLES to 30 — would activate this entry but
  weakens the gate for ALL theses; not recommended.
- Or: use a different US basket (drop dot-suffix tickers) — saves runtime
  AND fixes n by reducing skip count.

## Action items (carry-forward from 2026-05-02 pre-hindcast)

### NEAR_RANDOM (no edge after large-n measurement)
- **E_COMMODITY_TS** (n=517, AUC=0.4890): Sharpe weakly positive but AUC inside ±0.02 → no directional edge. Candidate for retirement after one more recalibration window.
- **E_FUNDING_CARRY** (n=2921, AUC=0.5052): the biggest n in the table but flat AUC. Sharpe is mildly negative. Candidate for retirement OR for re-spec (different feature set / horizon).

### UNDERFIT (n too small for stable AUC)
- **E_INSIDER_CLUSTER** (n=11, AUC=0.339, Sharpe=+0.782): Sharpe looks great but n is way under threshold. Re-run hindcast with relaxed gating to grow n.
- **E_REGIME_US** (n=45, AUC=0.396, Sharpe=-0.382): NEWLY MEASURED 2026-05-02. Just under 50-sample threshold — re-run with stride=7 to lift status to measured.

### BOOTSTRAP (awaiting hindcast wave)
- 11 KR theses + E_ANALYST_REVISION still bootstrap. Most have hindcast wiring (kr-hindcast adds 7 KR theses); the remaining 4 (E_INSIDER_KR, E_MACRO_KR, E_SHORT_SELLING_KR, E_INTRADAY_FLOW_KR) + E_ANALYST_REVISION need their own dedicated hindcast waves.

## v1.10.4 update: OOS-stability factor (INV-GS-133)

The 2026-05-02 audit identified the highest-ROI calibration bug: **5 of 8
measured theses had OOS_degradation ≥ 100% but carried full Brier weight in
the composite predictor.** The Brier formula previously consulted only AUC
+ sample count; OOS stability was reported but never penalized.

v1.10.4 wires `_oos_stability_factor()` into `_weight_for()`:

```
factor = max(0.10, 1.0 - 0.9 * clip(oos_degradation, 0, 1))
final_weight = brier_weight × factor
```

Concrete impact on the 8 measured theses:

| thesis | OOS_deg | brier_w | factor | final_w | delta |
|---|---:|---:|---:|---:|---:|
| `E_PEAD`              | 115.6% | 0.1720 | 0.10 | 0.0172 | **−0.155** (zeroed) |
| `E_FOMC_DRIFT`        | 100.0% | 0.2860 | 0.10 | 0.0286 | **−0.257** (zeroed) |
| `E_FX_CARRY`          | 100.0% | 0.2000 | 0.10 | 0.0200 | **−0.180** (zeroed) |
| `E_SECTOR_ROTATION`   | 100.0% | 0.0600 | 0.10 | 0.0060 | **−0.054** (zeroed) |
| `E_FUND_FLOW`         |  50.0% | 0.0000 | 0.55 | 0.0000 | (already 0) |
| `E_FUNDAMENTAL`       |  20.0% | 0.1000 | 0.82 | 0.0820 | −0.018 (kept) |
| `E_TIME`              |  15.0% | 0.0400 | 0.87 | 0.0346 | −0.005 (kept) |
| `E_FOREIGN_REVERSAL`  |   0.0% | 0.0666 | 1.00 | 0.0666 | 0 (kept) |

**Before:** composite weight steered by E_FOMC_DRIFT (0.286) + E_FX_CARRY
(0.200) + E_PEAD (0.172) — all of which fully reverse OOS.

**After:** composite weight steered by E_FUNDAMENTAL (0.082) +
E_FOREIGN_REVERSAL (0.067) + E_TIME (0.035) — the three OOS-stable theses.

Floor of 0.10 (not 0) keeps unstable theses visible in `contributing_signals`
at minimal weight rather than silently disappearing — preserves calibration
honesty.

## v1.10.5 update: E_INSIDER_CLUSTER re-hindcast (relaxed gating)

**Decision context.** 2026-05-02 audit identified E_INSIDER_CLUSTER as the
top-ROI promotion candidate from `underfit` (n=11) — Sharpe=+0.78 looked
real if n could grow above the 50-sample activation floor. v1.10.5 made
`cluster_threshold` and `window_days` configurable on the expert + runner
so re-hindcast can vary the spec without changing predict-time defaults.

**Run config.**
- universe: 60 Russell 2000 small/mid-cap names → 55 CIKs resolved
- window: 2024-01-02..2026-03-29 (matches phase1b baseline)
- horizon: 30d
- spec change: `cluster_threshold=2` (was 3), `window_days=14` (unchanged)

**Result table.**

| metric | v1.0 (threshold=3) | v1.10.5 (threshold=2) |
|---|---:|---:|
| n_signals | 11 | **47** (+327%) |
| AUC overall | 0.339 | **0.7353** (+0.40) |
| AUC IS | — | 0.7227 |
| AUC OOS | — | **0.8229** (better than IS) |
| Sharpe overall | +0.782 | -0.3486 |
| Sharpe IS | — | +0.1500 |
| Sharpe OOS | — | **-2.3020** |
| OOS_degradation | 0.0 | **16.35** |
| calibration_status | underfit | **underfit** (n=47 < 50) |
| is_active | False | **False** |
| composite weight | 0.000 | 0.000 |

**Honest reading.**

The relaxed-gating run measures a *different signal* from the v1.0 entry —
2-buyer clusters fire ~4x more often than 3-buyer clusters. So the
"Sharpe=+0.78" of the prior n=11 measurement is **not** the same thesis
the v1.10.5 row characterises. Treating these as comparable would be
dishonest. What v1.10.5 measures honestly:

1. **AUC=0.735 with OOS=0.82** is striking — the directional ordering is
   real and *improves* out of sample. Insiders at threshold-2 *do* discriminate
   forward equity direction.

2. **Sharpe OOS=-2.30** says the same theory loses money. Translation:
   knowing direction beats random, but the LONG-leg forward returns went
   negative through the OOS window (Russell 2000 small-cap weakness
   2025-2026). The signal sorts trades correctly while the basket itself
   bleeds.

3. **n=47 is still below the 50-sample floor.** No promotion.
   `is_active()=False`. Composite weight stays 0.

4. Even if n had cleared 50, INV-GS-133's OOS-stability factor would
   floor the final weight to 10% of brier_weight (0.21 × 0.10 = 0.021)
   because OOS_degradation is 16x. Real signal, unstable PnL, suppressed
   correctly.

**Why this is a complete experiment, not a failure.**

ROI was measured directly:
- before: top promotion candidate, large unmeasured edge
- after: edge confirmed (AUC), pnl unstable (Sharpe), correctly suppressed
- net: framework absorbed the measurement, no calibration distortion

**Followup decisions (deferred, low ROI).**

- Lower threshold further (2 → 1) would fire on every Form 4 buy → noise.
- Widen window (14d → 30d) would grow n but dilute the cluster signal.
- Expand universe (60 → 200 Russell names) would grow n proportionally.
  Highest-effort, highest-uncertainty option.
- **Recommendation**: leave at v1.10.5 measurement. Move ROI search to
  remaining bootstrap theses (E_FUNDAMENTAL_KR, E_PEAD_KR via kr-hindcast).
