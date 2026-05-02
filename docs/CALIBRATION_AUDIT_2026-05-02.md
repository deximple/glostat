# Calibration Status Audit — 2026-05-02 (v1.10.1)

Generated from synthetic_calibration_for_mock(). 23 theses total.

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

## UNDERFIT (1)

| thesis | AUC | n_samples | Sharpe | brier | active |
|---|---:|---:|---:|---:|:---:|
| `E_INSIDER_CLUSTER` | 0.3390 | 11 | +0.782 | 0.2500 | no |

## BOOTSTRAP (12)

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
| `E_REGIME_US` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_SHORT_SELLING_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_TIME_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |

## Action items

### NEAR_RANDOM (no edge after large-n measurement)
- **E_COMMODITY_TS** (n=517, AUC=0.4890): Sharpe weakly positive but AUC inside ±0.02 → no directional edge. Candidate for retirement after one more recalibration window.
- **E_FUNDING_CARRY** (n=2921, AUC=0.5052): the biggest n in the table but flat AUC. Sharpe is mildly negative. Candidate for retirement OR for re-spec (different feature set / horizon).

### UNDERFIT (n too small for stable AUC)
- **E_INSIDER_CLUSTER** (n=11, AUC=0.339, Sharpe=+0.782): Sharpe looks great but n is way under the 50-sample threshold. Re-run hindcast with relaxed gating to grow n to ≥ 50; current entry is unreliable.

### BOOTSTRAP (awaiting hindcast wave)
- **E_REGIME_US**: live hindcast launched 2026-05-02; result will land in cache/hindcast/phase_us_regime/.
- 11 KR theses + E_ANALYST_REVISION: most have hindcast wiring (kr-hindcast adds 7 KR theses); the remaining 4 (E_INSIDER_KR, E_MACRO_KR, E_SHORT_SELLING_KR, E_INTRADAY_FLOW_KR, E_ANALYST_REVISION) need their own dedicated hindcast waves.
