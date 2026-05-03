# Thesis Landscape — 2026-05-03 (post v1.10.15)

GLOSTAT calibration table 전체 25개 thesis를 6개 status 그룹으로 분류한
운영자 reference. 각 그룹의 의미와 composite weight 기여도 포함.

생성: `glostat predict` calibration loader가 cache/hindcast/+ phase1b/
디렉터리의 모든 report.json을 픽업한 시점. 합성 baseline (synthetic_
calibration_for_mock)에서 시작 → cache report 우선 적용.

---

## Summary 매트릭스

| 그룹 | 카운트 | composite weight 합 | 의미 |
|---|---:|---:|---|
| **measured-active** | 2 | 0.117 | 진짜 stable edge — 실제 prediction 주도 |
| **measured-suppressed** | 6 | 0.063 | IS edge 있지만 OOS 불안정 (INV-GS-133 floor) |
| **measured-near_random** | 5 | 0.000 | 측정됐지만 \|edge\| < 노이즈 임계값 |
| **underfit** | 2 | 0.000 | n < 50 (samples 부족) |
| **bootstrap** | 7 | 0.000 | hindcast 미실행 (외부 의존성 차단) |
| **retired** | 3 | 0.000 | measured no edge → 운영자 retirement 결정 |
| **TOTAL** | **25** | **0.180** | composite output 결정자: 8개 (2 stable + 6 floor) |

---

## measured-active (2) — composite의 사실상 결정자

진짜 stable edge. OOS_degradation이 적어 INV-GS-133 floor 적용 안 됨.

| thesis | AUC | n | Sharpe | OOS_deg | OOS factor | weight |
|---|---:|---:|---:|---:|---:|---:|
| `E_FUNDAMENTAL` | 0.550 | 120 | +0.40 | 20% | 0.82 | **0.082** |
| `E_TIME` | 0.520 | 200 | +0.30 | 15% | 0.86 | **0.035** |

이 두 thesis가 **composite output을 사실상 주도**. 다른 모든 active
thesis 합 (0.063)보다 큼.

---

## measured-suppressed (6) — IS-only-edge with INV-GS-133 floor

AUC discrimination은 있지만 OOS_deg ≥ 100% (IS edge가 OOS에서 무너짐)
→ INV-GS-133 OOS factor 0.10× 적용 → 사실상 floor weight.

| thesis | AUC | n | Sharpe | OOS_deg | brier_w | weight | direction |
|---|---:|---:|---:|---:|---:|---:|:---:|
| `E_FOMC_DRIFT` | 0.357 | 135 | -1.34 | 100% | 0.286 | 0.029 | flip (-1) |
| `E_FX_CARRY` | 0.400 | 135 | -1.53 | 100% | 0.200 | 0.020 | flip (-1) |
| `E_PEAD` | 0.581 | 298 | +0.62 | 117% | 0.162 | 0.016 | (+1) |
| `E_COMMODITY_INDEX_KR` | 0.539 | 234 | -0.84 | 100% | 0.079 | 0.008 | (+1) |
| `E_SECTOR_ROTATION` | 0.470 | 174 | -0.48 | 100% | 0.060 | 0.006 | flip (-1) |
| `E_PEAD_KR` | 0.478 | 1860 | -0.05 | 100% | 0.043 | 0.004 | flip (-1) |

**합계 weight 0.063** — stable-2 (0.117)의 절반 수준. composite output에
미세한 보조 역할.

---

## measured-near_random (5) — n 충분, edge 부재

n ≥ 50으로 측정 신뢰도는 있지만 \|AUC - 0.5\| < 0.02 노이즈 임계값.
weight = 0 (is_active False).

| thesis | AUC | n | Sharpe | 비고 |
|---|---:|---:|---:|---|
| `E_FUNDAMENTAL_KR` | 0.503 | 3042 | +0.42 | 표본 가장 큼, edge 0.003 |
| `E_TIME_KR` | 0.486 | 3510 | +0.51 | edge 0.014 (KR megacap) |
| `E_FOREIGN_REVERSAL` | 0.494 | 127 | +1.30 | **v1.10.15 강등** (broader→KR specific) |
| `E_FOREIGN_REVERSAL_KR` | 0.494 | 127 | +1.30 | calibration loader 별도 entry |
| `E_FUNDAMENTAL_KR_CYCLICAL` | 0.500 | 936 | 0.00 | universe gate 모두 fail로 random |

**Retirement 후보**: E_FUNDAMENTAL_KR_CYCLICAL (n=936이지만 perfectly
random — universe gate가 너무 좁음 → spec 변경 또는 retirement 검토).

---

## underfit (2) — n < 50 임계값

| thesis | AUC | n | Sharpe | 회복 path |
|---|---:|---:|---:|---|
| `E_INSIDER_CLUSTER` | 0.735 | 47 | -0.35 | universe 60→200 확장 (Russell2k) |
| `E_REGIME_US` | 0.396 | 45 | -0.38 | stride 7→3 (130 events 가능) |

E_INSIDER_CLUSTER는 v1.10.5에서 10-패널 결정으로 게이트 존중 (보류).
E_REGIME_US는 stride 단축으로 n 확대 가능.

---

## bootstrap (7) — hindcast 미실행

| thesis | 차단 사유 | 구축 ROI |
|---|---|---|
| `E_INSIDER_KR` | DART API key 필요 | 중간 (KR insider 패턴) |
| `E_INSIDER_VELOCITY_KR` | DART API key (kr-hindcast wired) | 중간 |
| `E_MACRO_KR` | ECOS API key + 새 하네스 | 낮음 (macro overlay) |
| `E_INTRADAY_FLOW_KR` | 새 하네스 (Naver+KIS) | 낮음 (intraday) |
| `E_SHORT_SELLING_KR` | 새 하네스 (KRX 공매도) | 중간 |
| `E_ANALYST_REVISION` | 새 하네스 (yfinance recommendations) | **높음 (외부 key 불필요)** |
| `E_VKOSPI_MOOD_KR` | universe 확대 필요 (현재 n=0) | 높음 (academic 검증) |

**가장 빠른 ROI = E_ANALYST_REVISION**: API key 불필요, yfinance
Ticker.upgrades_downgrades로 직접 측정 가능.

---

## retired (3) — measured no edge

| thesis | retired_in | 이유 |
|---|---|---|
| `E_FUND_FLOW` | v1.10.12 | n=80, AUC=0.48 = noise floor, Sharpe=-0.10 |
| `E_COMMODITY_TS` | v1.10.13 | n=517, \|edge\|=0.011 sub-threshold |
| `E_FUNDING_CARRY` | v1.10.13 | n=2921 largest, \|edge\|=0.005, OOS_deg=457% |

이미 weight=0이었으므로 prediction output 영향 0. 운영자 가시성 cleanup.

---

## 핵심 통찰

### 1. composite output의 실질적 결정자는 2개

24개 active thesis 중 stable-2 (E_FUNDAMENTAL + E_TIME) weight 합이
나머지 6개의 약 2배. **composite은 사실상 stable-2가 결정**.

### 2. KR thesis 6개 모두 measured no edge

KR-specific phase_kr 측정 결과:
- n=3042, 3510 (큰 표본) → edge 0.003, 0.014 (사실상 random)
- E_PEAD_KR, E_COMMODITY_INDEX_KR만 measured-active이지만 OOS-floored

KR megacap universe (TOP30)는 너무 narrow로 KR thesis들의 진짜 edge
잘 안 잡힘. **universe 확장 (200종목) + window 확장 (16년)이 다음
ROI 후보**.

### 3. INV-GS-133 OOS factor의 정량 임팩트

6개 measured-suppressed thesis가 IS edge=0.286 → OOS-floor=0.029 (90%
suppression). 만약 INV-GS-133 없었으면 composite weight 합:
- 현재: 0.180
- v1.10.3 가설 (no OOS factor): 0.117 + 0.286 + 0.200 + 0.162 + 0.079 +
  0.060 + 0.043 = 0.947 (5.3x)

→ INV-GS-133가 over-confidence를 80% 억제 중. 정확하게 의도대로 동작.

### 4. Retirement는 0 numerical impact

3개 retired (FUND_FLOW + COMMODITY_TS + FUNDING_CARRY) 모두 weight 이미
0이었음. 운영자 가시성 정리만, predict output 변화 0 (v1.10.13에서 입증).

---

## 다음 ROI 후보 (priority order)

1. **E_VKOSPI_MOOD_KR universe 확장** (TOP30 → 200, 67배 trigger)
   — academic 검증 thesis, 진짜 alpha 측정 가능
2. **E_ANALYST_REVISION 하네스** — yfinance만 사용, 외부 key 불필요
3. **E_INSIDER_CLUSTER universe 확장** (Russell2k 60 → 200) — n 확장으로
   underfit 탈출
4. **stable-2 (E_FUNDAMENTAL + E_TIME) 분기 재측정** — composite
   의 실질적 결정자, 안정성 검증 필수
5. **E_FUNDAMENTAL_KR_CYCLICAL retirement 또는 spec 변경** — n=936이지만
   perfectly random, universe gate 너무 좁음
