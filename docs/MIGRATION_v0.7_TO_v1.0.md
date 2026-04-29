# Migration guide — v0.7 → v1.0

> Audience: developers / researchers who used v0.6 / v0.7 of GLOSTAT and want
> to adapt to the v1.0 prediction-tool framing without re-implementing
> infrastructure.
>
> TL;DR: **Verdict (BUY/HOLD/SELL action) → Prediction (probability + CI +
> evidence chain).** All infrastructure (Snapshot Broker, Hindcast,
> compliance gate, free-stack clients, prompt registry) is unchanged. Only
> the output dataclass + composite logic + CLI rendering changes.

---

## What changed (semantic)

| Area | v0.6 / v0.7 | v1.0 |
|------|-------------|------|
| Mental model | Decision engine that emits a recommendation | Prediction tool that emits a probability |
| Output dataclass | `Verdict(action, conviction_w, target_price, stop_price, …)` | `Prediction(p_up, p_up_lower, p_up_upper, contributing, …)` |
| Composite logic | edge_bps cost gate + anti-herd discount | Brier-score sigmoid weighting |
| Sprint 4 gate | PASS/AMBIGUOUS/FAIL → archive on FAIL | Used as a calibration check, never shuts down the project |
| 8-thesis FAIL data | "Project failed" | "Calibration table inputs" |
| CLI primary command | `glostat predict <ticker>` → action+target+stop | `glostat predict <ticker>` → probability + CI + contributions |
| Quarterly cadence | none | `glostat calibrate --all-thesis` every quarter (INV-GS-105) |

## What stayed (infrastructure)

- `glostat.data.snapshot_broker.SnapshotBroker` — unchanged
- `glostat.data.prompt_versioning.PromptRegistry` — unchanged
- `glostat.data.data_router.DataRouter` — unchanged
- `glostat.data.yfinance_client / sec_edgar_client / cftc_client / ccxt_client / naver_kr_client` — unchanged
- `glostat.replay.validation_harness.Hindcast / HindcastSplit / PassCriteria` — unchanged
- `glostat.replay.sprint4_gate` — kept; output is now consumed as calibration data, not as a shutdown trigger
- `glostat.risk.compliance_gate` — unchanged (`broadcast_telegram` / `mass_email` still raise `ComplianceError`)
- All 11 thesis modules in `src/glostat/experts/` — unchanged at signal-generation level
- 506+ tests — pass under v1.0 reframe (semantics changed, code paths preserved)

## What is deprecated but kept for back-compat

- `glostat.core.types.Verdict` (dataclass) and `verdict_to_canonical_json` —
  deprecated but importable. Existing scripts that emit Verdict still work;
  they will warn on import in v1.1+ and may be removed in v2.0.
- `glostat.cli_predict_print.print_verdict` — kept; new code should use
  `print_prediction`.

---

## Verdict → Prediction field mapping

| `Verdict` (v0.6) | `Prediction` (v1.0) | Notes |
|------------------|---------------------|-------|
| `action: BUY/HOLD/SELL` | (removed) | INV-GS-101 forbids action output |
| `conviction_w: float [0, 3.5]` | `p_up: float [0, 1]` + `p_up_lower / p_up_upper` | probability replaces conviction |
| `target_price: float \| None` | (removed) | derivative of action |
| `stop_price: float \| None` | (removed) | derivative of action |
| `suggested_size_pct: float` | (removed) | derivative of action |
| `horizon_days: int` | `horizon: Literal["1d", "5d", "30d"]` | discrete |
| `edge_bps: float` | (removed) | cost gate not applicable |
| `all_in_bps: float` | (removed) | cost gate not applicable |
| `cost_passed: bool` | (removed) | cost gate not applicable |
| `expected_pnl_bps: float` | (removed) | derivative of action |
| `disagreement_weight: float` | (implicit in CI width) | CI width encodes ensemble disagreement |
| `contributing_signals: tuple[ExpertSignal, ...]` | `contributing: tuple[ThesisContribution, ...]` | Richer metadata: AUC, Brier, n, calibration window |
| `next_trigger: str` | (removed) | derivative of action |
| `evidence_hash: str` | `evidence_hash: str` | identical |
| `prompt_versions: tuple[(str, str), ...]` | `prompt_versions: tuple[(str, str), ...]` | identical |
| `git_commit: str` | `git_commit: str` | identical |
| `user_profile_hash: str` | (removed) | personal use only — hash unnecessary |
| `issued_at: datetime` | `issued_at: datetime` | identical |
| `market: Literal["XNAS", "XNYS"]` | `market: str` (any MIC) | global, not US-only |
| (n/a) | `disclaimer: str` | NEW — INV-GS-104 enforced at construction |
| (n/a) | `composite_brier: float` | NEW — ensemble Brier estimate |
| (n/a) | `snapshot_root: str` | NEW — broker.audit_root() at issue time |

---

## CLI changes

### Before (v0.6 / v0.7)

```bash
$ uv run glostat predict AAPL
[BUY] AAPL  conviction_w=2.4  target=$210  stop=$185
edge_bps=18.4  all_in_bps=0.84  cost_passed=true
contributing: E_FUNDAMENTAL +1.2, E_TIME +0.8, E_FUND_FLOW NEUTRAL
horizon: 5d  next_trigger: "..."
```

### After (v1.0)

```bash
$ uv run glostat predict AAPL --horizon 5d
GLOSTAT v1.0 — Probability Predictor
Information tool. Not investment advice.

AAPL  XNAS  horizon=5d  issued=2026-04-29T13:30:00Z
p_up = 0.547  90%CI = [0.491, 0.603]  composite_brier = 0.247

Contributing:
  E_PEAD               UP    score=+0.42  weight=0.18  AUC=0.587  n=298
  E_INSIDER_CLUSTER    UP    score=+0.18  weight=0.05  AUC=0.339  n=11

evidence_hash: 9f8e...c2a1
git_commit:    2b96ca5
snapshot_root: fedcba...0987

Past calibration data ≠ future performance.
```

### New CLI command — `glostat calibrate`

```bash
# Run quarterly recalibration (re-run all thesis hindcasts)
uv run glostat calibrate --all-thesis --window 365d

# Update calibration_table.parquet in place
uv run glostat calibrate --update-table

# Regenerate docs/CALIBRATION.md from current parquet
uv run glostat calibrate --regenerate-docs

# Inspect a single thesis's calibration row
uv run glostat calibrate --inspect E_PEAD
```

---

## INV-GS changes

### New (v1.0)

- INV-GS-101 — Output is probability + CI. BUY/SELL action output forbidden.
- INV-GS-102 — Every prediction must cite source signals + calibration window + n_samples.
- INV-GS-103 — Composite p_up uses Brier-score-weighted ensemble.
- INV-GS-104 — Per-prediction disclaimer required (extends INV-GS-024).
- INV-GS-105 — Quarterly recalibration policy.

### Deprecated (v0.6 → v1.0)

- INV-GS-001 — `edge_bps ≥ 1.5 × all_in_bps` cost gate. Decision-engine artifact; v1.0 emits no action so cost gate has no semantic role. Kept as `gating.py::cost_mask()` for use during hindcast PnL realization, but does **not** demote any output.
- INV-GS-005 — 4+ experts agreeing → 0.80× anti-herd discount. Brier-weighted ensemble inherently captures dissent via CI width; no manual discount needed.
- INV-GS-033 — Sprint 4 gate FAIL → automatic shutdown. The gate metric still computes, but per-thesis FAIL now downgrades that thesis's weight (potentially to 0). The project is not bound to per-thesis Sharpe.

### Reinforced

- INV-GS-024 (broadcast forbidden) — preserved; INV-GS-104 layers disclaimer requirement on top so personal-use status is visible per output, not just per channel.

### Continued

- INV-GS-002, INV-GS-006, INV-GS-010, INV-GS-022, INV-GS-023, INV-GS-026,
  INV-GS-035, INV-GS-036..040 — all unchanged.

---

## Code migration examples

### 1. Emitting a Prediction instead of a Verdict

```python
# v0.6 / v0.7
from glostat.core.types import Verdict
from glostat.verdict_builder import build_verdict
verdict = build_verdict(
    ticker="AAPL", market="XNAS", horizon_days=5,
    contributing=[expert_fund, expert_time, expert_flow],
    cost_gate=cost_gate, prompts=prompts, broker=broker,
)
print(verdict.action, verdict.conviction_w)
```

```python
# v1.0
from glostat.core.types import Prediction, ThesisContribution
from glostat.predictor.composite import composite_p_up, build_prediction
prediction = build_prediction(
    ticker="AAPL", market="XNAS", horizon="5d",
    contributing=[contrib_pead, contrib_insider],   # ThesisContribution objects
    calibration_table=calibration_table,
    snapshot_broker=broker, prompts=prompts,
)
print(prediction.p_up, prediction.p_up_lower, prediction.p_up_upper)
print(prediction.disclaimer)   # always non-empty (INV-GS-104)
```

### 2. Running calibration (replaces Sprint 4 gate decision logic)

```python
# v0.6 / v0.7
from glostat.replay.sprint4_gate import Sprint4Gate
gate = Sprint4Gate(profile="cautious")
status = gate.evaluate(report)   # "PASS" | "AMBIGUOUS" | "FAIL"
if status == "FAIL":
    raise SystemExit("INV-GS-033: shutdown")
```

```python
# v1.0
from glostat.predictor.calibration import (
    compute_brier, derive_weight, append_calibration_row,
)
brier = compute_brier(report.predictions, report.outcomes)
weight = derive_weight(brier=brier, n_samples=report.n)
append_calibration_row(
    table_path="cache/calibration_table.parquet",
    thesis="E_NEW_THESIS", universe="us_sp100_50", horizon="5d",
    auc=report.auc, sharpe=report.sharpe, oos_deg=report.oos_deg,
    brier=brier, weight=weight, n=report.n,
)
# No shutdown — weak thesis just gets weight ↓ in the next prediction
```

### 3. Reading calibration weights at predict time

```python
# v1.0
import pandas as pd
calib = pd.read_parquet("cache/calibration_table.parquet")
weight_pead = calib.loc[calib.thesis_name == "E_PEAD", "weight"].iloc[0]
# pass into ThesisContribution at prediction time — see build_prediction()
```

### 4. CLI compliance check (unchanged, but worth re-verifying)

```python
from glostat.risk.compliance_gate import broadcast_telegram, ComplianceContext
broadcast_telegram(
    ctx=ComplianceContext(user_profile_hash="0"*64, jurisdiction="US"),
    chat_ids=["@anyone"], message="anything",
)
# → ComplianceError — same as v0.6
```

---

## Test-suite migration

- `tests/test_invariants.py` — INV-GS-001 / 005 / 033 tests are kept but
  marked `pytest.mark.deprecated_invariant`; they verify the legacy code path
  still behaves correctly (no action output ever produced via Verdict path).
- `tests/test_invariants_v06.py` — unchanged.
- `tests/test_invariants_v10.py` — NEW. Add tests for INV-GS-101..105:
  - `test_inv_gs_101_no_action_field` — verify `Prediction` has no `action`
    attribute.
  - `test_inv_gs_102_contributing_required` — `Prediction(..., contributing=())` raises.
  - `test_inv_gs_103_brier_sigmoid` — verify weight = 0.5 at Brier 0.25, > 0.5
    at Brier < 0.25.
  - `test_inv_gs_104_disclaimer_required` — `Prediction(..., disclaimer="")` raises.
  - `test_inv_gs_105_table_freshness` — warn if calibration_table > 2 quarters old.
- `tests/test_calibration.py` — NEW. Brier formula, weighting curve, composite
  ensemble math.

---

## Migration checklist for downstream code

- [ ] Replace `from glostat.core.types import Verdict` with `Prediction`
- [ ] Replace `verdict.action` checks with probability thresholds (e.g. `p > 0.55`)
- [ ] Replace `verdict.target_price` / `stop_price` reads with **client-side** logic
      (the framework no longer recommends targets — that is a user decision)
- [ ] Add `prediction.disclaimer` to any UI that surfaces predictions
- [ ] Re-run `pytest -q` — all 506+ tests should pass
- [ ] Add `glostat calibrate` to your scheduled jobs (quarterly cadence)
- [ ] Verify `cache/calibration_table.parquet` is gitignored (it is, by default)

---

## FAQ

**Q: Can I still output BUY/SELL on top of v1.0 internally for my own use?**
A: That is your prerogative, but not via the framework's API. Build a thin
adapter in your own code that maps `prediction.p_up >= 0.6 → "BUY"`, etc.
The framework deliberately does not provide that mapping — INV-GS-101.

**Q: My script depends on `verdict.cost_passed` being True. What now?**
A: Use `gating.cost_mask(prediction, all_in_bps)` to compute cost-feasibility
on the client side. The mask returns a boolean; emit only the predictions
that pass.

**Q: I had a Sprint 4 gate PASS for a thesis. Does v1.0 still recognize it?**
A: Yes — that thesis becomes a calibration row with the AUC/Sharpe/OOS values
that gave it PASS. The Brier weight reflects the same data; PASS-quality
thesis will land in the 0.3–0.6 weight range typically.

**Q: Will v1.0 ever re-introduce action output?**
A: No. INV-GS-101 is permanent. Any PR introducing `action`, `target_price`,
`stop_price`, `suggested_size_pct`, or `directive` on `Prediction` is
auto-rejected.

**Q: Does the post-mortem still apply?**
A: Yes — and you should still read it first. The post-mortem is honest about
what the v0.6 framework attempted and why that attempt failed against its
own goals. v1.0 changes the goal (predict, not decide), so the post-mortem's
"alpha absent" finding becomes a calibration input rather than a project-end
verdict. The infrastructure conclusions (Snapshot Broker, kill criteria,
compliance gate are sound) all carry forward.

---

## Where to go next

- [`docs/ssot/PLAN_v1.0.md`](ssot/PLAN_v1.0.md) — full canonical v1.0 spec
- [`docs/CALIBRATION.md`](CALIBRATION.md) — current calibration table
- [`docs/EXAMPLES.md`](EXAMPLES.md) — extending the framework
- [`README.md`](../README.md) — the public-facing entry
