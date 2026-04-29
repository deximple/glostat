---
name: Alpha thesis proposal
about: Propose a new thesis to validate using the GLOSTAT framework
title: "thesis: <one-line summary>"
labels: ["thesis", "discussion"]
---

> Read `docs/post_mortem/SPRINT5_FAIL_post_mortem.md` first. It explains
> which thesis class the framework already refuted and why.

## Thesis (one paragraph)

<what is the alpha hypothesis? what is the directional claim? on what
universe? on what horizon? what is the entry / exit definition?>

## Pre-existing evidence

- [ ] Cited paper(s) / blog post(s) with reproducible backtests:
  - <link>
  - <link>
- [ ] Open-source implementation that we can port or wrap:
  - <link>
- [ ] None — purely original (acknowledge this is a higher bar)

## Universe + horizon

- Universe: <e.g. S&P 500, Russell 2000, sector ETFs, options on QQQ>
- Horizon: <intraday / 1d / 1–7d / 1–30d / 1q / 1y>
- Estimated all-in cost (bps): <commission + spread + slippage>

## Data sources

- [ ] Available via existing free-stack clients (yfinance / SEC EDGAR)
- [ ] Needs new client: <name>, free / paid, requires consent? (Phase 2+)

## Proposed Expert(s)

- `E_<name_1>`: <signal model>
- `E_<name_2>`: <signal model>

## How would the kill criteria look?

- Sharpe min: <e.g. 0.8 — explain if different from default>
- AUC min: <e.g. 0.62>
- OOS degradation max: <e.g. 0.30>
- Cost-passed band: <e.g. [40%, 60%]>

## Risk that this thesis is alpha-absent

<be honest. if this looks like the v0.6 "formulaic composite on megacaps"
shape, say so and explain what's different this time>

## What success looks like

- Hindcast PASS on 90 days IS / 30 days OOS, then …
- A 6-month live paper-trade window with Sharpe ≥ <X>
