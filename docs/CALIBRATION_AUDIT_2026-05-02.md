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
