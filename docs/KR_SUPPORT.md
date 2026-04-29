# GLOSTAT v1.1 — KR (Korea Exchange) Support Guide

> Status: ACTIVE 2026-04-29. K1 milestone (KR support landed). Production routing
> for XKRX (KOSPI) live; XKOS (KOSDAQ) covered by yfinance fundamentals only.
>
> Information tool. Not investment advice. Past calibration ≠ future performance.

---

## What changed in v1.1

`glostat predict 005930` (삼성전자), `glostat predict 096770` (SK이노베이션), and
any other KOSPI 200 6-digit code now produces a Prediction with **at least three
active signals** instead of falling back to base-rate. The change is additive:
v1.0 US predictions (AAPL, MSFT, etc.) are unaffected.

| Surface | v1.0 behavior | v1.1 behavior |
|---------|---------------|---------------|
| `glostat predict 096770` | baseline fallback (52% / +0bps, 0 active signals) | 3+ active signals, signal-driven edge |
| `E_FOREIGN_REVERSAL` | static neutral=0 wrapper | live Naver-backed expert (TITAN B4 port) |
| `E_FUNDAMENTAL_KR` | did not exist | new expert (yfinance .KS PER/ROE/dividend) |
| `E_TIME` | US-only gate | universe-agnostic (Ichimoku — works for any equity OHLCV) |
| Snapshot UAID | `XNAS.{ticker}` for all | `XKRX.{code}` for KR (proper market segregation) |

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
| 외인/기관 net flows | Naver Finance scraper (`finance.naver.com/item/frgn.naver`) | KIS API quotes |
| Earnings calendar | yfinance | KRX disclosure (DART filings) |

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

## E_FOREIGN_REVERSAL (live Naver wiring)

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
  tests/test_e_foreign_reversal_universe.py

# Live prediction
GLOSTAT_SEC_USER_AGENT="Your Name your@email" uv run glostat predict 096770
GLOSTAT_SEC_USER_AGENT="Your Name your@email" uv run glostat predict 005930

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
5. **DART API integration deferred to Phase 2.5.** XBRL-grade financial-statement
   trend (revenue / NI growth, balance-sheet quality) is the obvious next data
   source. Out of scope for v1.1 K1.
6. **E_FUNDAMENTAL_KR has no calibration data yet.** Bootstrapped at AUC=0.50,
   n=0 → weight=0 in the composite. Run a 90-day hindcast + IS/OOS split
   (INV-GS-026) to graduate it from "shows raw_score" to "carries weight".

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
