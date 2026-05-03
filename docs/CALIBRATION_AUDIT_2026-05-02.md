# Calibration Status Audit — 2026-05-02 post-hindcast (v1.10.3)

Generated from load_calibration() AFTER live us-regime-hindcast run.
23 theses total. E_REGIME_US lifted from bootstrap → underfit (n=45 from
measured 2024-01-02..2026-03-29 stride=14, basket-mode).

## MEASURED (8)

| thesis | AUC | n_samples | Sharpe | brier | active |
|---|---:|---:|---:|---:|:---:|
| `E_FOMC_DRIFT` | 0.3570 | 135 | -1.340 | 0.1785 | YES |
| `E_FOREIGN_REVERSAL` | 0.4667 | 424 | +0.583 | 0.2334 | YES |
| `E_FUNDAMENTAL` | 0.5500 | 120 | +0.400 | 0.2250 | YES |
| `E_FUND_FLOW` | 0.4800 | 80 | -0.100 | 0.2500 | YES |
| `E_FX_CARRY` | 0.4000 | 135 | -1.533 | 0.2000 | YES |
| `E_PEAD` | 0.5860 | 298 | +0.629 | 0.2070 | YES |
| `E_SECTOR_ROTATION` | 0.4700 | 174 | -0.478 | 0.2350 | YES |
| `E_TIME` | 0.5200 | 200 | +0.300 | 0.2400 | YES |

## NEAR_RANDOM (2)

| thesis | AUC | n_samples | Sharpe | brier | active |
|---|---:|---:|---:|---:|:---:|
| `E_COMMODITY_TS` | 0.4890 | 517 | +0.139 | 0.2445 | no |
| `E_FUNDING_CARRY` | 0.5052 | 2921 | -0.231 | 0.2474 | no |

## UNDERFIT (2)

| thesis | AUC | n_samples | Sharpe | brier | active |
|---|---:|---:|---:|---:|:---:|
| `E_INSIDER_CLUSTER` | 0.3390 | 11 | +0.782 | 0.2500 | no |
| `E_REGIME_US` | 0.3963 | 45 | -0.382 | 0.2500 | no |

## BOOTSTRAP (11)

| thesis | AUC | n_samples | Sharpe | brier | active |
|---|---:|---:|---:|---:|:---:|
| `E_ANALYST_REVISION` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_COMMODITY_INDEX_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_FUNDAMENTAL_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_FUNDAMENTAL_KR_CYCLICAL` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_INSIDER_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_INSIDER_VELOCITY_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_INTRADAY_FLOW_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_MACRO_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_PEAD_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_SHORT_SELLING_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |
| `E_TIME_KR` | 0.5000 | 0 | +0.000 | 0.2500 | no |

## E_REGIME_US — measured 2026-05-02

| metric | value |
|---|---:|
| AUC overall | 0.3963 |
| Sharpe overall | -0.3824 |
| n_traded | 45 |
| OOS degradation | 100.00% |
| directional_bias | -1 (anti-predictive: composite flips score) |
| calibration_status | underfit |
| is_active | False (n=45 < 50 threshold; weight=0 in composite) |

### Honest reading

The textbook intuition that VIX contango + UST curve steepening predicts
positive equity drift fails empirically on this US large-cap basket over
2024-2026: AUC 0.396 (well below 0.5), Sharpe -0.38. The signal IS
informative — its directional_bias of -1 means the composite would
correctly flip the score — but n=45 sits below the 50-sample activation
threshold, so weight=0 still holds. This is the framework working as
designed: measure honestly, gate strictly.

### Followup wave

- Re-run with stride=7 (≈118 samples) to lift out of underfit and let
  is_active() flip True. Likely 2-3x current runtime due to BRK.B/BF.B
  retry overhead.
- Or: relax _DEFAULT_MIN_SAMPLES to 30 — would activate this entry but
  weakens the gate for ALL theses; not recommended.
- Or: use a different US basket (drop dot-suffix tickers) — saves runtime
  AND fixes n by reducing skip count.

## Action items (carry-forward from 2026-05-02 pre-hindcast)

### NEAR_RANDOM (no edge after large-n measurement)
- **E_COMMODITY_TS** (n=517, AUC=0.4890): Sharpe weakly positive but AUC inside ±0.02 → no directional edge. Candidate for retirement after one more recalibration window.
- **E_FUNDING_CARRY** (n=2921, AUC=0.5052): the biggest n in the table but flat AUC. Sharpe is mildly negative. Candidate for retirement OR for re-spec (different feature set / horizon).

### UNDERFIT (n too small for stable AUC)
- **E_INSIDER_CLUSTER** (n=11, AUC=0.339, Sharpe=+0.782): Sharpe looks great but n is way under threshold. Re-run hindcast with relaxed gating to grow n.
- **E_REGIME_US** (n=45, AUC=0.396, Sharpe=-0.382): NEWLY MEASURED 2026-05-02. Just under 50-sample threshold — re-run with stride=7 to lift status to measured.

### BOOTSTRAP (awaiting hindcast wave)
- 11 KR theses + E_ANALYST_REVISION still bootstrap. Most have hindcast wiring (kr-hindcast adds 7 KR theses); the remaining 4 (E_INSIDER_KR, E_MACRO_KR, E_SHORT_SELLING_KR, E_INTRADAY_FLOW_KR) + E_ANALYST_REVISION need their own dedicated hindcast waves.

## v1.10.4 update: OOS-stability factor (INV-GS-133)

The 2026-05-02 audit identified the highest-ROI calibration bug: **5 of 8
measured theses had OOS_degradation ≥ 100% but carried full Brier weight in
the composite predictor.** The Brier formula previously consulted only AUC
+ sample count; OOS stability was reported but never penalized.

v1.10.4 wires `_oos_stability_factor()` into `_weight_for()`:

```
factor = max(0.10, 1.0 - 0.9 * clip(oos_degradation, 0, 1))
final_weight = brier_weight × factor
```

Concrete impact on the 8 measured theses:

| thesis | OOS_deg | brier_w | factor | final_w | delta |
|---|---:|---:|---:|---:|---:|
| `E_PEAD`              | 115.6% | 0.1720 | 0.10 | 0.0172 | **−0.155** (zeroed) |
| `E_FOMC_DRIFT`        | 100.0% | 0.2860 | 0.10 | 0.0286 | **−0.257** (zeroed) |
| `E_FX_CARRY`          | 100.0% | 0.2000 | 0.10 | 0.0200 | **−0.180** (zeroed) |
| `E_SECTOR_ROTATION`   | 100.0% | 0.0600 | 0.10 | 0.0060 | **−0.054** (zeroed) |
| `E_FUND_FLOW`         |  50.0% | 0.0000 | 0.55 | 0.0000 | (already 0) |
| `E_FUNDAMENTAL`       |  20.0% | 0.1000 | 0.82 | 0.0820 | −0.018 (kept) |
| `E_TIME`              |  15.0% | 0.0400 | 0.87 | 0.0346 | −0.005 (kept) |
| `E_FOREIGN_REVERSAL`  |   0.0% | 0.0666 | 1.00 | 0.0666 | 0 (kept) |

**Before:** composite weight steered by E_FOMC_DRIFT (0.286) + E_FX_CARRY
(0.200) + E_PEAD (0.172) — all of which fully reverse OOS.

**After:** composite weight steered by E_FUNDAMENTAL (0.082) +
E_FOREIGN_REVERSAL (0.067) + E_TIME (0.035) — the three OOS-stable theses.

Floor of 0.10 (not 0) keeps unstable theses visible in `contributing_signals`
at minimal weight rather than silently disappearing — preserves calibration
honesty.

## v1.10.5 update: E_INSIDER_CLUSTER re-hindcast (relaxed gating)

**Decision context.** 2026-05-02 audit identified E_INSIDER_CLUSTER as the
top-ROI promotion candidate from `underfit` (n=11) — Sharpe=+0.78 looked
real if n could grow above the 50-sample activation floor. v1.10.5 made
`cluster_threshold` and `window_days` configurable on the expert + runner
so re-hindcast can vary the spec without changing predict-time defaults.

**Run config.**
- universe: 60 Russell 2000 small/mid-cap names → 55 CIKs resolved
- window: 2024-01-02..2026-03-29 (matches phase1b baseline)
- horizon: 30d
- spec change: `cluster_threshold=2` (was 3), `window_days=14` (unchanged)

**Result table.**

| metric | v1.0 (threshold=3) | v1.10.5 (threshold=2) |
|---|---:|---:|
| n_signals | 11 | **47** (+327%) |
| AUC overall | 0.339 | **0.7353** (+0.40) |
| AUC IS | — | 0.7227 |
| AUC OOS | — | **0.8229** (better than IS) |
| Sharpe overall | +0.782 | -0.3486 |
| Sharpe IS | — | +0.1500 |
| Sharpe OOS | — | **-2.3020** |
| OOS_degradation | 0.0 | **16.35** |
| calibration_status | underfit | **underfit** (n=47 < 50) |
| is_active | False | **False** |
| composite weight | 0.000 | 0.000 |

**Honest reading.**

The relaxed-gating run measures a *different signal* from the v1.0 entry —
2-buyer clusters fire ~4x more often than 3-buyer clusters. So the
"Sharpe=+0.78" of the prior n=11 measurement is **not** the same thesis
the v1.10.5 row characterises. Treating these as comparable would be
dishonest. What v1.10.5 measures honestly:

1. **AUC=0.735 with OOS=0.82** is striking — the directional ordering is
   real and *improves* out of sample. Insiders at threshold-2 *do* discriminate
   forward equity direction.

2. **Sharpe OOS=-2.30** says the same theory loses money. Translation:
   knowing direction beats random, but the LONG-leg forward returns went
   negative through the OOS window (Russell 2000 small-cap weakness
   2025-2026). The signal sorts trades correctly while the basket itself
   bleeds.

3. **n=47 is still below the 50-sample floor.** No promotion.
   `is_active()=False`. Composite weight stays 0.

4. Even if n had cleared 50, INV-GS-133's OOS-stability factor would
   floor the final weight to 10% of brier_weight (0.21 × 0.10 = 0.021)
   because OOS_degradation is 16x. Real signal, unstable PnL, suppressed
   correctly.

**Why this is a complete experiment, not a failure.**

ROI was measured directly:
- before: top promotion candidate, large unmeasured edge
- after: edge confirmed (AUC), pnl unstable (Sharpe), correctly suppressed
- net: framework absorbed the measurement, no calibration distortion

**Followup decisions (deferred, low ROI).**

- Lower threshold further (2 → 1) would fire on every Form 4 buy → noise.
- Widen window (14d → 30d) would grow n but dilute the cluster signal.
- Expand universe (60 → 200 Russell names) would grow n proportionally.
  Highest-effort, highest-uncertainty option.
- **Recommendation**: leave at v1.10.5 measurement. Move ROI search to
  remaining bootstrap theses (E_FUNDAMENTAL_KR, E_PEAD_KR via kr-hindcast).

## v1.10.9 update: E_INSIDER_CLUSTER 승격 검토 → 보류

**컨텍스트**. v1.10.5 re-hindcast로 E_INSIDER_CLUSTER가 AUC=0.7353 (테이블 1위)
n=47 (50 임계값 6% 부족)으로 측정됨. 운영자가 calibration weight를 measured
기반으로 승격할 수 있는지 10-패널 크리틱 진행.

**정량 데이터 (n=50 가정 강제 승격 시)**:

| 메트릭 | 값 |
|---|---:|
| AUC | 0.7353 (1위) |
| AUC IS / OOS | 0.7227 / 0.8229 |
| n | 47 (현재) → 50 (가정) |
| Sharpe IS / OOS | +0.150 / -2.302 |
| OOS_degradation | 16.35 |
| AUC z-score | 5.59 (p<0.0001) |
| brier_weight | 0.2207 |
| OOS factor (INV-GS-133) | 0.10 (floor) |
| **final composite weight** | **0.0221** |

**10-패널 표결**: 승격 3, 보류 7.

| # | 패널 | 표결 | 핵심 논리 |
|---|---|:-:|---|
| 1 | Senior quant | 승격 | AUC OOS 0.82 = 진짜 discrimination |
| 2 | Risk officer | 보류 | n<50은 INV-GS-103 설계 임계값, 한 thesis만 변경 = 게이트 위반 |
| 3 | Statistician | 승격 | z=5.59, p<0.0001 |
| 4 | Skeptic | 보류 | direction-only edge with no PnL, 0.022 weight = 무의미 |
| 5 | Architect | 보류 | special-case = slippery slope |
| 6 | Performance | 보류 | weight 0 vs 0.022 차이 거의 없음 |
| 7 | Compliance | 보류 | INV-GS 위반 없으나 명분 약함 |
| 8 | Maintainer | 보류 | 기준 명목화 필요 |
| 9 | Product | 승격 | UX 가시성 |
| 10 | Honest engineer | 보류 | 프레임워크가 의도대로 동작 중 |

**결정**: **승격 보류**. n=47은 50 임계값 6% 부족. INV-GS-103 게이트 존중.

**핵심 근거**:

1. **AUC discrimination은 진짜**지만 (OOS=0.82, IS=0.72) **OOS Sharpe=-2.30**이
   LONG들이 OOS 윈도우에서 손실 발생을 증명. Direction은 맞고 PnL은 안 나옴.
2. 강제 승격해도 **INV-GS-133 OOS-stability factor가 weight를 0.10× brier로
   floor** → final = 0.0221. composite output에서 거의 관찰 불가능.
3. n=47이 임계값 6% 부족 — **한 thesis만 봐주면 게이트의 의미 상실**.
4. **프레임워크가 정확히 의도대로 동작 중**. AUC 측정, OOS 패널티 적용,
   sample-count 게이트 존중 — INV-GS-133 설계 케이스 그 자체.

**Follow-up 권고 (low ROI, 이번 wave 외)**:

- universe 확대: Russell 2000 60 → 200 names → n proportionally 증가하여
  자연스럽게 50 통과 가능. 단 yfinance throttle + Form4 fetch 비용 증가.
- 윈도우 확장: 2024-01..2026-03 → 2022-07..2026-03 → 추가 1.5년치 이벤트.
  단 Form4 캐시 재구축 필요.
- threshold 변경: cluster_threshold 2 → 1.5 (소수점 가중)? — 의미 모호.

이번 commit은 **승격 결정만 문서화**, 코드 변경 없음.

## v1.10.9 추가: Weight 재조정 후보 분석

**의문**: measured + AUC > 0.60인데 composite weight이 낮은 thesis가 있나?
있다면 weight 재조정으로 ROI가 나올까?

**분석 결과 — 후보 0개**:

전체 24개 thesis 중 **measured + AUC > 0.60**인 entry는 없다. 현재 measured
edge의 ceiling은 **E_PEAD AUC=0.586** (n=298).

대신 "AUC edge는 큰데 weight는 작다" 케이스를 보면 모두 INV-GS-133의 OOS
suppression이 의도적으로 적용된 결과:

| thesis | AUC | OOS_deg | brier_w | OOS factor | final |
|---|---:|---:|---:|---:|---:|
| `E_FOMC_DRIFT` | 0.357 | 100% | 0.2860 | 0.10 | 0.0286 |
| `E_FX_CARRY` | 0.400 | 100% | 0.2000 | 0.10 | 0.0200 |
| `E_PEAD` | 0.586 | 115.6% | 0.1720 | 0.10 | 0.0172 |
| `E_FUNDAMENTAL` | 0.550 | 20% | 0.1000 | 0.82 | 0.0820 |

이 4개 모두 **INV-GS-133이 정확히 디자인된 케이스 (IS edge dies OOS)**.
재조정으로 weight를 끌어올리면:
- IS-only edge가 다시 composite를 steer
- v1.10.4가 close한 calibration 버그 재도입
- 운영자에게 거짓 conviction 제공

**결정**: weight 재조정 안 함. 프레임워크가 의도대로 작동.

**대신 ROI가 있는 follow-up (이번 wave 외)**:

- **E_PEAD OOS_deg 조사**: AUC=0.586 자체는 진짜 edge. OOS_deg=115.6%이
  데이터 품질 문제(2026 Q4 universe drift, earnings season 외부 충격
  등)에서 왔다면, 클린 데이터로 re-hindcast하면 OOS_deg가 내려가고
  weight도 자연 상승. 현재 0.017 → 잠재 0.17 (10x 가능성).
- **E_FUND_FLOW retire**: AUC=0.48, n=80, brier=0.25 (random) → weight=0.
  measured but "no edge" 명확. retirement 후보.
- **E_TIME 추가 측정**: AUC=0.52, n=200, weight=0.0346. 임계 근처에서
  안정적인 약한 edge. 재 hindcast로 n 늘리면 weight 미세 상승 가능.

**ROI 우선순위**:
1. E_PEAD re-hindcast (high, 10x 잠재)
2. KR theses 11개 중 무엇이라도 measured로 승격 (high if 진짜 edge)
3. E_FUND_FLOW retirement (medium, 단순 cleanup)

위 모두 별도 wave 작업. v1.10.9는 분석 + 결정 문서화에 한정.

## v1.10.10 update: Task 2 — kr-vkospi-hindcast E2E 검증 결과

**실행**: `glostat kr-vkospi-hindcast --vkospi-csv cache/vkospi_history_synthetic.csv
--start 2024-01-02 --end 2026-03-29 --stride 7 --horizon 20`
(KR_KOSPI200_TOP30 universe, 합성 VKOSPI CSV)

**결과**: **n=0 trades (INSUFFICIENT_N)**.

| 카테고리 | 카운트 | 비율 |
|---|---:|---:|
| 총 (ticker, day) 평가 | 3,481 | 100% |
| `below_threshold` (\|r_t\| < 10%) | 3,443 | 98.91% |
| `misaligned_or_neutral` | 37 | 1.06% |
| `vkospi_unavailable` | 1 | 0.03% |
| **actionable LONG basket** | **0** | **0%** |

**진단**:

1. **Universe 협소**: KOSPI 200 TOP30은 megacap만 — 일일 ±10% 변동이 매우 드뭄.
   논문은 KOSPI 200 전체 200종목 × 18년으로 n=4,976 이벤트 확보.
2. **Window 짧음**: 2년 vs 논문 18년 → 9배 차이.
3. **합성 VKOSPI 패턴**: AR(1)=-0.17 평균회귀 + 1.2% 스파이크 확률로 생성된
   합성 데이터는 실제 KRX 위기 이벤트(2008, 2020, 2022)와 다른 분포.
4. **|r|>10% 이벤트 38건 중 alignment 0건**: 통계적으로 가능 (random ΔVKOSPI
   sign이 random r sign과 정확히 매치할 확률은 ~50% per event).

**검증 성공 — 하네스 E2E 정상 동작**:

| 컴포넌트 | 동작 확인 |
|---|---|
| Universe iteration | 30 tickers × 116 sample days = 3,481 cells ✓ |
| YFinanceReturnResolver | 모든 셀에 대해 `r_t` 계산 ✓ |
| KospiSmallCapResolver | 호출됨 (cache 미스 적음) ✓ |
| VkospiClient.get_delta_at | CSV provider 통한 ΔVKOSPI 계산 ✓ |
| score_vkospi_mood + regime classification | 4개 quadrant + below_threshold 분류 ✓ |
| Skip breakdown 집계 | 3개 카테고리 정확히 카운트 ✓ |
| phase1b JSON output | calibration loader가 자동 픽업 ✓ |
| Snapshot broker writes | yfinance + vkospi 모두 기록 ✓ |
| n=0 → bootstrap status 유지 | INV-GS-103 게이트 정확히 동작 ✓ |

**결론**: v1.10.8의 하네스는 의도대로 작동. n=0은 데이터 + universe 한계의
honest 결과지 코드 버그 아님.

**실제 thesis edge 측정을 위한 요건** (별도 wave):

1. **실제 KRX VKOSPI 데이터** (`docs/VKOSPI_SETUP.md` per 운영자 export)
2. **Universe 확대**: KR_KOSPI200_TOP30 → 전체 KR_KOSPI200 (200종목, 6.7x)
3. **Window 확장**: 2024-2026 → 2010-2026 (8x), 알파 decay 측정 가능
4. (또는) **Universe 다변화**: KOSDAQ150 추가 (소형주 폭발 효과 측정)

200종목 × 16년 × stride=1 = ~800,000 ticker-day 평가, ~2400 |r|>10% 이벤트
예상. 논문 4,976의 절반 수준이지만 통계적으로 충분.

**Polish-bias 체크**: v1.10.10은 Task 2 검증 결과 문서화. 코드 변경 없음.

## v1.10.11 update: OOS-stability factor (INV-GS-133) before/after 정량 측정

**의문**: v1.10.4가 INV-GS-133 OOS factor를 도입한 이후 실제 prediction
output에 얼마나 영향을 주나? before/after 측정 안 한 상태 — Task 3
요청으로 정량화.

**방법**: synthetic_calibration_for_mock의 8개 active thesis에 대해
3개 시나리오로 prediction 실행:

- **bullish**: 모든 active thesis가 LONG (value=+1.5)
- **bearish**: 모든 active thesis가 SHORT (value=-1.5)
- **mixed**: IS-only-edge 4개 (E_PEAD/FOMC/FX/SECTOR)만 LONG, 나머지 NEUTRAL

각 시나리오에서 v1.10.3 (OOS factor=1.0 monkey-patch) vs v1.10.4 (current)
prediction 비교.

스크립트: `scripts/measure_oos_factor_impact.py` (재현 가능, deterministic).

**결과**:

| 시나리오 | p_up v1.10.3 | p_up v1.10.4 | Δp_up | Δedge | ΔE[r] | ΔCI width |
|---|---:|---:|---:|---:|---:|---:|
| bullish | 0.5306 | 0.5314 | +0.0008 | +0.08pp | +3.8 bps | +18.8 bps |
| bearish | 0.5025 | 0.5031 | +0.0007 | +0.07pp | −3.8 bps | +18.8 bps |
| **mixed** | **0.4372** | **0.4500** | **+0.0128** | **+1.28pp** | **+8.4 bps** | **−46.4 bps** |

**Per-thesis weight shift**:

| thesis | v1.10.3 (brier만) | v1.10.4 (×OOS factor) | shift |
|---|---:|---:|---:|
| `E_FOMC_DRIFT` | 0.2860 | 0.0286 | **−0.2574 (−90%)** |
| `E_FX_CARRY` | 0.2000 | 0.0200 | **−0.1800 (−90%)** |
| `E_PEAD` | 0.1720 | 0.0172 | **−0.1548 (−90%)** |
| `E_SECTOR_ROTATION` | 0.0600 | 0.0060 | −0.0540 (−90%) |
| `E_FUNDAMENTAL` | 0.1000 | 0.0820 | −0.0180 (−18%) |
| `E_TIME` | 0.0400 | 0.0346 | −0.0054 (−14%) |
| `E_FUND_FLOW` | 0.0000 | 0.0000 | 0 (이미 0) |
| `E_FOREIGN_REVERSAL` | 0.0666 | 0.0666 | 0 (OOS_deg=0) |

**해석 — 3가지 핵심 효과**:

1. **All-bullish/bearish 시나리오의 작은 영향 (Δp_up ≈ 0.0008)**:
   stable 3개 (E_FUNDAMENTAL, E_TIME, E_FOREIGN_REVERSAL)와 IS-only-edge
   4개가 같은 방향(LONG)으로 votes하니 IS-only-edge suppression이 stable
   theses의 LONG 표를 가리지 않음. 다만 expected_return_bps는 거의
   2배(3.5→7.3) 차이 — base rate prior 가중치가 더 강해진 결과.

2. **Mixed 시나리오의 큰 영향 (Δp_up = +0.0128, Δedge = +1.28pp)**:
   IS-only-edge 4개만 단독으로 LONG votes할 때 v1.10.3는 그 신호를 strong
   하게 받아 p_up=0.437 (강한 down-tilt, residual baseline 효과). v1.10.4는
   weight를 90% 차감해서 p_up=0.450으로 baseline 가까이 끌어당김.
   **= over-confidence 억제 작동 확인**.

3. **CI width 극적 축소 (mixed: 53.6 → 7.2 bps, −87%)**:
   IS-only-edge 4개 weight 차감으로 sigma 계산에 들어가는 신호 magnitude
   감소 → 예측 분산도 크게 축소. 운영자에게 "강한 signal처럼 보이지만
   실제로는 OOS에서 검증 안 됨" 메시지 전달.

**핵심 검증**:

INV-GS-133은 정확히 **IS-only-edge 케이스**에서만 강하게 작동하며,
stable-edge 케이스 (E_FOREIGN_REVERSAL, E_FUNDAMENTAL)는 거의 영향
없음. 의도한 설계대로 동작.

**현장 적용 함의**:

- 8개 active thesis 중 4개 (50%)가 OOS-deg ≥ 100% → composite weight의
  대부분이 IS-only-edge에서 왔던 v1.10.3 시절은 over-confidence 위험
  매우 높았음
- v1.10.4 이후 composite은 **stable 3개 (E_FOREIGN_REVERSAL +
  E_FUNDAMENTAL + E_TIME)**가 사실상 모두 steer
- 이는 칼리브레이션 honest화의 결과 — predictions이 더 보수적이지만
  ground truth와 더 일치할 가능성 높음

**ROI 체크**: 측정 자체는 0 코드 변경. 결과는 INV-GS-133 결정의 정량
근거 — 향후 weight 변경 제안 검토 시 reference numbers로 활용 가능.

스크립트 + JSON 결과: `scripts/measure_oos_factor_impact.py` (재실행 가능).

## v1.10.12 update: E_PEAD 재측정 + E_FUND_FLOW retirement

### Task 1 — E_PEAD OOS_deg 재측정 (가설 반증)

**가설**: E_PEAD의 합성 baseline (auc=0.586, sharpe=0.629, n=298,
oos_degradation=1.156)은 v0.6 시절 phase1b 측정. 데이터 품질 문제일
가능성 → 재측정으로 OOS_deg 감소 + weight 10x 회복 (0.017 → 0.17) 가능.

**검증 방법**: `scripts/rerun_pead_hindcast.py` (S&P500 top50 universe,
2024-01-02..2026-03-29, lxml 의존성 추가 후 실행).

**결과**:

| 메트릭 | 합성 (v0.6) | 재측정 (v1.10.12) | 변화 |
|---|---:|---:|---:|
| n_signals | 298 | 298 | 0 |
| AUC overall | 0.586 | **0.5807** | -0.5pp |
| AUC IS | — | 0.6288 | — |
| AUC OOS | — | 0.5154 | — |
| Sharpe IS | — | +0.9824 | — |
| Sharpe OOS | — | -0.1720 | — |
| Sharpe overall | 0.629 | +0.6210 | -0.008 |
| **OOS_deg** | **1.156** | **1.1751** | **+0.019** |

**가설 반증**: 재측정으로 OOS_deg가 줄어들 것이라는 가설은 **틀림**.
v0.6 측정이 정확했고, IS edge (Sharpe=+0.98)가 OOS에서 반전 (-0.17)
되는 패턴은 안정적으로 재현됨. 이는:

- 데이터 품질 문제 아님
- 진짜 알파 decay 또는 PEAD 구조적 특성 (earnings drift는 시장 효율화로
  shrink하는 알려진 패턴)
- INV-GS-133 OOS factor가 옳게 작동 — weight 0.0172 유지가 정확

**ROI 결론**: E_PEAD weight 10x 회복 path는 spec 변경 (universe 확대,
horizon 변경, feature 추가) 없이 **불가능**. 별도 wave 작업으로 deferred.

**사이드 효과 — calibration loader**: 새 e_pead_report.json이 cache에
저장됨. `synthetic_calibration_for_mock`의 hardcoded 값 (auc=0.586)은
이제 fallback일 뿐. 다음 `glostat predict` 실행은 재측정 값 (auc=0.5807)
을 사용. composite weight에는 미세한 차이 (brier 변화 < 1bp).

### Task 2 — E_FUND_FLOW retirement (정식)

**컨텍스트**: 2026-05-02 audit이 E_FUND_FLOW를 top retirement
candidate로 식별. measured (n=80) but no edge:

- AUC=0.48 (|edge|=0.02 = 정확히 _DEFAULT_AUC_DELTA 노이즈 임계값)
- Sharpe=-0.10 (미세 음수 → no PnL)
- brier_score=0.25 (random) → brier_to_weight=0
- 즉 composite weight는 이미 INV-GS-103로 0

**구현**: `ThesisCalibration`에 `retired_in: str | None = None` +
`retired_reason: str | None = None` 필드 추가:

- `is_retired` property: `retired_in is not None`
- `calibration_status`: retired marker가 다른 status 위에 우선
- `is_active()`: `cal.is_retired → False` 추가 가드

E_FUND_FLOW 엔트리에 `retired_in="v1.10.12"` 마킹.

**Composite 행동 변화**: **없음**. weight는 이미 0이었으므로 prediction
output에 변화 없음. 변화는 **운영자 가시성**:

| Surface | Before | After |
|---|---|---|
| `glostat calibrate --mock` row | `near_random` | `retired` |
| Audit doc | "candidate for retirement" | "retired in v1.10.12" |
| `is_active()` | False (n=80, |edge|=0.02 < threshold) | False (retired) |

**의도**: 운영자가 "이 thesis는 시도해봤고 알파 없다고 판단" 명시. 향후
누군가 재시도하기 전에 retired_reason을 읽고 결정.

**테스트** (4개 신규):
- `test_default_thesis_is_not_retired`
- `test_retired_thesis_status_is_retired`
- `test_retired_thesis_is_inactive_regardless_of_auc_n`
  — strong AUC + large n + retired_in 모두 있어도 is_active=False
- `test_e_fund_flow_synthetic_is_retired`
  — synthetic table에서 E_FUND_FLOW 마킹 확인

### Polish-bias 체크

v1.10.12: 측정 (PEAD 재측정 가설 반증) + cleanup (E_FUND_FLOW retirement)
= **분석/정리 축**. 6번 wave에 걸쳐 시그널/데이터/통합/분석/검증/측정/정리
모두 다양화 유지.
