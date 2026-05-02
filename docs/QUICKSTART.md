# GLOSTAT — 10-minute Quickstart

> Status: v1.6.2 (2026-05-02). Information tool. Not investment advice.
> Past calibration ≠ future performance.

---

## 1. What is GLOSTAT?

GLOSTAT is an open-source, evidence-based **probability predictor** for global
equities (US + KR + FX + commodities + crypto). Each call produces a
`Prediction(p_up, p_up_lower, p_up_upper, contributing[], disclaimer, ...)`
with Brier-weighted ensemble contributions and a per-thesis evidence chain —
**not** a BUY / SELL action, target, or stop. Personal-use information tool;
broadcast and mass-email entry points are permanently disabled (INV-GS-024 +
INV-GS-104).

---

## 2. Install (1 minute)

```bash
pip install glostat==1.6.2
```

Or from source (preferred for development):

```bash
git clone https://github.com/<you>/glostat.git
cd glostat
uv sync --extra dev
uv run python -c "import glostat; print(glostat.__version__)"   # → 1.6.2
```

Requires Python 3.11+.

---

## 3. First prediction — US ticker (30 seconds)

```bash
export GLOSTAT_SEC_USER_AGENT="YourName your@email.com"
glostat predict AAPL
```

Expected shape:

```
=== GLOSTAT Prediction — AAPL (XNAS) ===
  up / down / sideways: 53.2% / 26.4% / 20.4%
  expected return: gross +24bps  net +22bps   (CI 1-sigma (68%): -41bps .. +89bps)
  edge over baseline: +1.2pp
Contributing signals (active 4 / total 12):
  E_PEAD                 ^   +1.21  (AUC 0.587, n=298, p<0.001, conf_v2=0.78)
  E_INSIDER_CLUSTER      ^   +0.55  (AUC 0.339, n=11, p=0.31, n.s., conf_v2=0.12)
  E_TIME                 -   +0.00  (AUC 0.520, n=200, p=0.46, n.s.)
  E_FUNDAMENTAL          v   -0.42  (AUC 0.515, n=212, p=0.39, n.s.)
*** Statistical note: every active signal's AUC is statistically
    indistinguishable from random except E_PEAD.
Disclaimer: Personal use only. Not investment advice.
```

SEC EDGAR mandates a contactable User-Agent (INV-GS-038) — bare hostname or
`example.com` is rejected.

---

## 4. First KR prediction (1 minute)

```bash
glostat predict 005930   # 삼성전자
glostat predict 096770   # SK이노베이션
glostat predict 000720   # 현대건설
```

KR tickers normalize to bare 6-digit form (INV-GS-106); yfinance fetch
auto-appends `.KS`. UAID becomes `XKRX.005930` (segregated from US).

KR megacap (XKRX/XKOS) predictions append a universe-honesty footer:

```
*** Phase KR M1: AUC <= 0.51 on n=3,510 KOSPI 200 samples —
    discrimination is at the edge of statistical noise.
```

This is intentional. Most KR megacap signals are near-random — see
`docs/EMPIRICAL_RESULTS_2026-05-02.md` for the full measured table.

---

## 5. Optional API keys (3 minutes)

All optional. Each absence triggers a clean skip — no errors, no silent
failures. Set what you need:

| Env var | Source | Enables |
|---|---|---|
| `GLOSTAT_DART_API_KEY` | https://opendart.fss.or.kr/ | E_INSIDER_KR (Form-4 equivalent), DART overlay on E_FUNDAMENTAL_KR |
| `GLOSTAT_ECOS_API_KEY` | https://ecos.bok.or.kr/ | E_MACRO_KR (BoK rate, KRW/USD, CPI, KOSPI momentum) |
| `GLOSTAT_KIS_APP_KEY` + `GLOSTAT_KIS_APP_SECRET` | KIS Open API portal | 3-source flow fusion (KIS real-time + Toss + Naver) |

Setup walkthroughs: `docs/DART_API_SETUP.md`, `docs/ECOS_API_SETUP.md`,
`docs/KIS_API_SETUP.md`. All free tiers.

---

## 6. Run KR hindcast for real calibration (10–20 minutes)

The first KR predict you run uses **bootstrap weights (n=0)** — most KR
signals contribute zero until you measure them on your machine:

```bash
glostat kr-hindcast --start 2025-09-01 --end 2026-01-31 \
    --max-concurrent 3 --stride 7
```

Reports land in `cache/hindcast/phase_kr/*.json`. Subsequent `glostat predict`
calls load the measured AUC + n + Sharpe per thesis and weight them via
Brier sigmoid. Expect ~5–25 minutes depending on network.

The wave-1+wave-2 measurement on 2026-05-02 found exactly 1 of 6 new KR
theses with statistically significant edge: E_PEAD_KR (AUC 0.5405, p=0.008,
n=360). The others receive near-zero ensemble weight. See
`docs/EMPIRICAL_RESULTS_2026-05-02.md`.

---

## 7. Output anatomy

| Section | Meaning |
|---|---|
| `up / down / sideways` | `p_up` / `p_down` / `p_sideways` — full mass distribution |
| `expected return: gross +Xbps  net +Ybps` | gross = signal-driven return; net = after `round_trip_bps(market)` (KR ~23 bps, US ~1.4 bps) — INV-GS-113 X4 |
| `CI 1-sigma (68%)` | **NOT 95%.** 1-sigma symmetric interval. Append `*** includes 0` when interval crosses zero (INV-GS-113 X1, X2) |
| Contributing signals table | per-thesis: direction, raw_score, measured AUC, n, p-value, conf_v2 — INV-GS-103 + INV-GS-113 X3 |
| `no data (n=0, weight=0)` | n=0 thesis displays explicit zero-data line instead of silent `+0.00` (INV-GS-113 X5) |
| Sizing tier | Optional `W값` field — INFORMATION ONLY (INV-GS-111). NOT a position-size recommendation |
| `Next triggers` | Concrete D-day countdowns from `kr_calendar_client` for KR tickers (BoK 금통위, OPEC, earnings) — INV-GS-120 |
| Statistical disclaimer | Surfaces when every active signal has p > 0.05 — INV-GS-113 X6 |
| Universe note | KR megacap honesty footer — INV-GS-114 |
| `Disclaimer` | Per-prediction personal-use disclaimer — INV-GS-104 (always present) |

CI also widens near scheduled events: D-day < 7 → ×1.5σ, D-day < 3 → ×2.0σ
(INV-GS-121).

---

## 8. Troubleshooting

**`ConfigError: SEC EDGAR User-Agent missing or example.com`**
Set a real contactable address (INV-GS-038):
```bash
export GLOSTAT_SEC_USER_AGENT="YourName your@email.com"
```

**`*** Statistical note: every active signal indistinguishable from random`**
Expected for KR megacap before hindcast. Run:
```bash
glostat kr-hindcast --start 2025-09-01 --end 2026-01-31
```

**`sqlite3.OperationalError: database is locked`**
Snapshot Broker contention with concurrent hindcast workers. Fixed in v1.6.3+
(busy_timeout=30s). On v1.6.2 reduce `--max-concurrent` to 1–2.

**`ticker not in KOSPI 200`**
Universe gate. Inspect `configs/universes/kospi200.txt` (200 tickers, pinned
quarterly). KOSDAQ tickers fall through to yfinance-only path.

**`ECOS / DART / KIS skipped`**
API key not set — this is normal. Set the corresponding env var to enable.

---

## 9. What GLOSTAT does NOT do

Strict by design — every line below is a permanent invariant, not a
not-yet-implemented feature:

- **No BUY / SELL output** (INV-GS-101)
- **No target / stop prices** (derivative of prohibited action)
- **No portfolio sizing recommendation** (`dca_sizing` is INFORMATION ONLY,
  INV-GS-111)
- **No broadcast / mass-email** — `broadcast_telegram` and `mass_email`
  raise `ComplianceError` unconditionally (INV-GS-024)
- **No multi-user deployment** — personal use only

Attempts to bypass any of these are PR-rejected automatically.

---

## 10. Where to go next

| Doc | Why read it |
|---|---|
| `docs/EMPIRICAL_RESULTS_2026-05-02.md` | Honest measured AUCs. **Read this before trusting any signal.** |
| `docs/CALIBRATION.md` | Full per-thesis calibration table (AUC, Sharpe, OOS, weight) |
| `docs/KR_SUPPORT.md` | KR (KOSPI/KOSDAQ) operational guide — universe, signals, Naver/DART/ECOS/KIS wiring |
| `docs/V15_SECTOR.md` | v1.5 sector-aware cyclicals (commodity_client, sector_classifier_kr, E_FUNDAMENTAL_KR_CYCLICAL, E_COMMODITY_INDEX_KR) |
| `docs/V16_CALENDAR.md` | v1.6 calendar awareness (kr_calendar_client, E_PEAD_KR, CI widening, next_triggers) |
| `docs/DCA_SIZING.md` | TITAN-derived W값 sizing tier (INFORMATION ONLY framing) |
| `docs/CONFIDENCE_V2.md` | 5-component confidence_v2 (sample_quality + effective_size + score_stability + return_consistency + recency_quality) |
| `docs/ssot/PLAN_v1.0.md` | Canonical v1.0 spec — the contract the framework enforces |
| `docs/post_mortem/SPRINT5_FAIL_post_mortem.md` | The honest v0.6 → v1.0 reframe story. Start here if evaluating adoption. |
| `docs/MIGRATION_v0.7_TO_v1.0.md` | Developer migration guide (Verdict → Prediction) |
| `docs/EXAMPLES.md` | Adding your own thesis to the calibration table |

---

## Compliance disclaimer

GLOSTAT is an information tool for personal use. Output is a probability
distribution with explicit confidence intervals and source provenance — not
an investment recommendation, not a securities solicitation, not financial
advice. Past calibration data does not guarantee future predictive
performance. Users are responsible for their own decisions.
