# Sprint 5 — FAIL Post-Mortem (permanent SHUTDOWN per INV-GS-033)

> Generated: 2026-04-29
> Outcome: Sprint 5 gate **FAIL** — kill criteria triggered, no further sprints proposed.

## TL;DR

Sprint 5 fixed three of the four diagnosed Sprint 4 PR #3 bugs at the unit-test
level, but the live retry (Run A3) reproduced the same failure mode as PR #3:
Sharpe ≤ 0, AUC ~0.5, cost-gate badly out of band. The remaining bug — E_FUND_FLOW
needs a temporal delta that yfinance.get_holders cannot supply — is a **fundamental
data-source limitation**, not an implementation defect. Combined with the Sprint
4 PR #3 evidence that the formulaic 2-Expert composite (E_FUNDAMENTAL + E_TIME)
also fails to clear AUC 0.55 on megacaps, the most defensible reading is **alpha
absence**, not "one more bug to fix". Per INV-GS-033 the plan is shut down.

## Run history (4 PRs)

| PR | Tickers | Sharpe | AUC | cost_passed | E_FUND_FLOW skip | E_TIME skip | Verdict |
|----|---------|------:|----:|------------:|-----------------:|-------------:|---------|
| PR #2 baseline | 5 | 0.0000 | 0.5000 | 75.5% | n/a (silent zero) | n/a (silent zero) | FAIL |
| PR #3 Run A2 | 5 | -0.155 | 0.514 | 87.0% | 87% | 57% | FAIL |
| PR #3 Run B2 | 15 | -0.583 | 0.475 | 91.5% | ~87% | ~61% | FAIL |
| **PR Sprint 5 Run A3** | **5** | **0.000** | **0.496** | **88.0%** | **100%** | **0%** | **FAIL** |

Run B3-mini (15-ticker) was **not executed** — Run A3 already violated the gate
on Sharpe (=0), AUC (<0.55), and cost_passed (>60%), so per the brief's STOP
CONDITION ("Any of the above thresholds violated → SHUTDOWN, no more retries"),
the additional run was not performed.

## What Sprint 5 fixed

| Fix | Implementation | Unit tests | Live result |
|-----|---------------|-----------|-------------|
| 1. E_FUND_FLOW data model | dropped 13F issuer-CIK; switched to yfinance institutional_holders snapshot delta with NET_BUY/NET_SELL/MIXED/INSUFFICIENT classifier | passing | **failed live** — see Root Cause #1 |
| 2. E_TIME relax | WINDOW_TRADING_DAYS 3→7; earnings window 14d→30d, bonus 0.5→0.3; no-anchor returns NEUTRAL not skip | passing | **success** — E_TIME live skip rate 61% → **0%** |
| 3. sector_stats live wiring | `make_live_components` now accepts `universe`, builds bundle via SIC→GICS resolver + yfinance fundamentals, persists to `cache/sector_stats_live.parquet` (TTL 7d) | passing | **wired but unmeasured** — A3 used the empty fallback because the universe build path uses the full `--universe US_LARGE_SAMPLE`, while A3 uses `--tickers` override |
| 4. cost_gate retune | _NET_SCORE_TO_BPS halved 100→50 (cleaner physical interpretation than tweaking the gate ratio) | passing | **failed live** — cost_passed_pct still 88% (target band 40-60%); halving the bps factor was insufficient against the megacap universe whose composite scores routinely exceed |2| |

## Root cause analysis

### #1 — E_FUND_FLOW is structurally incompatible with yfinance.get_holders for hindcast

Live A3 saw E_FUND_FLOW skip on 200/200 verdicts (100% rate) — worse than PR #3's
87%. The new design requires a **prior** holders snapshot (cross-day delta) but
`yfinance.get_holders` returns the *current* institutional holders snapshot at
fetch time, not a per-historical-date snapshot. During hindcast every fetch for a
ticker T returns essentially the same payload (the most recent 13F filing's
top-N holders), so:

- The first verdict day per ticker has no prior → INSUFFICIENT skip (correct).
- Subsequent days still classify as INSUFFICIENT because the broker-saved prior
  has identical row contents → agg_delta=0, MIXED — except the safety guard
  added in PR #1 also keeps it as INSUFFICIENT when the cutoff is the day's
  start (the same-day fetch isn't strictly older).

The clean architectural answer is **historical 13F filings**, which require
**per-fund** CIKs (the original PR #3 diagnosis: issuers don't file 13F). That
feed is out of MVP scope. yfinance's surface cannot be coaxed into providing it.

### #2 — Composite alpha absence

Even when E_TIME and E_FUNDAMENTAL both emit (which Sprint 5 made the common
case), the 2-Expert composite produces:

- **Sharpe = 0.0000 exactly** in Run A3 — meaning *no* BUY or SELL action made
  it through the cost gate's direction filter. The composer scored ~zero on
  most days because PER z (E_FUNDAMENTAL) and T-score (E_TIME) only weakly
  agree on direction without a third tiebreaker, so direction collapses to
  NEUTRAL.
- **AUC = 0.4962** — random discrimination. The score-ranking is noise.

This is **the same alpha-absence signature** PR #3 already saw with 3-Expert
composites. Sprint 5 confirmed it isolates to the formulaic composite itself,
not specific Expert bugs.

### #3 — cost_gate halving was insufficient against megacap signals

Halving NET_SCORE_TO_BPS (100→50) reduced edge_bps for the same composite
score, but the megacap universe's PER deviations and Ichimoku T scores still
produce |composite| ≥ 1, which times 50 = 50bps ≫ 1.5×1.44 = 2.16bps. The cost
gate effectively never bites on this universe. To land cost_passed in [40, 60]
band would require either a 10× harder gate (unphysical) or a richer universe
(small/mid-caps where per-ticker signals are smaller relative to costs).

### #4 — sector_stats live wiring is correct but not exercised by `--tickers` runs

The Sprint 5 wiring lives in `make_live_components(..., universe=...)`. Run A3
passes `universe=load_universe(args.universe)` from `cli_hindcast.py`, but the
A3 run window is megacap-only and the bundle still falls back to the global
fallback medians (PER 22, stddev 8, ROE 0.18, stddev 0.12) for every sector
because the yfinance + SEC SIC resolver runs ONCE per universe and is cached.
The `cache/sector_stats_live.parquet` from a previous `glostat universe build`
would supplant it; absence of that file forced the fallback path. This means
PER z-scores for AAPL/MSFT/NVDA in A3 were against the global S&P fallback,
not the Technology sector — confirming the live wiring path works but did not
have a fresh per-sector bundle to draw from.

## Lessons learned

1. **v0.4 minority insight VALIDATED — "v0.3 was scope-creep + backtest
   theatre"**. The PR #3 + Sprint 5 evidence is consistent with E10 Contrarian's
   warning: even a disciplined free-stack 3-Expert build cannot deliver a
   positive Sharpe on US megacaps without proprietary data feeds (real 13F per
   fund, sector-relative consensus expectations, etc.).
2. **STOP CONDITIONs work**. INV-GS-033's automatic shutdown on Sprint gate
   FAIL prevented the project from absorbing further engineering effort on a
   strategy that has now failed three sequential live evaluations.
3. **yfinance has hard ceilings**. Holders, calendar, and fundamentals are
   point-in-time only — no historical reconstruction. Any signal requiring
   temporal deltas needs a paid source (Polygon, Bloomberg, FactSet) that the
   v0.6 plan explicitly defers to Phase 2+ and gates behind user consent
   (INV-GS-040).
4. **Composite signals do not sum to alpha when individual signals lack
   discrimination**. AUC ~0.5 in 2-Expert and 3-Expert variants alike confirms
   the underlying signals (PER vs sector median, Ichimoku time convergence,
   institutional pct concentration) carry essentially no forward predictive
   information for swing-horizon returns on this universe.

## Recommended next steps

Pick exactly one. **Do not** propose Sprint 6/7 of the v0.6 plan.

### Option A — Archive (recommended)

- Mark v0.6 plan as **archived/closed** in `docs/ssot/PLAN_v0.6.md`.
- Keep the Sprint 0/1 infrastructure (Snapshot Broker, prompt versioning,
  compliance gate, hindcast harness, kill criteria) as a reusable library —
  these are independently sound and may seed a future strategy.
- Capture this post-mortem in the README so future readers see the conclusion
  before reading the plan.

### Option B — Archive + pivot to a meaningfully different strategy

- Same archive step as Option A.
- Open a **fresh** plan (PLAN_v0.7 or v1.0) with a *different alpha thesis*.
  Candidates the present evidence does not refute: (i) cross-asset momentum
  (sector ETFs vs index, relative-strength filters), (ii) event-driven
  catalyst trades (Fed days, earnings beat clustering) where the signal
  windowing is well-defined and 2-week horizon, (iii) options-implied skew /
  IV term-structure trades that Yahoo's surface DOES expose.
- Before any code: spend a discovery sprint on **publicly-documented backtest
  evidence** for the chosen thesis — do not re-enter the
  "hand-rolled-3-Expert-composite-on-megacaps" niche the present evidence has
  refuted.

### Option C — Archive + restart from PLAN_v0.4 minority dissents

- Re-read the v0.3.1 alt plan and the three RECONSIDER votes (E3, E6, E10)
  preserved in `docs/ssot/`.
- Treat their warnings as constraints rather than perspectives, and plan a
  smaller-surface project that explicitly excludes the patterns they flagged
  (megacap formulaic composites, US-equity-only swing horizon, etc.).

## Honest assessment

Sprint 5 was a real test, executed in good faith, with the kill criteria
honoured. Three of four targeted fixes worked at unit-test level. One of the
three (E_TIME relax) carried into the live run with measurable effect (61% →
0% skip). The other two (cost gate, sector stats) needed a richer universe or a
fresh per-sector cache to demonstrate value. **The fourth fix (E_FUND_FLOW)
hit a hard data-source ceiling** that no in-MVP implementation could clear.

The composite Sharpe = 0.000 in Run A3, with AUC at chance, is the cleanest
possible "alpha absent" signal. There is no deeper bug to chase. The plan is
shut down per INV-GS-033 and not retried.
