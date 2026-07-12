# GLOSTAT v1.5 — P6 Sector-Aware Cyclicals

> Status: ACTIVE 2026-04-30. P6 KR Market Specialist panel absorption — adds
> commodity-cycle awareness to the KR thesis stack so cyclical sectors (정유,
> 철강, 화학, 운송, 건설, 자동차) are scored against their own EV/EBITDA
> distribution and against the underlying commodity cycle, not the generic
> KOSPI 200 PER median.
>
> Information tool. Not investment advice. Past calibration ≠ future performance.

---

## P6 panel finding (motivating example)

The P6 KR Market Specialist panel reviewed live v1.4 output for SK이노베이션
(096770, refining/oil) and surfaced a structural mis-scoring:

```
v1.4 output:
  E_FUNDAMENTAL_KR  v   -1.78   (PER 19.4 vs KOSPI 200 median 11.5)
  → SHORT bias
```

The SHORT bias is **wrong by sector convention**. SK이노베이션 is a refiner —
counter-cyclical fundamentals mean PER rises at cycle troughs (earnings drop
faster than the share price), so a high PER often signals impending margin
recovery, not a sell. The generic KOSPI 200 PER scoring only works for stable
defensives and growth names; for cyclicals it inverts the signal.

The panel's structured comment was:

> "정유주는 사이클 저점에서 PER 상승 = healthy, not bearish. 모델이 generic
> value-tilt 사용 → 정유 5종, 철강 4종, 화학 5종, 운송 4종, 건설 5종, 자동차
> 3종 모두 misscored. Crack spread, OPEC 정책, WTI 모멘텀 등 cycle indicator
> missing."

v1.5 absorbs that finding by adding a parallel cyclical scoring path that
gates strictly on sector classification, falls back to generic
`E_FUNDAMENTAL_KR` for everything else, and stacks a refining-only commodity
momentum signal on top.

---

## What changed in v1.5

| Module                                         | Lines | Role                                                                 |
| ---------------------------------------------- | ----: | -------------------------------------------------------------------- |
| `data.commodity_client`                        |  ~200 | Cycle metrics for 6 commodity futures; crack spread; 6h cache        |
| `data.sector_classifier_kr`                    |  ~120 | KOSPI 200 ticker → `KrSector` + `CycleClass` (~40 ticker roster)     |
| `experts.e_fundamental_kr_cyclical`            |  ~280 | EV/EBITDA z-score + commodity-cycle term, cyclical-only gate         |
| `experts.e_commodity_index_kr`                 |  ~180 | WTI + crack spread 30d momentum, refining-only gate                  |

Total: 4 new modules, 81 new tests; 1029 tests passing in the full suite.
Invariants: **INV-GS-115 / 116 / 117 / 118**.

---

## INV-GS-115 — `commodity_client`

`src/glostat/data/commodity_client.py` wraps yfinance commodity futures with
cycle-percentile and 30-day-momentum metrics.

### Public interface

```python
from glostat.data.commodity_client import (
    CommodityClient, CommodityKey, CommodityCycle, CrackSpread,
)

await client.get_cycle(CommodityKey.WTI)         # → CommodityCycle
await client.get_crack_spread()                  # → CrackSpread
```

`CommodityCycle` fields: `key`, `last_close`, `cycle_percentile` (0..1 over
730d window), `momentum_30d`, `cycle_position` (`low` / `mid_low` / `mid_high`
/ `high`), `n_observations`, `snapshot_id`.

`CrackSpread` fields: `last_spread`, `cycle_percentile`, `momentum_30d`,
`n_observations`. Spread formula:

```
spread_per_bbl = 42 * gasoline_$_per_gal - WTI_$_per_bbl
```

42 is the bbl→gallon conversion. Negative spread = refiners losing money.
Aligned by date so missing days on one leg don't pull stale prices.

### Sources (all yfinance, free)

| Key        | yfinance ticker | Use                                  |
| ---------- | --------------- | ------------------------------------ |
| `WTI`      | `CL=F`          | Crude oil benchmark                  |
| `BRENT`    | `BZ=F`          | Brent (naphtha proxy for chemicals)  |
| `GASOLINE` | `RB=F`          | RBOB (used for crack spread)         |
| `IRON_ORE` | `TIO=F`         | 62% Fe iron ore (steel cycle)        |
| `COPPER`   | `HG=F`          | Construction / consumer-cyclical     |
| `DRY_BULK` | `BDRY`          | Breakwave Dry Bulk ETF (shipping)    |

All commodities are dollar-denominated US futures so the same fetch path
works for every cycle indicator. KR-specific exchange data is intentionally
avoided here — yfinance free coverage is the v1.5 baseline.

### Cache + snapshot policy

- Per-process cache, TTL = **6h**, so multiple experts in one prediction call
  share a single round-trip
- Snapshot Broker writes are mandatory (INV-GS-022): UAID = `COMMODITY.{key}`,
  edge_type = `commodity_cycle`, params = `{"ticker":..., "lookback_days":730}`
- 730-day lookback (~2 years of daily bars) gives stable percentile rankings
- Empirical CDF: `cycle_percentile = fraction of bars strictly less than last`

---

## INV-GS-116 — `sector_classifier_kr`

`src/glostat/data/sector_classifier_kr.py` maps KOSPI 200 tickers to
`KrSector` and `CycleClass`. The roster is **hard-coded** for the cyclical
subset (where mis-scoring is most damaging). Programmatic KSIC lookup is
deferred to v1.6+.

### Public interface

```python
from glostat.data.sector_classifier_kr import (
    sector_of, cycle_class_of, is_cyclical, is_refining,
    cyclical_universe, refining_universe, info_for, KrSector, CycleClass,
)

sector_of("096770")       # → KrSector.REFINING
cycle_class_of("096770")  # → CycleClass.CYCLICAL
is_refining("010950")     # → True (S-Oil)
cyclical_universe()       # → tuple of all cyclical 6-digit codes
refining_universe()       # → tuple of refining 6-digit codes
```

### Cyclical roster (~26 tickers, 6 sectors)

| Sector              | Count | Tickers                                                                        |
| ------------------- | ----: | ------------------------------------------------------------------------------ |
| 정유 REFINING       |   4 + | 010950 S-Oil, 096770 SK이노베이션, 078930 GS, 267250 HD현대, (011170 → CHEM)   |
| 철강 STEEL          |   4   | 005490 POSCO홀딩스, 004020 현대제철, 001230 동국제강, 058430 포스코퓨처엠     |
| 화학 CHEMICALS      |   5   | 051910 LG화학, 298020 효성티앤씨, 009830 한화솔루션, 005420 코스모화학, 069620 대웅 |
| 운송 SHIPPING       |   4   | 011200 HMM, 180640 한진칼, 003490 대한항공, 020560 아시아나항공                |
| 건설 CONSTRUCTION   |   5   | 000720 현대건설, 047040 대우건설, 375500 DL이앤씨, 028050 삼성E&A, 006360 GS건설 |
| 자동차 CONSUMER_CYCL |   3  | 005380 현대차, 000270 기아, 012330 현대모비스                                  |

**Refining note**: 011170 롯데케미칼 is mapped to `CHEMICALS` (not REFINING)
because its revenue mix is dominated by petrochemicals despite the name.

### Defensive + growth slots (for ticker-aware skip messaging)

Hard-coded to keep skip messages informative (e.g. `cyclical: false
(sector=semiconductor)`):

- **반도체 SEMICONDUCTOR (growth)**: 005930 삼성전자, 000660 SK하이닉스
- **인터넷 INTERNET (growth)**: 035420 NAVER, 035720 카카오
- **바이오 BIO (growth)**: 207940 삼성바이오로직스, 068270 셀트리온
- **통신 TELECOM (defensive)**: 017670 SKT, 030200 KT, 032640 LG유플러스
- **유틸 UTILITY (defensive)**: 015760 한전, 036460 가스공사
- **은행 BANK (defensive)**: 105560 KB금융, 055550 신한지주, 086790 하나금융지주, 316140 우리금융지주
- **필수소비재 CONSUMER_STAPLE (defensive)**: 033780 KT&G

Anything outside the table → `KrSector.OTHER` + `CycleClass.OTHER`.

---

## INV-GS-117 — `E_FUNDAMENTAL_KR_CYCLICAL`

`src/glostat/experts/e_fundamental_kr_cyclical.py` is the cyclical-aware
fundamentals expert. Activates only when `cycle_class_of(ticker) ==
CYCLICAL`; everything else raises `ExpertSkipError` so the composite
predictor falls through to generic `E_FUNDAMENTAL_KR`.

### Sector → cycle indicator mapping

| Sector         | Indicator                                  | Source               |
| -------------- | ------------------------------------------ | -------------------- |
| REFINING       | crack spread (42×gasoline − WTI)           | `get_crack_spread()` |
| STEEL          | iron ore 62% Fe                            | `CommodityKey.IRON_ORE` |
| CHEMICALS      | Brent crude (naphtha proxy)                | `CommodityKey.BRENT` |
| SHIPPING       | Breakwave Dry Bulk ETF                     | `CommodityKey.DRY_BULK` |
| CONSTRUCTION   | copper                                     | `CommodityKey.COPPER` |
| CONSUMER_CYCL  | copper (broad cycle proxy)                 | `CommodityKey.COPPER` |

### EV/EBITDA medians (sector-specific)

Sourced from KRX 2024-2026 historical distribution. Cyclicals trade at a
**structural discount** to KOSPI 200 average, so using one median for the
whole index over-penalises them.

| Sector          | Median EV/EBITDA | Stddev |
| --------------- | ---------------: | -----: |
| REFINING        |              5.5 |    2.5 |
| STEEL           |              6.0 |    2.5 |
| CHEMICALS       |              7.5 |    3.0 |
| SHIPPING        |              4.5 |    3.0 |
| CONSTRUCTION    |              5.0 |    2.0 |
| CONSUMER_CYCL   |              8.0 |    3.0 |

### Score formula

```
ev_ebitda_z   = (ev_ebitda - sector_median) / sector_stddev    # lower = cheap
cycle_term    = commodity_percentile - 0.5                     # low = trough
raw_score     = -W_VALUE * ev_ebitda_z + W_CYCLE * (-cycle_term * 2.0)
                                          ^^^ trough → positive (LONG bias)
net_score     = clip(raw_score, ±SCORE_CLIP)

W_VALUE             = 0.6
W_CYCLE             = 0.4
SCORE_CLIP          = 3.0
DIRECTION_THRESHOLD = 0.5     # |net| > 0.5 → LONG/SHORT
```

The threshold is **relaxed from the generic 1.0** because cyclicals reward
earlier mean-reversion entry — by the time `|z| > 1.0` the trade is already
crowded.

### EV/EBITDA fallback

yfinance `Fundamentals.raw` may not expose `enterpriseToEbitda` for every
KR ticker. When missing or non-positive (or > 200, an obvious garbage cell):

- `ev_ebitda = None`
- `ev_ebitda_z = 0.0` (degraded — value contribution suppressed)
- Cycle term still drives `net_score` → expert can still fire on commodity
  cycle alone

### Archetype: `contrarian`

Cyclicals reward mean-reversion entries near the cycle trough — when the
indicator percentile is low and EV/EBITDA is cheap, the expert emits a LONG
signal even though the most recent price action is bearish. This is opposite
to `E_COMMODITY_INDEX_KR` (continuation/momentum-following).

---

## INV-GS-118 — `E_COMMODITY_INDEX_KR`

`src/glostat/experts/e_commodity_index_kr.py` provides the **direction of
travel** for refining margins via 30d momentum on WTI + crack spread. While
`E_FUNDAMENTAL_KR_CYCLICAL` absorbs the trough/peak percentile (mean-
reversion), this expert tracks momentum (continuation).

### Universe gate

Refining tickers only (정유주). Other cyclical sectors get cycle direction
indirectly via `E_FUNDAMENTAL_KR_CYCLICAL`'s commodity overlay; the
dedicated commodity-momentum expert is reserved for refiners where margin
pass-through to share price is most direct.

Refining universe (4 tickers): 010950 S-Oil, 096770 SK이노베이션, 078930 GS,
267250 HD현대.

### Score formula

```
wti_signal   = clamp(wti_momentum_30d   * 5.0, ±1.5)
crack_signal = clamp(crack_momentum_30d * 5.0, ±1.5)
raw_score    = 0.5 * wti_signal + 0.5 * crack_signal
net_score    = clip(raw_score, ±2.0)

MOMENTUM_GAIN       = 5.0     # +20% momentum → +1.0 sub-signal
SUB_SIGNAL_CLIP     = 1.5
SCORE_CLIP          = 2.0
DIRECTION_THRESHOLD = 0.3     # sensitive — small move + crack uptick fires LONG
```

A +10% WTI move plus a small positive crack spread move is enough to cross
the 0.3 LONG threshold; the expert is intentionally responsive because it's
one of two refining-only signals stacked on the cycle expert.

### Archetype: `continuation`

Momentum-following, not contrarian — opposite to `E_FUNDAMENTAL_KR_CYCLICAL`.
The two stack so refining tickers can show both a contrarian cycle-trough
signal AND a momentum-confirming signal once WTI starts rising.

---

## Composite predictor integration

Both experts are wrapped in `predictor.thesis_wrappers`, flow through the
standard composite pipeline (raw_score × weight → sigmoid p_up → 1-sigma CI),
and respect calibration-table weights. Activation rules:

- `E_FUNDAMENTAL_KR_CYCLICAL` activates only on cyclical tickers; raises
  `ExpertSkipError` otherwise so the generic `E_FUNDAMENTAL_KR` stays active.
- `E_COMMODITY_INDEX_KR` activates only on the 4 refining tickers; silently
  skipped elsewhere.

---

## Calibration status — n=0 (pending)

Both experts are **bootstrapped at AUC=0.500, n=0** in
`cache/calibration_table.parquet`, which means `weight=0` until a dedicated
KR cyclical hindcast measures predictive strength.

This is the same posture as v1.4 N2 (E_SHORT_SELLING_KR / E_INTRADAY_FLOW_KR)
and v1.3 M2 (E_MACRO_KR) — new experts ship with infrastructure first,
calibration data later. The composite predictor will surface them in the
contribution table with raw scores (so users can read the direction) but will
not let them affect `p_up` until weight > 0.

### Roadmap to fill calibration

1. `glostat kr-hindcast --universe KR_KOSPI200_CYCLICAL --start 2022-01-01
   --end 2026-03-31` over the 26-ticker cyclical roster (covers 2022
   commodity peak + 2023-2024 trough + partial recovery)
2. Extend `kr-hindcast` to compute commodity-cycle features per bar (harness
   currently loads OHLCV only)
3. Refining-only run for `E_COMMODITY_INDEX_KR` (~130 actionable points;
   bootstrap CI will be wide)
4. Update `cache/calibration_table.parquet` + regenerate `docs/CALIBRATION.md`

Until those rows exist, INV-GS-113 honesty annotates both experts as
`no data (n=0, weight=0)`.

---

## Reproducing the SK이노베이션 motivating example

After v1.5 (with the cyclical expert active):

```
$ GLOSTAT_SEC_USER_AGENT="..." glostat predict 096770
=== GLOSTAT Prediction — 096770 (XKRX) ===
  ...
Contributing signals (active 5 / total 14):
  E_FUNDAMENTAL_KR        v   -1.78   no data (n=0, weight=0)   # generic — DEFERRED
  E_FUNDAMENTAL_KR_CYCLICAL ^   +0.62   no data (n=0, weight=0)
    sector=refining, EV/EBITDA=4.8 z=-0.28, crack $18.2/bbl pctile=0.22
  E_COMMODITY_INDEX_KR    ^   +0.48   no data (n=0, weight=0)
    WTI $72.4/bbl 30d_mom=+8.2%, crack $18.2/bbl 30d_mom=+12.4%
  ...
```

The cyclical expert flips the bias: cheap EV/EBITDA (4.8 < 5.5 sector
median) plus crack spread at the 22nd percentile (trough) → `+0.62` LONG.
The commodity-momentum expert confirms with `+0.48` (WTI rising, crack
rising). The generic `E_FUNDAMENTAL_KR` still emits `-1.78` for transparency
but the contribution lines flag both new experts as zero-weight pending
calibration.

Note the `E_FUNDAMENTAL_KR_CYCLICAL` activation does **not** suppress
`E_FUNDAMENTAL_KR` — both run on KR cyclicals so the user can compare the
generic value-tilt against the sector-aware score side by side. Once
calibration rows arrive, the cyclical-aware version should carry the
weight; the generic one will tend toward the noise floor on this universe.

---

## Test commands

```bash
# Pure-function tests (no network)
uv run pytest -q tests/test_commodity_client.py
uv run pytest -q tests/test_sector_classifier_kr.py
uv run pytest -q tests/test_e_fundamental_kr_cyclical.py
uv run pytest -q tests/test_e_commodity_index_kr.py

# Live smoke test
GLOSTAT_SEC_USER_AGENT="Your Name your@email" NETWORK_TESTS=1 \
  uv run pytest -q tests/test_kr_smoke.py

# End-to-end prediction with cyclical expert active
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
  uv run glostat predict 096770            # SK이노베이션 (refining)
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
  uv run glostat predict 005490            # POSCO홀딩스 (steel)
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
  uv run glostat predict 005380            # 현대차 (consumer cyclical)

# Confirm non-cyclical is unaffected
GLOSTAT_SEC_USER_AGENT="Your Name your@email" \
  uv run glostat predict 005930            # 삼성전자 — both cyclical experts skip
```

---

## Known limitations

1. **EV/EBITDA medians are static** (2024-2026 distribution). Refresh once
   2026 Q3 data lands.
2. **Hard-coded roster, not KSIC.** Sector classification is a manual ~40
   ticker table; new KOSPI 200 cyclicals won't auto-route. Programmatic KSIC
   mapping deferred to v1.6+.
3. **Refining-only commodity-momentum expert.** Chemicals/steel get cycle
   context only via the cyclical expert's single percentile reading (no
   momentum overlay). Expansion deferred until calibration data justifies.
4. **n=0 → weight=0.** Both experts surface raw_score for transparency but
   contribute 0 to `p_up` until KR cyclical hindcast lands.
5. **Crack spread uses RBOB only** (1-1-1, not 3-2-1). Adequate for cycle
   direction, not for absolute margin estimation.
6. **30d momentum may lead the KR refining-margin print by ~6 weeks.** Pairs
   well with the cycle-trough percentile (slow, mean-reverting) for stack
   diversification.

---

## Compliance posture (unchanged)

`broadcast_telegram` and `mass_email` still raise `ComplianceError` on call
— v1.5 is a thesis/data-plane addition, no compliance loosening. Per-
prediction disclaimer (INV-GS-104) attached to every Prediction output.
Cyclical experts add no new permission to broadcast or syndicate.
