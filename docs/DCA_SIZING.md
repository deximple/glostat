# DCA Sizing — INV-GS-111 (v1.4 N3)

> **STATUS: ACTIVE v1.4.** TITAN L4 W-value scheduling adapted to GLOSTAT
> prediction-tool framing. Output is INFORMATION ONLY (calibration-derived
> sizing tier %); does NOT constitute a BUY/SELL recommendation.
> INV-GS-101 preserved. INV-GS-104 disclaimer reinforced.

## Why DCA sizing on a prediction tool?

GLOSTAT v1.0 reframed away from BUY/SELL action output (INV-GS-101). But
calibration data still has a usable shape: a thesis with high `n_samples`,
stable AUC, and clean OOS Sharpe carries more usable signal than a thesis with
n=80 and degraded OOS. The W-value (TITAN L4) is one way to *summarize* that
strength as a tier band, which the user can choose to interpret as a sizing
suggestion if they decide to enter a position. We never tell the user to enter.

## Formula

```
W = 0.30·R + 0.25·T + 0.25·V + 0.20·S       (cap 3.5)
```

| Letter | Meaning                           | GLOSTAT mapping                                                      |
|--------|-----------------------------------|----------------------------------------------------------------------|
| R      | regime / macro fit                | sum of \|value\| over active regime-themed thesis (E_MACRO_KR / E_FOMC_DRIFT / E_FX_CARRY) |
| T      | time / convergence catalyst       | sum of \|value\| over active time-themed thesis (E_TIME / E_TIME_KR / E_PEAD)              |
| V      | valuation / fundamentals          | sum of \|value\| over E_FUNDAMENTAL / E_FUNDAMENTAL_KR                                       |
| S      | composite signal strength         | edge_over_baseline_pp scaled into [0, 2]                              |

Each component is clamped to its TITAN range (R ≤ 3, T ≤ 2, V ≤ 3, S ≤ 2),
then `W` is clamped to [0, 3.5].

A "neutral macro" (no R-themed thesis active) defaults to R=1.0 so the W
floor isn't artificially zeroed by the absence of a macro signal — this
mirrors TITAN's behaviour where the macro engine returns a mid-band score
when nothing is firing.

## Tier table

| W band          | Tier         | Suggested entry % (of user-allocated capital) |
|-----------------|--------------|-----------------------------------------------|
| W < 0.8         | wait         | 0%                                            |
| 0.8 ≤ W < 1.2   | explore      | 7%                                            |
| 1.2 ≤ W < 1.8   | base         | 12.5%                                         |
| 1.8 ≤ W < 2.5   | active       | 22.5%                                         |
| W ≥ 2.5         | aggressive   | 32.5%                                         |

Thresholds are TITAN L4 §6 verbatim. The percentage is **of user-allocated
capital for this ticker**, not of total portfolio.

## INV-GS-111 — what it forbids

- The tier label MUST NOT be displayed as "BUY", "SELL", "STRONG BUY", etc.
- The disclaimer on every `SizingRecommendation` carries the literal substring
  `INV-GS-111` so downstream consumers can trace the compliance chain.
- The `Prediction` dataclass continues to omit `action`, `target_price`,
  `stop_price`, `suggested_size_pct` fields (INV-GS-101 unchanged).
- Sizing is exposed via `Prediction.dca_sizing: SizingRecommendation | None`.
  Default is `None`; it is populated when `predict()` runs and is read-only.

## CLI rendering

```
Sizing tier: BASE (W=1.45, suggested 12.5% if user enters)
  W components: R=1.00 T=1.00 V=3.00 S=0.49
  INFORMATION ONLY — sizing tier reflects prediction strength, not advice
  to enter or size positions. INV-GS-101 + INV-GS-104 + INV-GS-111.
```

## Edge cases

- **W = NaN** → tier "wait", 0% (graceful).
- **W < 0** → clamped to 0.0 (tier "wait").
- **W > 3.5** → clamped to 3.5 (tier "aggressive").
- **No active thesis** → R defaults to 1.0; T/V/S = 0 → W ≈ 0.30 → tier "wait".

## How it interacts with confidence_v2

`build_sizing_recommendation()` reads from the *post-Brier* Prediction.
That Prediction's `up_probability` is already weighted by `confidence_v2`
(INV-GS-112). So a thesis with stale calibration or n=0 already collapses
into the base rate before W is computed — and S falls accordingly. This
keeps W from inflating sizing on weakly-supported predictions.
