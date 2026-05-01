# confidence_v2 — INV-GS-112 (v1.4 N4)

> **STATUS: ACTIVE v1.4.** TITAN chart_pattern.py `_compute_confidence`
> 5-component confidence model adapted to GLOSTAT's `ThesisCalibration` shape
> and used as a Brier-weight modulator (INV-GS-112). Composite uses geometric
> mean (weakest-link semantics).

## Why a second confidence layer?

The Brier score on its own (INV-GS-103) tells us how well-calibrated a thesis
*was* on the historical sample. It doesn't tell us how much we should trust
that calibration *now*. Two thesis with identical Brier scores can have very
different reliability:

- Thesis A: n=2000, IS Sharpe = 0.5, OOS Sharpe = 0.5, calibrated last month.
- Thesis B: n=80, IS Sharpe = 0.5, OOS Sharpe = 0.0, calibrated 18 months ago.

Both report the same point estimate but only A is safely usable today. The
5-component confidence_v2 captures that distinction.

## 5 components (each ∈ [0, 1])

| # | Component               | Formula                                               | Intuition                            |
|---|-------------------------|-------------------------------------------------------|--------------------------------------|
| 1 | sample_quality          | `log(min(n, 1000)) / log(1000)`                       | Diminishing returns above n=1000.    |
| 2 | effective_size_factor   | `sqrt(n / (n + 50))`                                  | Bayesian shrinkage anchored at n=50. |
| 3 | score_stability         | `1 - std(rolling_aucs) / mean(rolling_aucs)`          | High variance across quarters → low. |
| 4 | return_consistency      | `1 - |IS_sharpe - OOS_sharpe| / max(|IS_sharpe|, 0.1)`| OOS gap collapses this.              |
| 5 | recency_quality         | `exp(-days_since_last_calibration / 90)`              | Half-life ≈ 62 days.                 |

Each component is clamped to [0, 1].

## Composite (geometric mean)

```
composite_confidence = (c1 · c2 · c3 · c4 · c5) ^ (1/5)
```

Geometric mean gives **weakest-link** behaviour: a single weak component
(e.g., `n=0` or stale calibration) collapses the composite confidence to
near zero, regardless of the other four. This is the desired behaviour for
a quality gate — we don't want to upweight a thesis with great Brier and
n=2000 if its OOS Sharpe completely diverged from IS Sharpe.

## Composite weight rule (INV-GS-112)

```python
final_weight = brier_weight × confidence_v2.composite_confidence
```

This multiplication is performed inside `predictor.composite._weight_for_v2`
and consumed by `predictor.composite._compute_masses` for the directional
ensemble.

## CLI rendering

```
E_FUNDAMENTAL          ^   +3.00  (AUC 0.550, n=120, conf_v2=0.801)
    conf_v2 breakdown: sample=0.69 eff=0.84 stab=1.00 cons=0.80 recency=0.71
```

## `confidence_v2_from_calibration` — synthesis defaults

`ThesisCalibration` only stores `auc`, `sharpe`, `n_samples`, `oos_degradation`,
and the calibration window. The helper synthesizes the missing fields:

- `is_sharpe` defaults to `cal.sharpe`.
- `oos_sharpe` defaults to `cal.sharpe * (1 - cal.oos_degradation)`.
- `days_since_last_calibration` defaults to `(today - cal.period_end).days`.
- `rolling_aucs` defaults to `(cal.auc,)` — single point → score_stability = 1.0.

Once the calibration table starts persisting rolling-quarter AUCs (a future
v1.5 enhancement), the synthesis becomes lossless.

## Edge cases

- **n = 0** → sample_quality = 0, effective_size_factor = 0 → composite ≈ 0
  → final_weight ≈ 0 (placeholder thesis collapses to base rate).
- **OOS Sharpe missing** → return_consistency uses `cal.oos_degradation` as
  proxy.
- **`is_sharpe ≈ 0`** → denominator floor of 0.1 prevents division blow-up.
- **Negative `days_since_last_calibration`** → clamped to 0 (no negative age).

## How it interacts with dca_sizing

`Prediction.dca_sizing` (INV-GS-111) is computed from the *post-confidence_v2*
Prediction, so a thesis with low confidence_v2 already collapses to the base
rate. The S component of W = 0.30·R + 0.25·T + 0.25·V + 0.20·S is derived
from `edge_over_baseline_pp`, which is itself confidence_v2-modulated. This
prevents weak or stale thesis from inflating the sizing tier.
