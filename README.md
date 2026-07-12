# GLOSTAT — Evidence-based Probability Predictor for Global Equities

> **Calibrated multi-horizon predictions with evidence chains.**
>
> Information tool. Not investment advice. Past calibration ≠ future performance.

---

## Status

> **v2.0.1 ACTIVE (2026-05-23) — IP boundary cleanup + security hotfix.**
> v2.0.0 removed the sizing-tier attachment (INV-GS-111 deprecated); v2.0.1
> hardened API-key handling in DART/ECOS/KIS clients and added IP regression
> guards. All v1.x releases (1.3.0..1.9.1, 12 versions) are yanked from PyPI;
> `pip install glostat` now resolves to v2.0.1+.
>
> v0.6/v0.7 framed GLOSTAT as a **decision engine** (BUY/SELL action output)
> and concluded "8 thesis FAIL" against a `Sharpe ≥ 0.8 / AUC ≥ 0.62 / OOS deg
> ≤ 30%` gate. v1.0 reframed the same data as a **prediction tool**: probability
> distribution + 1-sigma (~68%) CI per INV-GS-113 + per-thesis Brier-weighted
> contribution + evidence chain. The 8-thesis FAIL outcomes are now the **first
> input rows** of the calibration table — `E_PEAD AUC 0.587`,
> `E_FOREIGN_REVERSAL OOS Sharpe 1.46`, `E_FOMC_DRIFT AUC 0.357`
> (anti-predictor) — all carry honest, sample-aware weights.
>
> **v2.0 migration**: `Prediction.dca_sizing` field is REMOVED. The predictor
> surface is strictly probability + CI. Callers that read `.dca_sizing` must
> remove that access. See [v2.0 release notes](https://github.com/deximple/glostat/releases/tag/v2.0.0).
>
> **Read first**:
> [`docs/post_mortem/SPRINT5_FAIL_post_mortem.md`](docs/post_mortem/SPRINT5_FAIL_post_mortem.md)
> (the v0.6 honest diagnosis), then
> [`docs/ssot/PLAN_v1.0.md`](docs/ssot/PLAN_v1.0.md) (the active spec), then
> [`docs/CALIBRATION.md`](docs/CALIBRATION.md) (the empirical predictive
> strength table), then [`docs/ROADMAP_v2.md`](docs/ROADMAP_v2.md) (v2.x.x
> integrated roadmap).

---

## What this project IS

- A **calibrated probability predictor** — outputs
  `Prediction(up_probability, confidence_interval_bps, contributing_signals,
  ...)` with Brier-derived ensemble weights per thesis.
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
- **71+ numbered invariants** (`INV-GS-001..114`) with a 1:1 unit-test mapping
  and a machine-readable `configs/invariants.yaml`. Active invariants include
  `INV-GS-101` (no BUY/SELL output), `INV-GS-104` (per-prediction disclaimer),
  `INV-GS-113` (CI 1-sigma + p-value + all-noise statistical disclosures),
  `INV-GS-114` (KR megacap honesty footer).

## What this project IS NOT

- **Not investment advice.** Use at your own risk. Read the post-mortem first.
- **Not a trading bot.** No BUY/SELL action output (INV-GS-101). No
  target/stop/size output. The `Prediction` dataclass deliberately omits any
  field that prescribes action.
- **Not an alpha-generating decision engine.** v0.6 attempted that, failed
  honestly across 8 thesis. The framework's value is the **honest measurement
  + Brier weighting**, not a guaranteed alpha.
- **Not a black-box predictor.** Every `Prediction` carries a
  `SignalContribution` chain with calibration window, n_samples,
  calibration_auc, confidence_v2, and source snapshot IDs.
- **Not a broadcast tool.** `broadcast_telegram` and `mass_email` are inert
  sentinels that always raise (INV-GS-024).
- **Not a multi-user product.** Personal use only.

---

## Supported markets

| Market | MIC | Status | Universe | Data sources |
|--------|-----|--------|----------|--------------|
| US large-cap | XNAS, XNYS | active | S&P 500 Top 50 (`sp500_top50.txt`) | yfinance + SEC EDGAR + FRED-ready |
| US small-mid | XNAS, XNYS | active | Russell 2000 Top 200 proxy | yfinance + SEC EDGAR |
| KR KOSPI | XKRX | active | KOSPI 200 (`kospi200.txt`) | yfinance (.KS) + Naver Finance + DART + ECOS + KIS + KRX |
| KR KOSDAQ | XKOS | active | KOSDAQ 150 Top 30 | yfinance (.KQ) + Naver Finance + DART |
| Crypto perp | BINANCE_PERP | research | BTC/ETH | CCXT |
| FX / Commodity ETFs | NYSE/CBOE | partial | per-thesis | yfinance + CFTC COT |

Active data clients (13): `yfinance`, `sec_edgar`, `cftc`, `ccxt`,
`naver_kr`, `dart` (KR insider/disclosure), `ecos` (BoK macro), `kis`
(KIS Open API read-only), `krx_short` (KRX short statistics),
`toss` (Toss local-parquet cache), `commodity`, `kr_calendar`,
`bigdata` (phase-gated, MVP-blocked per INV-GS-036).

KR predictions use up to 11 active KR-specific signals: `E_FUNDAMENTAL_KR`
(yfinance PER/ROE/dividend + DART overlay), `E_FUNDAMENTAL_KR_CYCLICAL`,
`E_FOREIGN_REVERSAL` (3-source Naver+KIS+Toss flow fusion),
`E_INSIDER_KR` (DART elestock), `E_MACRO_KR` (BoK ECOS), `E_PEAD_KR`,
`E_INSIDER_VELOCITY_KR`, `E_SHORT_SELLING_KR`, `E_INTRADAY_FLOW_KR`,
`E_COMMODITY_INDEX_KR`, `E_TIME` (Ichimoku, universe-agnostic). See
[`docs/KR_SUPPORT.md`](docs/KR_SUPPORT.md) for the full guide.

```bash
# Live KR prediction (no Bigdata MCP, $0 cost, no SEC_USER_AGENT needed for KR)
glostat predict 005930 --horizon swing_5d   # 삼성전자
glostat predict 096770 --horizon swing_5d   # SK Innovation

# Live US prediction (SEC EDGAR requires User-Agent per INV-GS-038)
GLOSTAT_SEC_USER_AGENT="Your Name your.email@yourdomain.com" \
  glostat predict AAPL --horizon swing_5d
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
The v1.0 verdict: "8 calibrated signals, composite up_probability exists with
explicit confidence interval, weak/anti-predictive signals carry near-zero
weight."

Both readings are honest. v1.0+ is the more useful one.

**The above table is the v0.6 baseline only.** Current main has 21 thesis
modules across US + KR + crypto, including the v1.x additions
`E_FUNDAMENTAL_KR`, `E_PEAD_KR`, `E_INSIDER_VELOCITY_KR`,
`E_ANALYST_REVISION`, `E_SHORT_SELLING_KR`, `E_INTRADAY_FLOW_KR`,
`E_FUNDAMENTAL_KR_CYCLICAL`, `E_COMMODITY_INDEX_KR`, `E_MACRO_KR`,
`E_INSIDER_KR`. See [`docs/CALIBRATION.md`](docs/CALIBRATION.md) for the
full per-thesis table and [`docs/KR_SUPPORT.md`](docs/KR_SUPPORT.md) for
KR-specific signal documentation.

### KR megacap honesty footer (INV-GS-114)

Phase KR M1 hindcast on KOSPI 200 (n = 3,510 samples) measured AUC ≤ 0.51 —
**at the edge of statistical noise**. The CLI surfaces this footer on every
KR megacap prediction (`*** KR megacap universe — AUC ≤ 0.51, predictions
are weak signals`). This is the same honest data that drives the v1.x
`E_FOREIGN_REVERSAL` Brier weight collapse on that universe; the framework
shows it rather than hides it.

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
   │   predict()  +  Brier weights       │   ← INV-GS-103
   └─────────────────────────────────────┘
                │
                ▼
            Prediction  ───►  up_probability + 1-sigma CI + SignalContribution[] + disclaimer
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

### Calibrated prediction (v2.0)

```python
from glostat.predictor import predict, load_calibration
from glostat.predictor.types import Prediction

cal_table = load_calibration()
prediction: Prediction = predict(
    ticker="AAPL",
    horizon="swing_5d",                     # intraday | swing_5d | swing_30d | long_3y
    contributions=(...),                    # build via collect_contributions(); see docs/EXAMPLES.md
    cal_table=cal_table,
)

print(f"p_up = {prediction.up_probability:.3f}")
low_bps, high_bps = prediction.confidence_interval_bps
print(f"  CI 1-sigma (~68%) bps = [{low_bps:+.1f}, {high_bps:+.1f}]")
if low_bps <= 0 <= high_bps:
    print("  *** includes 0 — no clear direction (INV-GS-113 X2)")

for c in prediction.contributing_signals:
    if c.direction == "skip":
        continue
    print(f"  {c.name:24} dir={c.direction:4}  "
          f"AUC={c.calibration_auc:.3f}  n={c.n_samples}")

print(prediction.disclaimer)                 # always non-empty (INV-GS-104)
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

Requires **Python ≥ 3.11**.

```bash
# install (PyPI default = v2.0.1+, since v1.x are yanked)
pip install glostat

# or from source (uv preferred)
git clone https://github.com/deximple/glostat.git
cd glostat
uv sync --extra dev

# verify
uv run pytest -q                                   # 836+ test functions
uv run python -c "import glostat; print(glostat.__version__)"   # → 2.0.1

# Mock prediction (no network, fixture data)
uv run glostat predict AAPL --horizon swing_5d --mock

# JSON output (machine-readable)
uv run glostat predict AAPL --horizon swing_5d --mock --json

# Refresh calibration_table.parquet from cached hindcast reports
uv run glostat calibrate --out cache/calibration_table.parquet

# Run KR hindcast (KOSPI 200 universe, produces calibration JSON)
uv run glostat kr-hindcast --universe kospi200 --start 2024-01-01 --end 2026-04-30
```

US live mode requires `GLOSTAT_SEC_USER_AGENT="Your Name your.email@yourdomain.com"`
(SEC EDGAR mandates a contactable User-Agent — `INV-GS-038`). KR live mode
does NOT need it (Naver / DART / ECOS / KIS use their own keys; bare
`glostat predict 005930 --horizon swing_5d` works).

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
  core/         # Verdict (deprecated, kept for back-compat), shared types/errors
  data/         # 13 data clients (snapshot broker, free-stack clients, phase-gated DataRouter)
  experts/      # 21 thesis modules (PEAD, FOREIGN_REVERSAL, INSIDER_CLUSTER, FX_CARRY,
                #   E_FUNDAMENTAL_KR, E_PEAD_KR, E_INSIDER_VELOCITY_KR, E_ANALYST_REVISION, …)
  predictor/    # composite.predict(), confidence_v2 (5-component geometric mean), calibration I/O
                #   Prediction lives here (predictor/types.py), NOT in core/
  gating/       # cost gate, regime gate (kept; used during hindcast as calibration mask)
  replay/       # hindcast harness, sprint4_gate (now calibration check), kill criteria
  risk/         # compliance gate (INV-GS-024 + INV-GS-104)

configs/
  invariants.yaml    # 71 numbered invariants (001..114), v0.6 + INV-GS-111 deprecated entries flagged
  budget.yaml        # phase-gated budget caps (mvp $0)
  markets.yaml       # XNAS + XNYS + XKRX + XKOS
  gating.yaml        # cost / regime / anti-herd parameters (decision-engine vintage; used as calibration mask only in v1.0+)
  kill_criteria.yaml # narrowed v1.0 triggers (compliance, broker integrity, stale calibration)
  universes/

cache/
  calibration_table.parquet  # quarterly-updated weights per thesis-universe-horizon
  hindcast/                  # phase1b + phase1c + phase1d + phase_kr historical reports

tests/                       # 836+ test functions across 79 files; INV-GS-001..114 coverage
docs/
  ssot/                      # plan history v0.1 → v0.7 + PLAN_v1.0.md (active spec)
  post_mortem/               # honest Sprint 5 FAIL diagnosis (v0.6)
  research/                  # design notes
  ROADMAP_v2.md              # v2.x.x integrated roadmap (10-agent synthesis)
  v2.1_PRD.md                # v2.1 product requirements
  CALIBRATION.md             # per-thesis empirical predictive strength
  CONFIDENCE_V2.md           # 5-component confidence model (INV-GS-112)
  MIGRATION_v0.7_TO_v1.0.md  # developer migration guide
  EXAMPLES.md                # extending the framework
```

---

## Honest reading order

If you are evaluating whether to adopt or fork this:

1. [`docs/post_mortem/SPRINT5_FAIL_post_mortem.md`](docs/post_mortem/SPRINT5_FAIL_post_mortem.md)
   — start here. The v0.6 framework worked; the alpha didn't. v1.0 turns that
   honest finding into the calibration baseline.
2. [`docs/ssot/PLAN_v1.0.md`](docs/ssot/PLAN_v1.0.md) — active framework spec.
   Section 1 explains the reframe rationale; Section 2 explains how the 8 FAIL
   outcomes become calibration data; later sections list invariants
   INV-GS-101..114 and deprecated INV-GS-001/005/033/111.
3. [`docs/CALIBRATION.md`](docs/CALIBRATION.md) — empirical predictive
   strength of every thesis currently in the calibration table.
4. [`docs/MIGRATION_v0.7_TO_v1.0.md`](docs/MIGRATION_v0.7_TO_v1.0.md) —
   developer migration guide.
5. `configs/invariants.yaml` — the contract the framework enforces.
6. [`docs/EXAMPLES.md`](docs/EXAMPLES.md) — practical extension recipes.

---

## Third-party data sources

Usage of the Bigdata.com integration requires a valid API key and is subject to
[Bigdata.com's Terms of Service](https://bigdata.com/terms). Users must obtain
their own credentials independently. The Bigdata MCP client is gated behind
`GLOSTAT_PHASE=phase_2` (INV-GS-036) and is **not active in the default MVP
configuration**.

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
(`v2.x.x`, quarter-bumped) so reproducibility is preserved.
