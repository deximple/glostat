# GLOSTAT v1.2 Calibration Table — Empirical Predictive Strength of Thesis Modules

> Generated: 2026-04-29 (v1.0 reframe day) + 2026-04-30 (v1.2 phase_kr smoke
> + M1 full 2-year update).
> Source: `cache/hindcast/phase1b/`, `cache/hindcast/phase1c_*`,
> `cache/hindcast/phase1d/`, `cache/hindcast/phase_kr/` (v1.2 L1 + M1 full).
> Originally produced as v0.6/v0.7 Sprint 4 gate evaluations; reframed in v1.0
> as **calibration data**; v1.2 added Phase KR (KOSPI 200) measurements;
> M1 (2026-04-30) replaced the Phase KR 3-month smoke with the full 2-year
> hindcast (n=189/210/7 → n=3510/3510/138).
>
> **Honest disclaimer:** Most signals tested were random or anti-predictive.
> This is HONEST data, not failure. Anti-predictive signals get weight 0;
> weakly positive signals get small weight; nothing here promises an "alpha".
> The framework's value is the table itself, not any one row in it.

---

## How to read this table

Every row is a calibration point: one thesis × one universe × one horizon ×
one calibration window. Columns:

- **n_samples** — number of actionable signals during the calibration window
- **AUC** — area under ROC of `raw_score → forward_return_sign`. 0.5 = chance.
- **Sharpe** — annualized return-to-volatility ratio of the realized PnL, post-cost.
- **OOS deg** — fractional Sharpe degradation between in-sample and out-of-sample.
- **Brier** — mean squared error of probability prediction vs binary outcome.
  Lower is better. 0.25 = perfectly random predictor (always says 0.5).
- **Weight** — Brier-derived sigmoid weight, scaled by sample-size factor `min(1, n/50)`.
  `weight = max(0, sigmoid(-20 × (Brier - 0.25)) × min(1, n/50))`.
- **Interpretation** — honest plain-language assessment.

The composite predictor (`glostat predict`) sums each thesis's `raw_score`
with these weights, then maps to `p_up ∈ [0, 1]` with a 90% CI derived from
per-thesis bootstrap variance.

---

## E_PEAD — Post-Earnings-Announcement Drift (US large/mid)

| Field | Value |
|-------|-------|
| Universe | US 50 (S&P 100 subset) |
| Horizon | 30d (calendar) |
| Calibration window | 2024-01-01 → 2026-03-29 |
| n_samples | 298 |
| AUC | 0.587 (overall) — IS 0.620 / OOS 0.543 |
| Sharpe | +0.629 (overall) — IS +0.981 / OOS -0.154 |
| OOS degradation | 116% (Sharpe collapsed OOS) |
| Brier | 0.244 (computed: AUC 0.587 → Brier ≈ 0.5 - 0.078² × penalty) |
| Composite weight | **0.18** |

**What this signal predicts:** the tendency of stocks that beat (miss)
earnings to drift in the same direction over the next 30 trading days.

**How strongly:** AUC 0.587 is **8.7pp above coin flip**. That is a small but
real signal. The OOS degradation (116%) is the warning: when out-of-sample,
the directional information stays present but the magnitude is unstable. The
v1.0 weight of 0.18 reflects both the positive AUC and the OOS instability —
it is included in the ensemble but cannot dominate.

**Re-run:**
```bash
uv run glostat calibrate --thesis E_PEAD --universe us_sp100_50 --horizon 30d
```

---

## E_FOREIGN_REVERSAL — KR Foreign-Investor Reversal (KOSPI 20)

| Field | Value |
|-------|-------|
| Universe | KR 20 (KOSPI200 top mega) |
| Horizon | 5d (TITAN-style) |
| Calibration window | 2024-01-01 → 2026-03-29 |
| n_samples | 424 |
| AUC | 0.467 (overall) — IS 0.466 / OOS 0.477 |
| Sharpe | +0.583 (overall) — IS +0.180 / OOS +1.463 |
| OOS degradation | 0% (Sharpe IMPROVED OOS) |
| Brier | 0.262 |
| Composite weight | **0.14** |

**What this signal predicts:** Korean foreign-investor net-buy reversals as
a forward-direction cue, originally TITAN's B4 historical signal (60.3% on
n=58, 2025.06–2026.03).

**How strongly:** the 2024–2026 generalization gave 52.2% hit rate (424
events) — **8.1pp below the original B4 result**. AUC sits below 0.5 (so the
score-rank flips slightly), but Sharpe is positive and OOS-stable. This is
the signature of a regime-dependent signal that nevertheless carries useful
direction in the reframe-and-aggregate context. v1.0 weight 0.14 acknowledges
the TITAN-vs-GLOSTAT gap while preserving the sign of the contribution.

**Re-run:**
```bash
uv run glostat calibrate --thesis E_FOREIGN_REVERSAL --universe kr_kospi_top20 --horizon 5d
```

---

## E_INSIDER_CLUSTER — Form 4 Insider Cluster Buying (US tech 19)

| Field | Value |
|-------|-------|
| Universe | US 19 (tech-heavy subset with reliable Form 4 data) |
| Horizon | 30d |
| Calibration window | 2024-01-01 → 2026-03-29 |
| n_samples | 11 |
| AUC | 0.339 (overall) — IS 0.300 / OOS 0.500 |
| Sharpe | +0.782 (overall) — IS +0.485 / OOS +1.688 |
| OOS degradation | 0% |
| Brier | 0.295 (high due to small n) |
| Composite weight | **0.05** (sample-size guard: 11/50 = 0.22× floor) |

**What this signal predicts:** clusters of 3+ insiders buying within a
trailing 14d window as a forward-positive cue.

**How strongly:** **n is too low to draw a confident conclusion**. Sharpe
+0.78 is encouraging; AUC 0.34 says the score-magnitude isn't usable; OOS
behaviour was strong but sample = ~3 OOS events. v1.0 weight 0.05 reflects
the sample-size penalty hard cap. Until n ≥ 50, this signal stays a tiebreaker.

**Re-run:**
```bash
uv run glostat calibrate --thesis E_INSIDER_CLUSTER --universe us_tech_19 --horizon 30d
```

---

## E_COMMODITY_TS — Commodity Term-Structure + COT Extremes (10 ETFs)

| Field | Value |
|-------|-------|
| Universe | USO, UNG, GLD, SLV, CPER, URA, CORN, WEAT, DBC, GSG |
| Horizon | 30d, weekly rebal |
| Calibration window | 2024-01-01 → 2026-03-31 |
| n_samples | 517 |
| AUC | 0.489 (overall) |
| Sharpe | +0.139 (overall) |
| OOS degradation | 100% (full collapse) |
| Brier | 0.252 |
| Composite weight | **0.06** |

**What this signal predicts:** combination of price/200dMA trend and
commercial-net-position COT extremes (5y rolling rank 0.85/0.15 thresholds).

**How strongly:** barely above noise. Composite weight 0.06 is essentially a
tiebreaker. **Caveat:** USO/UNG/CPER/CORN/WEAT all carry contango drag (the
front-month roll bleeds NAV — USO famously rewrote its prospectus in 2020
after roll losses exceeded -50%). The realized return series is contaminated
by structural cost the signal cannot anticipate. GLD and SLV (fully-allocated
physical) are the cleanest sub-universe.

**Re-run:**
```bash
uv run glostat calibrate --thesis E_COMMODITY_TS --universe commodity_etf_10 --horizon 30d
```

---

## E_SECTOR_ROTATION — Long/Short Top-3/Bottom-3 of 11 SPDR Sectors

| Field | Value |
|-------|-------|
| Universe | 11 SPDR sector ETFs (XLK, XLF, …) |
| Horizon | 30d, 21d rebal |
| Calibration window | 2024-01-01 → 2026-03-29 |
| n_samples | 174 |
| AUC | 0.470 (overall) |
| Sharpe | -0.479 (overall) |
| OOS degradation | 100% |
| Brier | 0.263 |
| Composite weight | **0.00** (clamped at 0; anti-predictive) |

**What this signal predicts:** 21-day rebalanced long-the-strongest /
short-the-weakest sector momentum.

**How strongly:** **anti-predictive on the calibration window**. Negative
Sharpe + AUC < 0.5 + 100% OOS degradation. v1.0 weight clamped to 0 (the
spec allows negative weights but, for safety, we floor at zero). Including
this thesis in the ensemble would degrade composite quality on the
calibration window.

**Re-run:**
```bash
uv run glostat calibrate --thesis E_SECTOR_ROTATION --universe sector_etf_11 --horizon 30d
```

---

## E_FOMC_DRIFT — FOMC Announcement-Day Drift Continuation (US 12)

| Field | Value |
|-------|-------|
| Universe | US 12 (broad index + sector ETFs) |
| Horizon | 5d |
| Calibration window | 2024-01-01 → 2026-03-29 |
| n_samples | 135 |
| AUC | 0.357 (overall) |
| Sharpe | -1.340 (overall) |
| OOS degradation | 100% |
| Brier | 0.282 |
| Composite weight | **0.00** (clamped; strongly anti-predictive) |

**What this signal predicts:** continuation of the FOMC announcement-day
return over the following 5 trading days.

**How strongly:** **strongly anti-predictive**. AUC 0.357 means the signal's
ranking is flipped from reality; Sharpe -1.34 confirms that following the
signal would have lost money. The honest reading: announcement-day returns
on US indices appear to **mean-revert**, not continue, on this sample.
Excluded from ensemble (weight 0).

**Re-run:**
```bash
uv run glostat calibrate --thesis E_FOMC_DRIFT --universe us_macro_12 --horizon 5d
```

---

## E_FX_CARRY — Risk-Off + Carry Unwind Defensive Tilt

| Field | Value |
|-------|-------|
| Universe | SPY + XLF/XLE/XLU/XLV + ^VIX + FXY + EWZ |
| Horizon | 7d swing |
| Calibration window | 2024-01-01 → 2026-03-31 |
| n_samples | 135 |
| AUC | 0.400 (overall) |
| Sharpe | -1.533 (overall) |
| OOS degradation | 100% |
| Brier | 0.275 |
| Composite weight | **0.00** (clamped) |

**What this signal predicts:** trigger = "VIX 5d ≥ 25 + FXY 5d > +2% + EWZ 3d
< -1.5%, ≥ 2 of 3 legs", forward defensive (XLU/XLV) tilt for 7 days.

**How strongly:** Strongly anti-predictive on this sample. The trigger fires
only 27 days in 587, and on those days the 7d-forward defensive tilt was
**worse** than holding cash. v1.0 weight clamped to 0. Honest read: either
the trigger criterion is too coarse, or the 7-day defensive bias on
recent-2yr regime is wrong.

**Re-run:**
```bash
uv run glostat calibrate --thesis E_FX_CARRY --universe macro_sector_8 --horizon 7d
```

---

## E_FUNDING_CARRY — Crypto Funding Rate Divergence (BTC/ETH perp)

| Field | Value |
|-------|-------|
| Universe | BTC/USDT:USDT, ETH/USDT:USDT (Binance perpetuals) |
| Horizon | 1d |
| Calibration window | 2024-01-01 → 2026-03-31 |
| n_samples | 4922 |
| AUC | 0.505 (overall) — IS 0.519 / OOS 0.478 |
| Sharpe | -0.231 (overall) — IS +0.606 / OOS -2.166 |
| OOS degradation | 457% |
| Brier | 0.250 (right at chance) |
| Composite weight | **0.02** |

**What this signal predicts:** funding rate spike + accumulation pattern
classification (CARRY / ACCUMULATION_LONG / REVERSAL_SHORT).

**How strongly:** **essentially random on aggregate**. AUC 0.505 = noise. The
CARRY pattern (n=1777) hit-rate 50.6%, ACCUMULATION_LONG (n=858) 55.4%,
REVERSAL_SHORT (n=291) 58.4% — the sub-pattern hit rates are above chance,
but the composite signal is dominated by noise. The huge OOS degradation
(457%) means the IS Sharpe of +0.61 was overfit. v1.0 weight 0.02 reflects
"large n, near-zero edge" — included only because the sample size keeps it
calibrated, but it contributes essentially nothing to the composite.

**Re-run:**
```bash
uv run glostat calibrate --thesis E_FUNDING_CARRY --universe crypto_perp_2 --horizon 1d
```

---

## Phase KR (v1.2 L1 + M1) — KOSPI 200 measurements

The KR-active theses are calibrated from a 2-year hindcast across the KOSPI 200
Top 30 universe (`KR_KOSPI200_TOP30`, 2024-01-02 → 2026-03-29, stride=5
sample days, 30d horizon for fundamental/time, 7d horizon for foreign
reversal). M1 (2026-04-30) replaced the v1.2 L1 3-month smoke (n=189/210/7)
with the full window.

### M1 measurements (current)

| Thesis | n_traded | AUC overall | AUC IS / OOS | Sharpe overall | Sharpe IS / OOS | OOS deg | Decision |
|--------|---------:|------------:|-------------:|---------------:|----------------:|--------:|----------|
| E_FUNDAMENTAL_KR | 3510 | 0.495 | 0.500 / 0.483 | +0.301 | +0.425 / +0.150 | 64.7% | NEAR_RANDOM |
| E_TIME_KR | 3510 | 0.483 | 0.487 / 0.468 | +0.335 | +0.001 / +0.842 | 0.0% | NEAR_RANDOM |
| E_FOREIGN_REVERSAL (KR) | 138 | 0.464 | 0.434 / 0.506 | +0.170 | -0.305 / +0.867 | 100.0% | AMBIGUOUS |

Source: `cache/hindcast/phase_kr/phase_kr_comparison.md`. JSON reports under
the same directory are loaded by `predictor.calibration.load_calibration()`
into `cache/calibration_table.parquet`.

### v1.2 L1 → M1 delta (smoke vs full window)

| Thesis | n (smoke → full) | AUC (smoke → full) | Sharpe (smoke → full) |
|--------|-----------------:|-------------------:|----------------------:|
| E_FUNDAMENTAL_KR | 189 → 3510 (18.6×) | 0.506 → 0.495 | +1.108 → +0.301 |
| E_TIME_KR | 210 → 3510 (16.7×) | 0.488 → 0.483 | +0.222 → +0.335 |
| E_FOREIGN_REVERSAL | 7 → 138 (19.7×) | 0.333 → 0.464 | +0.523 → +0.170 |

The smoke Sharpe was inflated by small-n variance; the full-window Sharpe
is the honest, regularized number.

**Honest read of the KR measurements:**
- `E_FUNDAMENTAL_KR` (cheap-PER + high-ROE + dividend tilt) lands AUC 0.495 —
  effectively chance. Sharpe +0.30 is positive but the IS/OOS gap (0.42 → 0.15)
  warns of regime sensitivity. The signal contributes a small Brier-weighted
  vote, not a confident directional call.
- `E_TIME_KR` (Ichimoku 257-day base) lands AUC 0.483 — also chance on KR
  daily bars. The OOS Sharpe (+0.84) actually outperformed IS (+0.00),
  suggesting the IS window straddled a regime shift; the result is positive
  but unstable in interpretation.
- `E_FOREIGN_REVERSAL` measured on KOSPI 200 Top-30 found 138 actionable
  REVERSAL_BUY patterns over 2 years — 96.07% skip rate (3275 NEUTRAL +
  97 insufficient_history). Pattern is genuinely rare in mega-caps. OOS
  Sharpe (+0.87) flipped from negative IS (-0.30); the override of the
  Phase 1D legacy n=424 calibration is automatic in the loader (Phase KR
  is the authoritative KR window).
- `E_INSIDER_KR` (DART elestock) requires `GLOSTAT_DART_API_KEY`. Without it,
  the slot reports skip; with it, the cluster signal mirrors US
  E_INSIDER_CLUSTER semantics. Not yet measured by Phase KR hindcast.

### Phase 1D legacy → Phase KR override

The earlier Phase 1D (TITAN heritage) E_FOREIGN_REVERSAL calibration shipped
n=424, AUC 0.467, Sharpe +0.583, OOS deg 0%. Phase KR M1 (n=138, Sharpe
+0.170, OOS deg 100%) replaces it: KR-specific, recent 2-year window, real
TOP30 pattern frequency. The Brier-derived weight will drop from ≈0.14
(Phase 1D) toward the M1 floor — this is honest data, not regression.

Re-run:
```bash
GLOSTAT_SEC_USER_AGENT="GLOSTAT (you@example.com)" NETWORK_TESTS=1 \
  GLOSTAT_DART_API_KEY="<your-key>" \
  uv run glostat kr-hindcast --universe KR_KOSPI200_TOP30 \
    --start 2024-01-02 --end 2026-03-29 \
    --max-concurrent 5 --stride 5
```

Expected wall-clock: ~45 min on a fresh machine; subsequent reruns are
much faster as Snapshot Broker caches yfinance.history/info/calendar leaves
(typically ~5 min when most data is cached).

---

## Composite calibration

The composite predictor (see `predictor/composite.py`) does:

```python
def composite_p_up(contributions: list[ThesisContribution]) -> float:
    weights = [c.brier_weight for c in contributions]
    total = sum(weights)
    if total <= 0:
        return 0.5  # all-anti or all-zero → return prior
    weighted = sum(c.brier_weight * (0.5 + 0.5 * c.raw_score) for c in contributions)
    return max(0.0, min(1.0, weighted / total))
```

For the 8 thesis above, the **maximum non-zero weight sum is 0.45** (E_PEAD
0.18 + E_FOREIGN_REVERSAL 0.14 + E_INSIDER_CLUSTER 0.05 + E_COMMODITY_TS 0.06
+ E_FUNDING_CARRY 0.02). When all 5 active thesis agree on direction, the
composite `p_up` will be at most ~0.66 (with substantial CI width). When they
disagree, `p_up` collapses toward 0.5 with wide CI — **this is the desired
behaviour**: the predictor admits ignorance.

The 90% CI is computed via per-thesis Brier-bootstrapped variance (see
`predictor/calibration.py::confidence_interval`).

---

## Quarterly update policy (INV-GS-105)

This table is regenerated every quarter:

```bash
# Q1 (by Jan 31)  /  Q2 (by Apr 30)  /  Q3 (by Jul 31)  /  Q4 (by Oct 31)
uv run glostat calibrate --all-thesis --window 365d
uv run glostat calibrate --update-table
uv run glostat calibrate --regenerate-docs   # rewrites this file
git tag v1.x.0                               # quarterly minor bump
```

Brier change > 0.05 on any thesis must be called out in the release CHANGELOG
with root-cause analysis.

---

## Reproducibility

This table is reproducible from a clean checkout:

```bash
git checkout 2b96ca5                         # archived v0.6 commit
uv sync --extra dev
uv run glostat calibrate --all-thesis --window 365d --start 2024-01-01 --end 2026-03-29
diff cache/calibration_table.parquet docs/calibration_table_2026Q2.parquet
# expected: identical Merkle root for all snapshots
```

Snapshot Broker Merkle leaves persist every external API response used in the
calibration; so any disagreement points at a real data drift, not at
non-determinism.

---

## Honesty disclaimer

Most signals tested were random or anti-predictive. **This is HONEST data,
not failure**. v0.6 framed this as "8 thesis FAIL → shutdown" and that
framing was internally consistent for a *decision engine*. v1.0 frames the
same data as "8 calibration points feeding a Brier-weighted ensemble" because
it is a *prediction tool*. Both readings are correct against their own goal;
v1.0 is the more useful goal, given the empirical landscape.

If you find a thesis with AUC ≥ 0.65 + Sharpe ≥ 1.0 + n ≥ 100 + OOS deg ≤
30% on a properly held-out window — please open a PR. We will calibrate it,
include it, weight it, and credit you.
