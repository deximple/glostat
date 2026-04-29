# GLOSTAT — Evidence-based Probability Predictor for Global Equities

> **개선된 TITAN의 open-source 진화형 / Calibrated multi-horizon predictions with evidence chains.**
>
> Information tool. Not investment advice. Past calibration ≠ future performance.

---

## Status

> **v1.1 ACTIVE (2026-04-29) — KR support landed (K1).**
> Previous: **v1.0 (2026-04-29) — Reframe of v0.7.**
>
> v0.6/v0.7 framed GLOSTAT as a **decision engine** (BUY/SELL action output)
> and concluded "8 thesis FAIL" against a `Sharpe ≥ 0.8 / AUC ≥ 0.62 / OOS deg
> ≤ 30%` gate. v1.0 reframes the same data as a **prediction tool**: probability
> distribution + 90% CI + per-thesis Brier-weighted contribution + evidence
> chain. The 8-thesis FAIL outcomes are now the **first input rows** of the
> calibration table — `E_PEAD AUC 0.587`, `E_FOREIGN_REVERSAL OOS Sharpe 1.46`,
> `E_FOMC_DRIFT AUC 0.357` (anti-predictor) — all carry honest, sample-aware
> weights.
>
> **Read first**:
> [`docs/post_mortem/SPRINT5_FAIL_post_mortem.md`](docs/post_mortem/SPRINT5_FAIL_post_mortem.md)
> (the v0.6 honest diagnosis), then
> [`docs/ssot/PLAN_v1.0.md`](docs/ssot/PLAN_v1.0.md) (the v1.0 spec), then
> [`docs/CALIBRATION.md`](docs/CALIBRATION.md) (the empirical predictive
> strength table).

---

## What this project IS

- A **calibrated probability predictor** — outputs `Prediction(p_up,
  p_up_lower, p_up_upper, contributing, ...)` with Brier-derived ensemble
  weights per thesis.
- A **deterministic hindcast harness** — turns any thesis into a calibration
  row (Brier + AUC + Sharpe + OOS) with explicit IS/OOS split and
  reproducibility guarantees.
- A **snapshot broker** — every external data response persisted as a parquet
  shard + SQLite index + Merkle leaf, so any prediction can be replayed
  bit-for-bit months later.
- An **open-source research framework** — MIT, fork-friendly, designed so
  third-party thesis authors can plug in and contribute calibration data.
- A **compliance gate** that makes broadcast permanently impossible
  (`ComplianceError` on Telegram / mass-email entry points; INV-GS-024) and
  stamps a personal-use, not-investment-advice disclaimer on every prediction
  (INV-GS-104).
- A **prompt registry** that pins each LLM call to a `sha256` so the prompt
  graph is auditable across versions.
- **45+ numbered invariants** (`INV-GS-001..105`) with a 1:1 unit-test mapping
  and a machine-readable `configs/invariants.yaml`.

## What this project IS NOT

- **Not investment advice.** Use at your own risk. Read the post-mortem first.
- **Not a trading bot.** No BUY/SELL action output (INV-GS-101). No
  target/stop/size output. The `Prediction` dataclass deliberately omits any
  field that prescribes action.
- **Not an alpha-generating decision engine.** v0.6 attempted that, failed
  honestly across 8 thesis. The framework's value is the **honest measurement
  + Brier weighting**, not a guaranteed alpha.
- **Not a black-box predictor.** Every `Prediction` carries a
  `ThesisContribution` chain with calibration window, n_samples, AUC, Brier
  weight, and source IDs.
- **Not a broadcast tool.** `broadcast_telegram` and `mass_email` are inert
  sentinels that always raise (INV-GS-024).
- **Not a multi-user product.** Personal use only.

---

## If you used TITAN, GLOSTAT v1.0 is its open-source global evolution

| Dimension | TITAN | GLOSTAT v1.0 |
|-----------|-------|--------------|
| Markets | KR (KOSPI/KOSDAQ) only | Global (US, KR, FX, commodities, crypto) |
| Output | `STRONG_BUY..STRONG_SELL` action + directive + target/stop | `Prediction(p_up, CI, contributing, evidence_hash)` |
| Compliance | Telegram bot historically active | `broadcast_telegram` raises (INV-GS-024); per-prediction disclaimer (INV-GS-104) |
| Reproducibility | Local cache | Snapshot Broker (Merkle leaf + parquet shard + SQLite) |
| Calibration | Single B4 historical run (60.3% hit) | Quarterly recalibration → `calibration_table.parquet` |
| Weights | Heuristic engine ratios | Brier-score sigmoid weighting (sample-size aware) |
| Distribution | Private repo | MIT open-source |
| Honesty | "PEAD 60%" | "PEAD AUC 0.587, n=298, weight 0.18" |
| Scope discipline | All 9 engines on | Weak thesis auto-weight 0 |

GLOSTAT v1.0 inherits TITAN's engine-ensemble pattern and hindcast-first
discipline, then layers on global coverage, calibrated probability output,
formal reproducibility, and a hard compliance gate.

---

## Supported markets (v1.1)

| Market | MIC | Status | Universe | Data sources |
|--------|-----|--------|----------|--------------|
| US large-cap | XNAS, XNYS | ACTIVE (v1.0) | S&P 500 Top 50 (`sp500_top50.txt`) | yfinance + SEC EDGAR |
| KR (KOSPI) | XKRX | ACTIVE (v1.1 K1) | KOSPI 200 (`kospi200.txt`) | yfinance (.KS) + Naver Finance |
| KOSDAQ | XKOS | partial (yfinance .KQ only; Naver pending) | — | yfinance (.KQ) |
| Crypto perp | BINANCE_PERP | research-only (Phase 1D) | BTC/ETH | CCXT |
| FX/Commodity ETFs | NYSE/CBOE | partial | (per-thesis) | yfinance + CFTC |

KR predictions use **E_FUNDAMENTAL_KR** (yfinance .KS PER/ROE/dividend yield)
+ **E_FOREIGN_REVERSAL** (Naver Finance 외인/기관 4-day reversal pattern,
TITAN B4 port) + **E_TIME** (Ichimoku — universe-agnostic). See
[`docs/KR_SUPPORT.md`](docs/KR_SUPPORT.md) for the full guide.

```bash
# v1.1 K1: live KR prediction (no Bigdata MCP, $0 cost)
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
  glostat predict 096770   # SK Innovation
glostat predict 005930   # 삼성전자
```

---

## What we tested (8 thesis → calibration data, not failures)

The numbers below come from the v0.6/v0.7 hindcast runs preserved in
`cache/hindcast/` and reframed here as the v1.0 calibration baseline.

| Thesis | Universe | n | AUC | Sharpe | OOS deg | v1.0 weight* |
|--------|----------|--:|----:|-------:|--------:|-------------:|
| E_PEAD | US 50 | 298 | 0.587 | +0.63 | 116% | 0.18 |
| E_FOREIGN_REVERSAL | KR 20 | 424 | 0.467 | +0.58 | 0% | 0.14 |
| E_INSIDER_CLUSTER | US 19 | 11 | 0.339 | +0.78 | 0% | 0.05 |
| E_COMMODITY_TS | Cmdy 10 | 517 | 0.489 | +0.14 | 100% | 0.06 |
| E_SECTOR_ROTATION | US 11 sectors | 174 | 0.470 | -0.48 | 100% | 0.00 |
| E_FOMC_DRIFT | US 12 | 135 | 0.357 | -1.34 | 100% | 0.00 |
| E_FX_CARRY | US/FX 8 | 135 | 0.400 | -1.53 | 100% | 0.00 |
| E_FUNDING_CARRY | Crypto 2 | 4922 | 0.505 | -0.23 | 457% | 0.02 |

*Brier-derived weight (illustrative — actual values computed at run time).
Full table + interpretation: [`docs/CALIBRATION.md`](docs/CALIBRATION.md).

The v0.6 verdict on the same data: "8 thesis FAIL → automatic shutdown."
The v1.0 verdict: "8 calibrated signals, composite p_up exists with explicit
confidence interval, weak/anti-predictive signals carry near-zero weight."

Both readings are honest. v1.0 is the more useful one.

---

## Architecture overview

```
                ┌─────────────────────────────────────────────────────┐
                │                CLI / library entry                   │
                │   glostat predict <ticker>      glostat calibrate    │
                └─────────────────────────────────────────────────────┘
                                       │
                ┌──────────────────────┼──────────────────────┐
                ▼                      ▼                      ▼
        DataRouter             Compliance Gate         PromptRegistry
        (phase-gated)          (broadcast=ERROR)       (sha256 per call)
                │
   ┌────────────┼────────────┬────────────┐
   ▼            ▼            ▼            ▼
yfinance    SEC EDGAR    CFTC/CCXT    Bigdata MCP   ← Phase 2+, blocked in MVP
   │            │            │            │
   └────────────┴────────────┴────────────┘
                │
                ▼
        Snapshot Broker  ───►  parquet shards + SQLite index + Merkle leaves
                │
                ▼
            Thesis modules  ───►  raw_score, direction, sources
                │
                ▼
   ┌─────────────────────────────────────┐
   │  predictor/composite.py             │
   │   composite_p_up()  +  Brier weights│   ← INV-GS-103
   └─────────────────────────────────────┘
                │
                ▼
            Prediction  ───►  p_up + CI + ThesisContribution[] + disclaimer
                │
                ▼  (calibration loop, quarterly)
       calibration_table.parquet
                │
                ▼
        docs/CALIBRATION.md (auto-regenerated)
```

### Snapshot broker

```python
from datetime import UTC, datetime
from pathlib import Path

from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

broker = SnapshotBroker(root=Path("./snapshots"))
key = SnapshotKey(
    uaid="XNAS.AAPL",
    edge_type="tearsheet",
    ts_utc=datetime(2026, 4, 28, 12, 0, tzinfo=UTC),
    tool="yfinance.fundamentals",
    params_canon='{"period":"quarter"}',
)
record = broker.save_snapshot(key, {"per": 28.4, "roe": 0.21})
print(record.leaf.leaf_hash[:12], broker.audit_root()[:12])
broker.close()
```

### Calibrated prediction (v1.0)

```python
from glostat.predictor.composite import composite_p_up
from glostat.core.types import Prediction

prediction: Prediction = pipeline.predict("AAPL", horizon="5d")
print(f"p_up = {prediction.p_up:.3f}  90%CI=[{prediction.p_up_lower:.3f}, {prediction.p_up_upper:.3f}]")
for c in prediction.contributing:
    print(f"  {c.thesis_name:24} dir={c.direction:4} weight={c.brier_weight:.3f}  AUC={c.auc:.3f}  n={c.n_calibration_samples}")
print(prediction.disclaimer)
```

### Compliance gate (cannot be bypassed)

```python
from glostat.risk.compliance_gate import broadcast_telegram, ComplianceContext

broadcast_telegram(
    ctx=ComplianceContext(user_profile_hash="0" * 64, jurisdiction="US"),
    chat_ids=["@anyone"], message="anything",
)
# → glostat.risk.compliance_gate.ComplianceError: INV-GS-024 …
```

---

## Quickstart

Requires **Python 3.14**.

```bash
# clone + install (uv preferred)
git clone https://github.com/<you>/glostat.git
cd glostat
uv sync --extra dev

# verify
uv run pytest -q                                   # all unit tests
uv run python -c "import glostat; print(glostat.__version__)"   # → 1.0.0

# v1.0 prediction (mock data, no network)
uv run glostat predict AAPL --horizon 5d --mock

# canonical JSON output for downstream tooling
uv run glostat predict AAPL --horizon 5d --mock --json

# quarterly recalibration (re-run all thesis hindcasts → update table)
uv run glostat calibrate --all-thesis --window 365d
uv run glostat calibrate --update-table
```

Live mode requires `GLOSTAT_SEC_USER_AGENT="Your Name your.email@yourdomain.com"`
(SEC EDGAR mandates a contactable User-Agent — `INV-GS-038`).

---

## Reusable for your own thesis

The infrastructure is independent of which thesis you screen. To add a new
thesis to the calibration table:

1. **Write a thesis module.** Subclass the Thesis protocol in
   `src/glostat/experts/`, return a typed `(direction, raw_score, sources)`.
   See [`docs/EXAMPLES.md`](docs/EXAMPLES.md) for a working template.
2. **Register a data source if needed.** Add a routing entry in
   `src/glostat/data/data_router.py`. The DataRouter enforces phase gating so
   paid sources stay blocked until you explicitly opt in.
3. **Run the hindcast.** Configure `Hindcast`, point at a universe, get an
   IS/OOS report with AUC, Sharpe, Brier.
4. **Add a calibration row.** Append the result to
   `cache/calibration_table.parquet` (one row per thesis-universe-horizon
   triple). The Brier-weighted ensemble picks the weight automatically.
5. **PR with calibration data attached.** New thesis PRs must include n ≥ 50,
   AUC, Sharpe, OOS deg and a calibration row. (INV-GS-026 + INV-GS-105.)

Full walkthrough: [`docs/EXAMPLES.md`](docs/EXAMPLES.md).
Migration from v0.7: [`docs/MIGRATION_v0.7_TO_v1.0.md`](docs/MIGRATION_v0.7_TO_v1.0.md).

---

## Repo layout

```
src/glostat/
  core/         # Prediction (v1.0), ThesisContribution (NEW), Verdict (deprecated, kept for back-compat)
  data/         # snapshot broker, prompt registry, free-stack clients, phase-gated DataRouter
  experts/      # 11 thesis modules (PEAD, FOREIGN_REVERSAL, INSIDER_CLUSTER, FX_CARRY, …)
  predictor/    # NEW v1.0 — composite_p_up(), thesis_weight() (Brier sigmoid), calibration I/O
  gating/       # cost gate, regime gate (kept; used during hindcast as calibration mask)
  replay/       # hindcast harness, sprint4_gate (now calibration check), kill criteria
  risk/         # compliance gate (INV-GS-024 + INV-GS-104)

configs/
  invariants.yaml    # 45 numbered invariants (001..105), v0.6 deprecated entries flagged
  budget.yaml        # phase-gated budget caps (mvp $0)
  markets.yaml       # XNAS + XNYS + XKRX
  gating.yaml        # cost / regime / anti-herd parameters (decision-engine vintage; used as calibration mask only in v1.0)
  kill_criteria.yaml # narrowed v1.0 triggers (compliance, broker integrity, stale calibration)
  universes/

cache/
  calibration_table.parquet  # NEW v1.0 — quarterly-updated weights per thesis-universe-horizon
  hindcast/                  # phase1b + phase1c + phase1d historical reports

tests/                       # 506+ pytest tests, INV-GS-001..105 coverage
docs/
  ssot/                      # immutable plan history v0.1 → v0.7 + PLAN_v1.0.md (canonical)
  post_mortem/               # honest Sprint 5 FAIL diagnosis (v0.6)
  research/                  # design notes
  CALIBRATION.md             # NEW — per-thesis empirical predictive strength
  MIGRATION_v0.7_TO_v1.0.md  # NEW — developer migration guide
  EXAMPLES.md                # extending the framework
```

---

## Honest reading order

If you are evaluating whether to adopt or fork this:

1. [`docs/post_mortem/SPRINT5_FAIL_post_mortem.md`](docs/post_mortem/SPRINT5_FAIL_post_mortem.md)
   — start here. The v0.6 framework worked; the alpha didn't. v1.0 turns that
   honest finding into the calibration baseline.
2. [`docs/ssot/PLAN_v1.0.md`](docs/ssot/PLAN_v1.0.md) — canonical v1.0 spec.
   Section 0 explains the reframe rationale; Section 2 explains how the 8 FAIL
   outcomes become calibration data; Section 5 lists new INV-GS-101..105 and
   deprecated INV-GS-001/005/033.
3. [`docs/CALIBRATION.md`](docs/CALIBRATION.md) — empirical predictive
   strength of every thesis currently in the calibration table.
4. [`docs/MIGRATION_v0.7_TO_v1.0.md`](docs/MIGRATION_v0.7_TO_v1.0.md) —
   developer migration guide.
5. `configs/invariants.yaml` — the contract the framework enforces.
6. [`docs/EXAMPLES.md`](docs/EXAMPLES.md) — practical extension recipes.

---

## Compliance disclaimer

GLOSTAT v1.0 is an **information tool** for personal use. Output is a
probability distribution with explicit confidence intervals and source
provenance — **not** an investment recommendation, not a securities solicitation,
not financial advice. Past calibration data does not guarantee future
predictive performance. Users are responsible for their own decisions.

`broadcast_telegram` and `mass_email` raise `ComplianceError` permanently and
unconditionally (INV-GS-024). Every `Prediction` instance carries a non-empty
`disclaimer` field, validated at construction time (INV-GS-104).

---

## Contributing

Issues and pull requests are welcome. Useful directions:

- **New thesis modules** that screen a *different* thesis (event-driven,
  cross-asset momentum, options-implied, factor-based). Must include
  calibration data.
- **New data source clients** (Polygon free tier, Tiingo, Stooq, FRED) routed
  through `DataRouter` with phase gating.
- **Refinements to Brier weighting / sample-size guards** in
  `predictor/composite.py`.
- **Fixes / hardening** of the snapshot broker, hindcast harness, or
  compliance gate.

PR template enforces:
- New thesis → calibration row in `cache/calibration_table.parquet` (INV-GS-026, INV-GS-105)
- No new INV-GS-101 violation (no BUY/SELL output)
- No new INV-GS-024/104 weakening (no broadcast, no missing disclaimer)

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for coding style, INV-GS conventions,
and templates.

---

## License

MIT — see [`LICENSE`](LICENSE). Use it commercially, fork it, embed it, port
it, just keep the copyright notice.

---

## Citing

If this framework helps your research or post-mortem write-up, a link back to
this repository is appreciated. Cite the calibration table version
(`v1.x.0`, quarter-bumped) so reproducibility is preserved.
