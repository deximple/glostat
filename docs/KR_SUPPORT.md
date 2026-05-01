# GLOSTAT v1.4 — KR (Korea Exchange) Support Guide

> Status: ACTIVE 2026-04-29. v1.4 N1 (KR 3-source investor flows: KIS/Toss/Naver)
> + v1.4 N2 (KR 공매도 + intraday flow experts) on top of v1.3 M2 (ECOS BoK
> macro), v1.2 L1 (Phase KR calibration) + L2 (DART API), and v1.1 K1 (KR
> production support). Production routing for XKRX (KOSPI) live; XKOS (KOSDAQ)
> covered by yfinance fundamentals only.
>
> Information tool. Not investment advice. Past calibration ≠ future performance.

---

## What changed in v1.4

v1.4 absorbs TITAN's deep KR practitioner capabilities while keeping the
prediction-tool framing (INV-GS-101 — no BUY/SELL output):

1. **N1 — KR 3-source investor flows**: new `KisClient` (real-time intraday,
   read-only KIS Open API), new `TossClient` (local-parquet cache reader,
   TITAN pattern), and a `fuse_three_source_flows()` helper in
   `naver_kr_client` that median-merges Naver + Toss + KIS daily summaries
   per date. When ≥ 2 sources disagree by > 50%, a warning is logged and
   the median takes over so a single bad scrape can't poison
   `E_FOREIGN_REVERSAL`. Source priority: KIS (real-time) > Toss (cache) >
   Naver (scrape, always available).
2. **N2 — KR 공매도 + intraday flow experts**: new `KrxShortClient` (free
   public KRX AJAX endpoint), new `EShortSellingKrExpert` (TITAN E5++
   inspired — short balance change + squeeze candidate detection), new
   `EIntradayFlowKrExpert` (TITAN E5+ inspired — Naver baseline + KIS
   overlay, foreign-flow acceleration). Both are bootstrapped at AUC=0.50
   / n=0 in the calibration table; weight=0 until a dedicated KR hindcast
   measures predictive strength.

KR predictions now have up to **7 active signal slots** (was 5):
`E_FUNDAMENTAL_KR`, `E_TIME`, `E_FOREIGN_REVERSAL` (3-source-aware),
`E_INSIDER_KR`, `E_MACRO_KR`, **`E_SHORT_SELLING_KR`**, **`E_INTRADAY_FLOW_KR`**.

Total contribution slots: 16 (was 14 in v1.3).

## What changed in v1.3

v1.3 adds one new KR thesis on top of v1.2:

1. **M2 — ECOS BoK macro overlay**: new `E_MACRO_KR` expert backed by the
   ECOS (한국은행 경제통계시스템) OpenAPI. Aggregates 4 macro signals (BoK
   base rate Δ, KRW/USD trend, CPI surprise, KOSPI momentum) into a single
   net score for any KR ticker. Free + 10,000 calls/day per key. See
   `docs/ECOS_API_SETUP.md`.

KR predictions now have up to **5 active signal slots** (was 4):
`E_FUNDAMENTAL_KR`, `E_TIME`, `E_FOREIGN_REVERSAL`, `E_INSIDER_KR`, **`E_MACRO_KR`**.

## What changed in v1.2

v1.2 builds on the v1.1 K1 foundation with two additions:

1. **L1 — Phase KR hindcast → real calibration**: replaces the bootstrapped
   AUC=0.5/n=0 placeholders for `E_FUNDAMENTAL_KR` and `E_TIME_KR` with
   measured numbers from a KOSPI 200 hindcast. Run via `glostat kr-hindcast`.
2. **L2 — DART API integration**: optional canonical KR financial-statement
   feed (replaces yfinance ROE/EPS gaps) and new `E_INSIDER_KR` expert
   (DART `elestock.json`, the KR equivalent of SEC Form 4). Fully gated on
   `GLOSTAT_DART_API_KEY` — see `docs/DART_API_SETUP.md`.

## What changed in v1.1

`glostat predict 005930` (삼성전자), `glostat predict 096770` (SK이노베이션), and
any other KOSPI 200 6-digit code now produces a Prediction with **at least three
active signals** instead of falling back to base-rate. The change is additive:
v1.0 US predictions (AAPL, MSFT, etc.) are unaffected.

| Surface | v1.0 behavior | v1.1 behavior | v1.2 delta |
|---------|---------------|---------------|------------|
| `glostat predict 096770` | baseline fallback (52% / +0bps, 0 active signals) | 3+ active signals, signal-driven edge | +1 slot (E_INSIDER_KR), real KR calibration |
| `E_FOREIGN_REVERSAL` | static neutral=0 wrapper | live Naver-backed expert (TITAN B4 port) | calibration measured via Phase KR hindcast |
| `E_FUNDAMENTAL_KR` | did not exist | new expert (yfinance .KS PER/ROE/dividend) | DART overlay when key configured |
| `E_TIME` | US-only gate | universe-agnostic (Ichimoku — works for any equity OHLCV) | distinct E_TIME_KR calibration cell |
| `E_INSIDER_KR` | did not exist | did not exist | DART elestock cluster (graceful skip if no key) |
| Snapshot UAID | `XNAS.{ticker}` for all | `XKRX.{code}` for KR (proper market segregation) | unchanged |

---

## Universe coverage

`configs/universes/kospi200.txt` — pinned snapshot, 200 tickers, refreshed
quarterly. Includes the megacap list called out in K1 plus 173 next-by-free-float
constituents:

- 005930 삼성전자, 000660 SK하이닉스, 005380 현대차, 035420 NAVER,
  005490 POSCO, 051910 LG화학, 207940 삼성바이오, 068270 셀트리온,
  035720 카카오, 105560 KB금융, 055550 신한지주, 028260 삼성물산,
  003670 포스코퓨처엠, 012330 현대모비스, 015760 한국전력, 017670 SK텔레콤,
  030200 KT, 032830 삼성생명, **096770 SK이노베이션**, 010130 고려아연,
  086790 하나금융지주, 066570 LG전자, 003550 LG, 034730 SK,
  009150 삼성전기, 011200 HMM, 010950 S-Oil, ...

`configs/universes/kospi200_top30.txt` — subset (30 tickers) for fast iteration
and sector-stat bootstrap during dev.

Universe loading is exposed in code at:
- `glostat.predictor.kr_universe.KOSPI200_UNIVERSE` (frozenset, module-level cache)
- `glostat.predictor.kr_universe.is_kospi200(ticker)` (membership predicate)
- `glostat.data.universe.load_universe("KR_KOSPI200")` (full loader path)

---

## Data sources

| Signal | Free source | Paid alternative |
|--------|-------------|------------------|
| OHLCV (KR) | yfinance `005930.KS` | KIS / KRX direct (Phase 2.5) |
| Fundamentals (PER/ROE/div) | yfinance `005930.KS` info | DART API (Phase 2.5 — XBRL grade) |
| 외인/기관 net flows (daily, share count) | Naver Finance scraper (`finance.naver.com/item/frgn.naver`) | — |
| 외인/기관 net flows (daily, KRW) — v1.4 N1 | Toss local parquet cache (`cache/toss/{code}.parquet`) | KIS Open API daily summary |
| 외인/기관 net flows (intraday, real-time) — v1.4 N1 | — | **KIS Open API** (read-only paths only) |
| 공매도 잔고 + 거래량 — v1.4 N2 | KRX 정보데이터시스템 public AJAX | — |
| Earnings calendar | yfinance | KRX disclosure (DART filings) |
| Macro (BoK rate, KRW/USD, CPI, KOSPI index) | ECOS BoK OpenAPI | KOSIS / IMF SDMX |

Cost: **$0 / month**. Fully free-stack (INV-GS-036 still enforces no Bigdata
MCP in MVP phase).

Naver client: `glostat.data.naver_kr_client.NaverKrClient`. 1 req/sec self-throttle,
parquet cache at `cache/naver_kr/{code}.parquet`. ~120 trading days per fetch
covers the 4-day prior window and delivers OOS hindcast input.

---

## KR ticker normalization (INV-GS-106)

Internal canonical form: bare 6-digit KRX code (e.g. `005930`). Three helpers:

```python
from glostat.data.data_router import (
    is_kr_ticker, normalize_kr_ticker, to_yfinance_kr_ticker,
)

is_kr_ticker("005930")       # True
is_kr_ticker("005930.KS")    # True
is_kr_ticker("AAPL")         # False

normalize_kr_ticker("005930.KS")          # "005930"
to_yfinance_kr_ticker("005930")           # "005930.KS"  (KOSPI default)
to_yfinance_kr_ticker("999999", default_suffix=".KQ")  # "999999.KQ"
```

`yfinance_client.get_ohlcv("005930", ...)` → transparently fetches `005930.KS`.
Snapshot Broker UAID for KR is `XKRX.005930` (not `XNAS.005930`), so KR + US
snapshots stay collision-free.

---

## E_FUNDAMENTAL_KR

KR-specific PER/ROE/dividend-yield z-score using KOSPI 200 historical medians:

| Field | KR median | KR stddev | Weight |
|-------|----------:|----------:|-------:|
| PER | 11.5 | 6.0 | 0.45 (value tilt — inverted: cheap = +) |
| ROE | 8.5% | 4.5% | 0.40 (quality) |
| div_yield | 1.8% | (capped ±2.0) | 0.15 (income) |

Differs from US `E_FUNDAMENTAL`:
- No SEC EDGAR XBRL trend (KR has no equivalent free XBRL feed)
- Lower PER median (chaebol discount + manufacturing-heavy mix)
- Direction threshold relaxed 1.5 → 1.0 (KR is noisier)
- Calibration: bootstrapped at AUC=0.50, n=0 (weight=0) until first hindcast run

Source code: `src/glostat/experts/e_fundamental_kr.py`.

---

## E_MACRO_KR (v1.3 M2 — ECOS BoK macro overlay)

KR macro context aggregated into a single net score. ECOS-backed; gracefully
skipped when `GLOSTAT_ECOS_API_KEY` is unset.

| Component | Source | Aggregation | Weight | Sign |
|-----------|--------|-------------|-------:|------|
| BoK base rate Δ3m | 722Y001 / 0101000 (M) | latest minus 3-mo prior | 1.00 | inverted (cuts → bull) |
| KRW/USD trend 60d | 731Y001 / 0000001 (D) | (latest / 60d-ago) − 1 | 0.50 × export_exposure | positive (KRW weak → exporters bull) |
| CPI surprise vs trailing 12m | 901Y009 / 0 (M) | (latest − mean12) / mean12 | 0.70 | inverted (above-trend → tightening fear) |
| KOSPI 60d momentum | 802Y001 / 0001000 (D) | (latest / 60d-ago) − 1 | 0.80 | positive (continuation) |

`net_score = clip([-3, +3], sum of weighted z-scores)`. Direction threshold ±0.6
(KR macro shifts slowly). Universe: ANY KR ticker (no KOSPI 200 sub-screen
since macro applies broadly). Calibration: bootstrapped at AUC=0.500, n=0
(weight=0) until first hindcast that includes E_MACRO_KR runs.

Source code: `src/glostat/experts/e_macro_kr.py`.
Setup: `docs/ECOS_API_SETUP.md`.

---

## v1.4 N1 — KR 3-source investor flows

### KIS Open API (real-time intraday, optional)

`src/glostat/data/kis_client.py` wraps two read-only KIS endpoints:

- `get_intraday_flows(ticker)` → 외국인/기관/개인/프로그램 net buy in shares
  (`FHKST01010900` — 종목별 투자자별 매매동향)
- `get_daily_summary(ticker)` → end-of-day net buy in 원화 (`FHKST01010800`
  — 종목별 일별 매매동향)

20 req/sec self-throttle, OAuth token cached + auto-refreshed (10-minute
margin before 1-day expiry), Snapshot Broker integration. Requires
`GLOSTAT_KIS_APP_KEY` + `GLOSTAT_KIS_APP_SECRET`. **Order-execution
endpoints intentionally NOT wrapped** — INV-GS-101 forbids action output.

Setup: `docs/KIS_API_SETUP.md` (free portal registration).

### Toss local cache (TITAN pattern, optional)

`src/glostat/data/toss_client.py` reads pre-exported Toss app data from
`cache/toss/{code}.parquet`. Schema: `(bar_date, ticker, foreign_net_won,
institutional_net_won, retail_net_won, source="toss")`. No live API —
operators populate the cache manually (mirrors TITAN's pattern). Skip
silently when files are absent.

### 3-source fusion

`fuse_three_source_flows()` in `naver_kr_client.py` merges all three
sources by date, picks shares-units when Naver is present (KIS/Toss cross-
check) or KRW-units otherwise, and uses the median when ≥ 2 sources agree
on units. Disagreement > 50% logs a warning but still emits the median —
keeps a single bad scrape from poisoning downstream signals.

`E_FOREIGN_REVERSAL` consumes the fused output: pattern detection still
runs on Naver share counts (so the existing calibration is preserved),
but the metadata records which sources were available so downstream
predictions show 3-source verification.

---

## v1.4 N2 — KR 공매도 + intraday flow experts

### E_SHORT_SELLING_KR (KRX-backed, free, public)

`src/glostat/data/krx_short_client.py` scrapes the KRX public AJAX
endpoint (https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd) for:

- `MDCSTAT30501` → daily short balance per ticker (잔고 수량 / 금액 / 비율)
- `MDCSTAT30401` → daily short volume per ticker (거래량 / 거래대금 / 비중)

5 req/sec self-throttle (no published rate limit, but KRX is regulator-
run; keep load low). Snapshot Broker integration. **Free, no API key.**

`src/glostat/experts/e_short_selling_kr.py` (TITAN E5++ inspired):

| Component | Threshold | Score | Direction |
|-----------|-----------|------:|-----------|
| balance decrease (3-day) | any | +0.6 × strength | bull (SHORT_COVER) |
| balance decrease + price up | any | +0.6 | bull (SHORT_SQUEEZE_RISK) |
| balance increase above 80th-pctile | rolling | -0.6 | bear (SHORT_PRESSURE) |
| short ratio ≥ 10% + price up | volume | +0.3 | bull (squeeze risk) |
| short ratio ≥ 10% + price down | volume | -0.3 | bear (pressure) |

Universe: KOSPI 200 only (liquidity needed for short-balance changes to
be meaningful). Calibration: bootstrapped at AUC=0.500, n=0 (weight=0)
until a dedicated KR short-selling hindcast runs.

### E_INTRADAY_FLOW_KR (Naver baseline + KIS overlay)

`src/glostat/experts/e_intraday_flow_kr.py` (TITAN E5+ inspired):

- Compute trailing 5-day foreign-flow average from Naver bars
- Compute foreign-flow acceleration (recent half vs earlier half)
- When KIS is wired: promote today's intraday running total into the
  acceleration window
- Foreign flow leading institutional flow (≥ 50% of organ_avg in same
  direction) → confirmation boost

| Component | Threshold | Score | Signal |
|-----------|-----------|------:|--------|
| foreign_recent_avg > 0 | any | +0.5 | FLOW_IMPROVING |
| foreign_recent_avg < 0 | any | -0.5 | FLOW_DETERIORATING |
| acceleration > 30% (signed) | rate | ±0.5 | momentum confirm |
| foreign leads organ | ratio ≥ 50% | ±0.5 | strength confirm |

Universe: KOSPI 200 only (intraday acceleration needs liquidity).
Calibration: bootstrapped at AUC=0.500, n=0 (weight=0).

---

## E_FOREIGN_REVERSAL (live Naver wiring + 3-source aware in v1.4 N1)

Direct port of TITAN B4 REVERSAL_BUY pattern:

- Day t-4 .. t-1: foreign net SELL (4 consecutive days)
- Day t: foreign net BUY → REVERSAL_BUY → LONG
- Confirmation: 기관 (institutional) also buying same day → confidence × 1.3

Calibration: Phase 1D live hindcast over 2024-01 .. 2026-03:
- n_actionable = 424 events (KOSPI 20 megacap subset)
- AUC = 0.467 (under 0.5 → directional_bias = -1; composite flips score)
- Sharpe = +0.58 overall (IS +0.18 / OOS +1.46 — pattern stable OOS)
- TITAN B4 historical: 60.3% hit (n=58); v1.1 generalization: 52.2% (n=424,
  -8.1pp gap)

Source code: `src/glostat/experts/e_foreign_reversal.py:EForeignReversalExpert`.

---

## E_TIME (universe-agnostic in v1.1)

`E_TIME` no longer skips KR tickers. The Ichimoku 257-day base only requires
OHLCV — no SEC filings, no exchange-specific signals. v1.0 had a US-only gate
that has been removed (crypto perpetuals still skip — daily-bar Ichimoku
doesn't transfer to 24/7 funding-driven instruments).

Calibration unchanged: AUC 0.520, n=200 (synthetic baseline pending dedicated
hindcast — see `synthetic_calibration_for_mock` in `predictor/calibration.py`).

---

## Test commands

```bash
# Live KR smoke test (requires NETWORK_TESTS=1 + GLOSTAT_SEC_USER_AGENT)
GLOSTAT_SEC_USER_AGENT="Your Name your@email" NETWORK_TESTS=1 \
  uv run pytest -q tests/test_kr_smoke.py

# Pure-function tests (no network)
uv run pytest -q tests/test_e_fundamental_kr.py tests/test_data_router_kr.py \
  tests/test_e_foreign_reversal_universe.py tests/test_e_insider_kr.py \
  tests/test_dart_client.py tests/test_phase_kr_hindcast.py

# v1.2 L1 — refresh the KR calibration table from hindcast
GLOSTAT_SEC_USER_AGENT="Your Name your@email" NETWORK_TESTS=1 \
  uv run glostat kr-hindcast --universe KR_KOSPI200_TOP30 \
  --start 2024-01-02 --end 2026-03-29 --max-concurrent 5

# v1.2 L2 — predict with DART overlay (requires GLOSTAT_DART_API_KEY)
GLOSTAT_DART_API_KEY="..." GLOSTAT_SEC_USER_AGENT="..." \
  uv run glostat predict 096770

# Live prediction (no DART)
GLOSTAT_SEC_USER_AGENT="Your Name your@email" uv run glostat predict 096770
GLOSTAT_SEC_USER_AGENT="Your Name your@email" uv run glostat predict 005930

# Side-by-side comparison v1.1 vs v1.2
GLOSTAT_SEC_USER_AGENT="..." uv run python scripts/compare_sk_innovation.py

# Confirm AAPL still works after KR changes (regression)
GLOSTAT_SEC_USER_AGENT="Your Name your@email" uv run glostat predict AAPL
```

---

## Known limitations

1. **yfinance KR fundamentals are partial.** PER, dividend yield, market cap,
   beta usually populate; ROE, EPS, forward PE often missing. The expert
   degrades gracefully (skips when both PER and ROE are absent).
2. **Naver scraping is unofficial.** Naver may change HTML structure without
   notice — the parser is regex-based and bounded, but fragile. If parser
   breakdown is detected (`page_failed` log lines), refresh the regex against
   the current Naver markup.
3. **No sector-aware z-score for KR.** v1.1 uses a single KOSPI 200 median for
   PER and ROE. v1.2 plans sector-stat resolver from `configs/universes/kospi200.txt`
   + KRX sector mapping.
4. **KOSDAQ (XKOS) E_FOREIGN_REVERSAL not wired.** Naver covers KOSDAQ flows
   but the universe file (kospi200.txt) is KOSPI-only. Add a `kosdaq150.txt`
   for KOSDAQ 150 expansion in a follow-up.
5. **DART API integration landed in v1.2 L2** — see `docs/DART_API_SETUP.md`.
   Free 10,000 calls/day key required. Without it, `E_INSIDER_KR` skips
   gracefully and `E_FUNDAMENTAL_KR` runs on yfinance only.
6. **v1.1 had no E_FUNDAMENTAL_KR / E_TIME_KR calibration.** v1.2 L1
   `glostat kr-hindcast` produces measured AUC / Sharpe / OOS_deg per thesis
   from a configurable KR universe + window. Reports persist to
   `cache/hindcast/phase_kr/*.json` and feed `load_calibration()` directly.

---

## Reproducing the K1 motivating example

Before v1.1:
```
$ glostat predict 096770
=== GLOSTAT Prediction — 096770 (XNAS) ===
  up / down / sideways: 50.0% / 25.0% / 25.0%
  expected return: +0bps  (CI: -5bps .. +5bps)
  edge over baseline: +0.0pp
Contributing signals (active 0 / total 11):
  ...all skipped...
```

After v1.1:
```
$ GLOSTAT_SEC_USER_AGENT="..." glostat predict 096770
=== GLOSTAT Prediction — 096770 (XKRX) ===
  up / down / sideways: 50.6% / 30.4% / 19.0%
  expected return: +17bps  (CI: -16bps .. +50bps)
  edge over baseline: -1.4pp
Contributing signals (active 3 / total 12):
  E_FUNDAMENTAL          . skip   (ticker not US equity)
  E_TIME                 ^   +1.43  (AUC 0.520, n=200)
  ...
  E_FUNDAMENTAL_KR       v   -1.60  (AUC 0.500, n=0)
  E_FOREIGN_REVERSAL     -   +0.00  (AUC 0.467, n=424)
```

`E_FUNDAMENTAL_KR` shows a real raw_score (-1.60, expensive vs KOSPI 200 median);
weight is 0 only because the KR-specific calibration table is bootstrapped at
n=0. Once a hindcast runs, the weight will lift.

---

## Compliance posture (unchanged)

`broadcast_telegram` and `mass_email` still raise `ComplianceError` on call —
the v1.1 KR delta is data-plane only, no compliance loosening. Per-prediction
disclaimer still attached to every Prediction (INV-GS-104). KR tickers gain no
new permission to broadcast or syndicate.
