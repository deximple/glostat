# GLOSTAT v1.7 — New Thesis Ideation (Options IV / Analyst Revision / Insider Velocity)

> Status: ACTIVE 2026-05-02. Empirical post-v1.6.2 finding (only E_PEAD_KR
> AUC 0.5405 carries statistically significant edge in KR megacap) motivates
> a fresh thesis-design round. This doc captures three candidate theses with
> different signal-source families: **derivatives** (options IV skew),
> **analyst behavior** (analyst revision), and **insider behavior**
> (insider transaction velocity).
>
> v1.7.0 ships **E_INSIDER_VELOCITY_KR** as the first skeleton implementation
> (lowest data-cost — extends existing DART E_INSIDER_KR plumbing). The
> other two are documented for future implementation rounds.

---

## Why three new theses?

The 6-thesis KR hindcast (2026-05-02) revealed:
- E_PEAD_KR: real edge (AUC 0.5405, p=0.008)
- 5 of 6 others: noise or anti-predictive

To improve the framework's empirical alpha (currently scoring ~4/10 in the
self-evaluation), we need **more high-quality candidate signals**. The three
ideated below target signal sources GLOSTAT currently does NOT exploit:

1. **Options IV skew** — derivatives market positioning (institutional fear/greed)
2. **Analyst revision** — sell-side consensus changes (information flow)
3. **Insider velocity** — pace + clustering of insider trades (corporate insiders)

---

## Thesis 1 — E_OPTIONS_IV_SKEW

### Signal hypothesis

Options market 25-delta IV skew (puts vs calls) reflects institutional
positioning: rising put skew = rising fear, falling = complacency. Historical
literature (Bakshi+ Madan 2003, Cremers+ Weinbaum 2010) shows skew sometimes
predicts returns, but with substantial noise.

### Data source options

| Source | Cost | Coverage | Notes |
|---|---|---|---|
| yfinance options | Free | US tickers | Basic IV but no historical depth |
| CBOE / ICE | Paid | US/Global | Authoritative but $$ |
| Polygon Options | Paid | US | Cheaper than CBOE |
| Binance options | Free | Crypto | Adjacent universe |
| KOFEX (KOSPI 200 options) | Paid | KR | Single-index, not per-ticker |

For MVP free-stack: **yfinance options chain** for US large-caps only. KR
options exist on KOSPI 200 index but not per-ticker (so no KR direct
implementation).

### Score formula sketch

```python
# Pull current 30d IV chain. Compute 25-delta put IV - 25-delta call IV.
skew = put_iv_25d - call_iv_25d

# Compare to 90d trailing distribution.
percentile = empirical_cdf(skew, history_90d)

# Fear extremes are contrarian: very high put skew → expect rebound.
# Very low put skew (complacency) → expect drawdown.
if percentile >= 0.85:
    raw_score = +clamp((percentile - 0.85) * 10, 0, 1.5)   # contrarian LONG
elif percentile <= 0.15:
    raw_score = -clamp((0.15 - percentile) * 10, 0, 1.5)   # contrarian SHORT
else:
    raw_score = 0  # neutral zone, skip
```

archetype: `contrarian` (mean-reversion expectation around fear extremes).

### Universe

US large-cap only (yfinance options reliable on top 50). Refining/extending
to mid-cap requires verifying option-chain depth (low OI breaks IV calc).

### Honest expected outcome

Based on academic literature: typical AUC 0.51-0.54 with high variance. Not
expected to dominate, but adds an independent signal source (uncorrelated
with E_PEAD / E_FUNDAMENTAL).

### Implementation cost

~2-3 days:
- Day 1: yfinance options client wrapper + IV chain parser
- Day 2: percentile + scoring + universe gate
- Day 3: hindcast wiring + first measurement

Deferred to v1.8.

---

## Thesis 2 — E_ANALYST_REVISION

### Signal hypothesis

Analyst forward-EPS revisions in the past 30-90 days correlate with future
price moves (Stickel 1991, Womack 1996). Specifically: count of upgrades
minus downgrades, weighted by analyst track-record reputation.

The "analyst revision drift" effect is academically well-documented, with
typical out-of-sample AUC 0.53-0.56 and reasonable Sharpe in liquid names.

### Data source options

| Source | Cost | Coverage | Notes |
|---|---|---|---|
| yfinance analyst recommendations | Free | US/global | Rolling 90d table; no historical depth |
| FactSet Estimates | Paid | Global | Authoritative |
| Refinitiv I/B/E/S | Paid | Global | Industry standard |
| Bloomberg ANR | Paid | Global | Proprietary |
| Estimize | Freemium | US | Crowdsourced revisions |

For MVP free-stack: **yfinance analyst recommendations** + a 30d rolling
window cache to build the time series ourselves.

### Score formula sketch

```python
# Fetch analyst recommendations changes in last 30 days.
recs = yfinance.recommendations(ticker, period="30d")

# Net revision count (upgrades - downgrades).
net = sum(1 for r in recs if r.action == "upgrade") - \
      sum(1 for r in recs if r.action == "downgrade")

# Weight by analyst track record (TR) — placeholder uniform for MVP.
weighted_net = sum(tr_weight(r.firm) * direction(r.action) for r in recs)

# Normalize to ±2.0.
raw_score = clamp(weighted_net / max_observed_30d, -2.0, +2.0)
```

archetype: `continuation` (revisions tend to come in clusters; one upgrade
often presages more).

### Universe

US Russell 2000 + KOSPI 200 (yfinance covers both with reasonable
analyst-recommendation depth). KOSDAQ likely sparse.

### Honest expected outcome

This is the most data-efficient and well-documented of the three. AUC
0.53-0.56 plausible. **Highest expected ROI of the three new theses.**

### Implementation cost

~1-2 days:
- Day 1: yfinance recommendations client + 30d rolling cache
- Day 2: scoring + universe gate + hindcast wiring

Deferred to v1.8 (after E_INSIDER_VELOCITY_KR proves the pattern).

---

## Thesis 3 — E_INSIDER_VELOCITY_KR (skeleton in v1.7.0)

### Signal hypothesis

Existing E_INSIDER_KR uses DART elestock data to detect "cluster" — multiple
insider transactions of the same direction within a window. **E_INSIDER_VELOCITY_KR**
extends this with a different angle: the **rate of acceleration** of insider
buying (or selling).

Hypothesis: if insider buys are accelerating week-over-week (e.g., last 7
days has 3x the buys of the prior 7 days), this signals stronger conviction
than a flat cluster. Conversely, accelerating sells may signal corporate
distress more strongly than aggregate selling.

This is the **derivative of the insider-cluster signal** — first-order vs
the existing zero-order cluster expert.

### Data source

DART elestock (already wrapped in `dart_client.py` for E_INSIDER_KR). No new
data source needed — just a different scoring transformation.

### Score formula sketch

```python
# Pull last 30d insider transactions for the ticker.
txs = await dart.get_executive_transactions(corp_code, days=30)

# Bucket into two 7-day windows: recent_7d and prior_7d (days 7-14 ago).
buys_recent = sum(t.shares for t in txs if t.action == "BUY" and t.day >= today - 7)
buys_prior = sum(t.shares for t in txs if t.action == "BUY" and 14 >= today - t.day > 7)
sells_recent = sum(t.shares for t in txs if t.action == "SELL" and t.day >= today - 7)
sells_prior = sum(t.shares for t in txs if t.action == "SELL" and 14 >= today - t.day > 7)

# Velocity ratio. Positive = accelerating buys; negative = accelerating sells.
buy_velocity = (buys_recent + 1) / (buys_prior + 1)   # +1 smoothing
sell_velocity = (sells_recent + 1) / (sells_prior + 1)

# Net velocity — buys accelerating > sells accelerating = LONG.
net_velocity = log(buy_velocity) - log(sell_velocity)

# Map to score with clip.
raw_score = clamp(net_velocity * 2.0, -2.5, +2.5)
```

archetype: `continuation` (insider conviction acceleration tends to predict
continued direction over 30d).

### Universe

KOSPI 200 (where DART data is densest). Same gate as E_INSIDER_KR.

### Honest expected outcome

Lower bound: extends an already weak signal (E_INSIDER_KR was n=0 in v1.6.2
because DART API key not configured in test runs — needs production key for
real measurement). Upper bound: velocity may be more predictive than
zero-order clustering.

Plausible AUC: 0.51-0.55 if insider behavior in KR is informative; 0.50 if
not (similar to E_FUNDAMENTAL_KR_CYCLICAL outcome).

### Implementation cost

~0.5-1 day (cheapest of the three):
- Hour 1: extend `dart_client.get_executive_transactions` to support 30d window
- Hour 2: write `e_insider_velocity_kr.py` (~150 lines)
- Hour 3: wrapper + calibration backfill + tests
- Hour 4: hindcast wiring (next-iteration, not v1.7.0 scope)

**v1.7.0 ships the skeleton** — expert + score formula + KOSPI 200 gate +
calibration n=0 bootstrap. Live activation requires DART API key. Hindcast
wiring deferred to v1.7.1.

---

## Summary table

| Thesis | Source cost | Implementation | Expected AUC | Priority |
|---|---|---|---|---|
| E_INSIDER_VELOCITY_KR | Free (DART, optional) | 0.5-1 day | 0.51-0.55 | **v1.7.0 (skeleton)** |
| E_ANALYST_REVISION | Free (yfinance) | 1-2 days | 0.53-0.56 | v1.8 |
| E_OPTIONS_IV_SKEW | Free (yfinance options US) | 2-3 days | 0.51-0.54 | v1.9 |

**v1.7.0 scope**: ship E_INSIDER_VELOCITY_KR skeleton + this ideation doc.
The other two are designed-on-paper and queued.

---

## Empirical reality check (P10 reminder)

P10 Contrarian Veteran's prediction was MOSTLY correct in the v1.5/v1.6
sprint: 5 of 6 new theses produced no edge. Adding 3 more theses does NOT
guarantee 3 more wins. Realistic expectation: **0-2 of these 3 will pass
the AUC > 0.52 + p < 0.05 bar**.

That outcome is fine. The framework's design handles it — failed experiments
get weight ≈ 0 via Brier ensemble, successful ones drive prediction
differentiation. The honest reporting layer (statistical disclaimer +
universe note + per-signal p-value) ensures the user sees what worked.

The point is **data accumulation across many experiments**, not betting on
any single thesis to be the alpha source.
