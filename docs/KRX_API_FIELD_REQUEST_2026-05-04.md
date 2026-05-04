# KRX OpenAPI 필드 신청 리스트 — v1.10.18 Phase 1-3

**참조**: <https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES001_S1.cmd>

KRX OpenAPI는 endpoint별 사용신청 필요. 본 문서는 GLOSTAT 활용 우선순위
(v1.10.18 plan 기반) + 운영자 신청 가이드.

---

## 우선순위별 신청 리스트

### Tier 1 — Critical (즉시 신청, Phase 1)

| # | API 명 (한글) | endpoint | 카테고리 | 활용 | 일별 호출 |
|---|---|---|---|---|---:|
| 1 | **변동성지수 일별매매정보** | `idx/vol_idx_bydd_trd` | IDX | **E_VKOSPI_MOOD_KR binding constraint 해소** | ~30 |

**Tier 1만으로도** v1.10.10/17의 binding constraint (synthetic VKOSPI) 해소
→ E_VKOSPI_MOOD_KR bootstrap → measured 승격 가능.

---

### Tier 2 — 기존 thesis 데이터 보강 (Phase 2)

| # | API 명 | endpoint | 카테고리 | 활용 | 일별 호출 |
|---|---|---|---|---|---:|
| 2 | **공매도 일별매매정보** (종목별) | `srt/sht_sell_bydd_trd` | SRT | E_SHORT_SELLING_KR bootstrap 해소 | ~50 |
| 3 | **공매도 잔고 일별** (종목별) | `srt/sbd_stk` | SRT | E_SHORT_SELLING_KR (잔고 + 거래량 조합) | ~50 |
| 4 | **투자자별 거래실적** (일별) | `sto/inv_trd_invtr` | STK | E_FOREIGN_REVERSAL_KR Naver 대체 | ~30 |

**Tier 2 효과**:
- E_SHORT_SELLING_KR: bootstrap → measured (active 9 → 10)
- E_FOREIGN_REVERSAL_KR: Naver 데이터 정확도 향상 → v1.10.14 강등 (n=127, AUC 0.49)
  회복 가능성

---

### Tier 3 — 기본 인프라 (모든 hindcast 가속)

| # | API 명 | endpoint | 카테고리 | 활용 | 일별 호출 |
|---|---|---|---|---|---:|
| 5 | **유가증권 일별매매정보** (KOSPI) | `sto/stk_bydd_trd` | STK | yfinance OHLCV 대체, KR 정확도 향상 | ~200 |
| 6 | **코스피 시리즈 일별매매정보** | `idx/kospi_dd_trd` | IDX | KOSPI200 지수 정확 측정 | ~10 |

**Tier 3 효과**:
- 모든 KR thesis hindcast가 yfinance throttle 8 req/sec 제약에서 해방
- KRX 직접 데이터 = data lineage 가장 깨끗 (INV-GS-022 강화)
- 향후 v1.10.14 KOSPI200 hindcast 4시간 → 1시간 미만 단축 가능

---

### Tier 4 — 신규 thesis (Phase 3, 장기)

| # | API 명 | endpoint | 카테고리 | 활용 | 일별 호출 |
|---|---|---|---|---|---:|
| 7 | **옵션 일별매매정보** | `drv/opt_dd_trd` | DRV | E_PCR_KR (Put/Call ratio) 신규 thesis | ~30 |
| 8 | **선물 일별매매정보** | `drv/fut_dd_trd` | DRV | E_BASIS_KR (현물-선물 베이시스) 신규 | ~10 |

**Tier 4 효과**:
- 학술 검증된 새 thesis 2개 추가 가능
- 단 학술적 prior가 약한 KR 시장 특성 검증 필요 (별도 hindcast)

---

## 일별 호출 총량 (모든 tier 신청 시)

| Tier | endpoint 수 | 일별 호출 합 |
|---|---:|---:|
| Tier 1 | 1 | 30 |
| Tier 2 | 3 | 130 |
| Tier 3 | 2 | 210 |
| Tier 4 | 2 | 40 |
| **TOTAL** | **8** | **410** |

KRX OpenAPI 무료 quota는 endpoint별 1,000~10,000 호출/일 수준 (정확한
quota는 신청 후 마이페이지에서 확인). Tier 1-3 합 (370 호출/일)은 충분히
무료 한도 내.

---

## 운영자 action — 신청 절차

1. https://openapi.krx.co.kr 로그인 (이미 가입)
2. "사용신청" 메뉴 → 위 endpoint 명 검색
3. **Tier 1 (1개) 우선 신청** → 1-2영업일 승인 대기
4. 승인 후 키 권한 확인:
   ```bash
   curl -H "AUTH_KEY: $GLOSTAT_KRX_API_KEY" \
        "https://data-dbg.krx.co.kr/svc/apis/idx/vol_idx_bydd_trd?basDd=20260301"
   ```
   401 → 200 전환 확인 시 Phase 1 개발 착수
5. Tier 2 (3개) 추가 신청
6. Tier 3 (2개) 추가 신청 (인프라)
7. Tier 4 (2개) 마지막 신청 (장기 plan)

---

## 신청 우선순위 요약

**즉시 (1순위)**:
1. `idx/vol_idx_bydd_trd` — VKOSPI

**Phase 1 완료 후 (2순위, ~1주)**:
2. `srt/sht_sell_bydd_trd` — 공매도 일별
3. `srt/sbd_stk` — 공매도 잔고
4. `sto/inv_trd_invtr` — 투자자별 거래

**인프라 (3순위, ~2주)**:
5. `sto/stk_bydd_trd` — 주식 일별
6. `idx/kospi_dd_trd` — 코스피 일별

**장기 (4순위, ~1개월)**:
7. `drv/opt_dd_trd` — 옵션 일별
8. `drv/fut_dd_trd` — 선물 일별

---

## 신청 후 검증 코드 (Tier 1 기준)

```python
import httpx, asyncio, os

async def verify_vkospi():
    url = "https://data-dbg.krx.co.kr/svc/apis/idx/vol_idx_bydd_trd"
    headers = {"AUTH_KEY": os.environ["GLOSTAT_KRX_API_KEY"]}
    params = {"basDd": "20260301"}
    async with httpx.AsyncClient(timeout=20.0) as c:
        r = await c.get(url, headers=headers, params=params)
        print(f"status={r.status_code}")
        if r.status_code == 200:
            d = r.json()
            print(f"keys: {list(d.keys())}")
            rows = d.get("OutBlock_1", []) or d.get("output", [])
            print(f"rows: {len(rows)}, first: {rows[0] if rows else None}")
        else:
            print(f"body: {r.text[:200]}")

asyncio.run(verify_vkospi())
```

200 응답 + VKOSPI row 확인 → Phase 1.2 (`KrxOpenApiClient` 구축) 착수.

---

## ROI 정량

| Tier 신청 후 활성화 thesis | 현재 weight | 예상 weight | 변화 |
|---|---:|---:|---|
| Tier 1 → E_VKOSPI_MOOD_KR | 0.000 (bootstrap) | 0.005-0.080 | bootstrap → measured |
| Tier 2 → E_SHORT_SELLING_KR | 0.000 (bootstrap) | 0.005-0.030 | bootstrap → measured |
| Tier 2 → E_FOREIGN_REVERSAL_KR 회복 | 0.000 (near_random) | 0.030-0.060 (가설) | 강등 회복 가능성 |
| Tier 3 → 모든 KR thesis OHLCV 정확도 | (현재 yfinance) | 미세 개선 | INV-GS-022 lineage 강화 |
| Tier 4 → E_PCR_KR / E_BASIS_KR | (없음) | 0.005-0.030 신규 | 신규 thesis 2개 |

**Tier 1+2만으로도 active 9 → 11+ 가능**. composite stable weight 비중
(56.1% → 65%+) 추가 향상.
