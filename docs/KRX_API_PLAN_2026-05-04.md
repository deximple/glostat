# KRX OpenAPI 활용 플랜 — v1.10.18

작성일: 2026-05-04. KRX OpenAPI 키 (`GLOSTAT_KRX_API_KEY`) 추가 시점.

## 0. 키 검증 상태

`probe` 결과 (5개 endpoint 시도):
- `data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd` → **401 Unauthorized**
  → 키는 존재하지만 endpoint 권한 미신청
- `idx/vkospi`, `srt/sbd_stk` 등 가설적 경로 → **404 Not Found**
  → 경로 자체가 다름

**실용 가능성**:
- 키 형식 자체는 valid
- KRX OpenAPI는 endpoint별 별도 사용 신청 + 일별 quota 관리
- 운영자가 https://openapi.krx.co.kr 에서 사용할 endpoint 활성화 필요

---

## 1. KRX OpenAPI catalog (공식)

KRX 정보데이터시스템에서 제공하는 OpenAPI 주요 카테고리:

| 카테고리 | 설명 | GLOSTAT 활용도 |
|---|---|---:|
| **STK (주식)** | 종목 일별 OHLCV, 외국인 보유율, 거래원 | ★★★ |
| **IDX (지수)** | KOSPI, KOSPI200, KOSDAQ, **VKOSPI** 등 일별 시계열 | **★★★★★** |
| **SRT (공매도)** | 공매도 잔고, 거래량, 종목별 | ★★★ |
| **DRV (파생)** | 선물/옵션 일별, 미결제약정 | ★★ |
| **ETF/ETN** | ETF/ETN 일별, NAV | ★ |
| **BND (채권)** | 채권 시세 | ☆ |
| **GEN (일반)** | 종목 기본정보, 이벤트 | ★★ |

---

## 2. GLOSTAT 현재 gap (calibration audit 2026-05-02 기반)

### Critical gap — 합성 데이터 한계

| Thesis | 현재 상태 | 차단 사유 | KRX 해결 가능? |
|---|---|---|:-:|
| `E_VKOSPI_MOOD_KR` | bootstrap (n=3 univ200) | synthetic VKOSPI mean reversion만 | **YES (IDX/vol_idx)** |
| `E_SHORT_SELLING_KR` | bootstrap | krx_short_client는 session-cookie 의존 | **YES (SRT)** |
| `E_FOREIGN_REVERSAL_KR` | near_random | Naver scrape 데이터 한계 | **YES (STK/외국인)** |
| `E_INTRADAY_FLOW_KR` | bootstrap | Naver+KIS 하네스 없음 | 부분 (STK) |

### Stable but narrow

| Thesis | 현재 status | KRX로 어떻게 강화? |
|---|---|---|
| `E_FUNDAMENTAL` (US) | active 0.082 weight | 무관 (US thesis) |
| `E_TIME` (US) | active 0.035 weight | 무관 |
| `E_FUNDAMENTAL_KR` | near_random n=3042 | KRX 거래원 데이터로 spec 변경 가능 |
| `E_TIME_KR` | near_random n=3510 | KOSPI200 정확한 OHLCV로 보강 |

### 신규 thesis 가능성

| 신규 thesis | 데이터 source | 학술 prior |
|---|---|---|
| `E_PCR_KR` (Put/Call ratio) | DRV 옵션 미결제약정 | Whaley fear/greed |
| `E_PROGRAM_KR` (프로그램매매) | STK 프로그램매매 | KRX official |
| `E_BASIS_KR` (현물-선물 베이시스) | DRV 선물 + IDX KOSPI200 | arbitrage pressure |
| `E_OWNER_FOREIGN_KR` (외국인 보유 변화) | STK 외국인 보유 | 수급 thesis |

---

## 3. 10-패널 크리틱

각 패널이 5개 후보에 대해 priority 표결:
- **A**: VKOSPI 실측 (E_VKOSPI_MOOD_KR 활성화)
- **B**: 공매도 잔고 (E_SHORT_SELLING_KR 활성화)
- **C**: 외국인 매매 (E_FOREIGN_REVERSAL_KR 정확도)
- **D**: 신규 thesis E_PCR_KR (옵션 P/C ratio)
- **E**: KRX 정확한 OHLCV로 KR thesis 4개 재측정

| # | 패널 | 1순위 | 2순위 | 핵심 논리 |
|---|---|:-:|:-:|---|
| 1 | Senior quant | A | D | VKOSPI 실측이 v1.10.10/17 binding constraint 해소. P/C는 학술 강한 prior |
| 2 | Risk officer | A | B | bootstrap thesis 해결 우선, 데이터 출처 신뢰도 (KRX 공식) |
| 3 | Statistician | A | E | n 확장보다 데이터 quality 향상이 calibration 개선 |
| 4 | Skeptic | E | A | 신규 thesis 추가하기 전에 기존 thesis 데이터 정확도 먼저 |
| 5 | Architect | A | C | VKOSPI client는 pluggable provider 이미 wired, KRX backend 1개 추가 |
| 6 | Performance | A | B | endpoint quota 적게 쓰는 일별 시계열 우선 |
| 7 | Compliance | E | A | KRX 공식 데이터 = data lineage 가장 깨끗 (INV-GS-022 강화) |
| 8 | Maintainer | A | C | 새 client 1개 추가가 4개 추가보다 유지비 낮음 |
| 9 | Product | A | D | "VKOSPI 실측 measured" = 운영자에게 큰 가치, P/C ratio도 운영자 관심 큼 |
| 10 | Honest engineer | A | E | binding constraint 해소가 진짜 ROI, 나머지는 cleanup |

**1순위 표결**: A(VKOSPI) 8표, E(KR thesis 재측정) 2표 → **A 압도적 우승**
**2순위 표결**: D(P/C) 2, B(공매도) 2, C(외국인) 2, E(재측정) 2, A(이미1순위) — 분산

**최종 결정**:
- **Phase 1 (즉시)**: A — KRX VKOSPI client 구축 + E_VKOSPI_MOOD_KR 실측
- **Phase 2 (Phase 1 완료 후)**: B + C — 기존 thesis 데이터 보강 (E_SHORT_SELLING_KR + E_FOREIGN_REVERSAL_KR)
- **Phase 3 (장기)**: D — 신규 thesis E_PCR_KR

---

## 4. 적용 플랜

### Phase 1 — KRX VKOSPI 실측 (v1.10.18)

**목표**: E_VKOSPI_MOOD_KR을 bootstrap (n=3) → measured로 승격

**Step 1.1: 운영자 action — endpoint 활성화**
- https://openapi.krx.co.kr 로그인
- "변동성지수 일별 시계열" (likely `idx/vol_idx_bydd_trd`) endpoint 사용 신청
- Daily quota 확인 (보통 무료 1,000~10,000건/일)

**Step 1.2: KrxOpenApiClient 구축** (`src/glostat/data/krx_openapi_client.py`)
- `httpx.AsyncClient` + `AUTH_KEY` header
- Retry + throttle (5 req/sec)
- Snapshot broker 통합 (INV-GS-022)
- 핵심 method: `get_vkospi_daily(start, end) -> tuple[VkospiBar, ...]`

**Step 1.3: vkospi_client 신규 backend**
- `vkospi_client.set_history_provider()`에 KrxOpenApiClient backend 주입
- 기존 CSV/synthetic provider도 fallback 유지

**Step 1.4: 실측 hindcast 재실행**
- `glostat kr-vkospi-hindcast --vkospi-source krx --universe KR_KOSPI200`
- 위기 spike 포함된 진짜 데이터로 alignment 자연 발생 → 100+ trades 예상

**Step 1.5: calibration loader 자동 픽업 + audit doc**

**예상 효과**:
- E_VKOSPI_MOOD_KR: bootstrap → measured (active set 9 → 10)
- 만약 OOS_deg 양호하면 stable-3 또는 stable-suppressed 그룹

### Phase 2 — 기존 KR thesis 데이터 보강 (v1.10.19+)

**Step 2.1: KRX 공매도 backend** (E_SHORT_SELLING_KR)
- 기존 `krx_short_client.py` (session-cookie scrape) → KRX OpenAPI로 교체
- 새 endpoint: `srt/sbd_stk`, `srt/sht_sell_iv_trd`
- Hindcast 가능성 열림

**Step 2.2: 외국인 매매 backend** (E_FOREIGN_REVERSAL_KR)
- 기존 Naver scrape 부분 KRX OpenAPI로 보강
- 새 endpoint: `sto/inv_trd_brkr` (거래원), `sto/inv_trd_invtr` (투자자별)
- 데이터 정확도 향상으로 v1.10.14 강등 (n=127, AUC 0.49) 회복 가능성 검토

### Phase 3 — 신규 thesis (v1.10.20+, 장기)

**Step 3.1: E_PCR_KR (Put/Call ratio)**
- KRX OpenAPI `drv/opt_dd_trd` (옵션 일별)
- KOSPI200 P/C ratio 시계열 → 극단값 contrarian thesis
- 학술: Whaley fear/greed, KRX 2009 연구

**Step 3.2: E_PROGRAM_KR (프로그램매매 잔고)**
- KRX OpenAPI `sto/prog_trd`
- 차익거래 잔고 = 시장 stress proxy

---

## 5. 즉시 action items

1. **운영자**: https://openapi.krx.co.kr 에서 `idx/vol_idx_bydd_trd` 등록
2. **개발**: `src/glostat/data/krx_openapi_client.py` skeleton 작성 (Phase 1.2)
3. **테스트**: 등록 후 키 권한 재확인 (401 해소)

## 6. ROI 예상

| Phase | 작업량 | 예상 ROI |
|---|---|---|
| Phase 1 (VKOSPI) | client 200줄 + 실측 hindcast | **★★★★★** binding constraint 해소 |
| Phase 2 (공매도+외국인) | 기존 client 2개 KRX backend 추가 | ★★★ |
| Phase 3 (신규 thesis) | 새 expert 2개 + hindcast | ★★ (학술 검증 필요) |

## 7. Polish-bias 체크

이 문서는 plan 작성 (action 축). 코드 변경 없음. v1.10.6 이래 14번째 wave.
실제 thesis 추가는 별도 wave.

## 8. 캡스 (caps) 체크

- INV-GS-036 (free-stack): KRX OpenAPI는 무료 quota 내에서 free, 그 외는 paid → quota 모니터링 필요
- INV-GS-022 (snapshot broker): 모든 KRX response는 snapshot 저장
- INV-GS-101: 신규 thesis도 BUY/SELL 출력 안 함 (probability + evidence)
