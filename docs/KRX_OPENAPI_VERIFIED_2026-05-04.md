# KRX OpenAPI 정확한 endpoint 매핑 — openkrx-mcp 검증

**생성**: 2026-05-04. RealYoungk/openkrx-mcp 설치 + source 검증 후 작성.
**대체**: docs/KRX_API_FIELD_REQUEST_2026-05-04.md의 가설 endpoint 명칭

## 0. 키 상태

`GLOSTAT_KRX_API_KEY` (315500AE12AA4407951536CAB71AB39A95AD28FA) 검증:
- 31개 KRX OpenAPI endpoint **전부 401 Unauthorized**
- 키는 형식 valid (404가 아닌 401 반환)
- **운영자가 KRX 사이트 (https://openapi.krx.co.kr) 에서 endpoint별
  사용신청 + 1-2영업일 승인 대기** 필요

openkrx-mcp 설치만으로는 권한 부여 안 됨. MCP는 wrap만 제공.

## 1. openkrx-mcp 설치 완료

```bash
claude mcp add openkrx -e KRX_API_KEY=<KEY> -- uvx openkrx-mcp
# → ✓ Connected
```

31개 KRX endpoint를 MCP tool로 노출. 키 권한 승인 후 즉시 사용 가능.

## 2. 정확한 endpoint catalog (openkrx-mcp source 검증)

### 지수 (idx) — 5개

| endpoint | 설명 | GLOSTAT 활용 |
|---|---|---|
| `idx/krx_dd_trd` | KRX 시리즈 | — |
| `idx/kospi_dd_trd` | KOSPI 시리즈 | KOSPI200 정확 측정 |
| `idx/kosdaq_dd_trd` | KOSDAQ 시리즈 | KOSDAQ thesis 확장 |
| `idx/bon_dd_trd` | 채권지수 | — |
| **`idx/drvprod_dd_trd`** | **파생상품지수 (VKOSPI 포함)** | **★★★★★ E_VKOSPI_MOOD_KR** |

### 주식 (sto) — 8개

| endpoint | 설명 | GLOSTAT 활용 |
|---|---|---|
| `sto/stk_bydd_trd` | KOSPI 일별매매 | yfinance 대체, KR 정확도 ★★★ |
| `sto/ksq_bydd_trd` | KOSDAQ 일별매매 | KOSDAQ thesis ★ |
| `sto/knx_bydd_trd` | KONEX 일별매매 | — |
| `sto/sw_bydd_trd` | 신주인수권증권 | — |
| `sto/sr_bydd_trd` | 신주인수권증서 | — |
| `sto/stk_isu_base_info` | KOSPI 종목기본정보 | universe metadata |
| `sto/ksq_isu_base_info` | KOSDAQ 종목기본정보 | — |
| `sto/knx_isu_base_info` | KONEX 종목기본정보 | — |

### 파생상품 (drv) — 6개

| endpoint | 설명 | GLOSTAT 활용 |
|---|---|---|
| `drv/fut_bydd_trd` | 선물 | E_BASIS_KR 신규 thesis ★ |
| `drv/eqsfu_stk_bydd_trd` | KOSPI 주식선물 | — |
| `drv/eqkfu_ksq_bydd_trd` | KOSDAQ 주식선물 | — |
| `drv/opt_bydd_trd` | 옵션 | E_PCR_KR 신규 thesis ★ |
| `drv/eqsop_bydd_trd` | KOSPI 주식옵션 | — |
| `drv/eqkop_bydd_trd` | KOSDAQ 주식옵션 | — |

### 기타 (etp/bon/gen/esg) — 12개

GLOSTAT 활용도 낮음 (ETF/채권/원자재/ESG 등).

## 3. KRX OpenAPI에 없는 데이터 (별도 source 유지)

다음 데이터는 KRX OpenAPI **자체에 없음**:

| 데이터 | GLOSTAT thesis | 대안 source |
|---|---|---|
| 공매도 잔고/거래 | E_SHORT_SELLING_KR | 기존 `krx_short_client.py` (session-cookie scrape) |
| 투자자별 매매동향 (외국인/기관) | E_FOREIGN_REVERSAL_KR | 기존 Naver scrape + KIS |

v1.10.18 plan의 Tier 2 (공매도, 외국인)는 KRX OpenAPI로 해결 불가.
기존 client 유지.

## 4. 수정된 신청 우선순위

### Tier 1 — Critical (즉시)

| # | 카테고리 | 정확한 endpoint | 신청 한글명 (추정) |
|---|---|---|---|
| 1 | 지수 (idx) | `idx/drvprod_dd_trd` | "파생상품지수 일별시세정보" |

VKOSPI는 파생상품지수에 포함됨. 신청 시 한글명에 "변동성지수" 단독으로
없을 수 있음 — "파생상품지수"로 검색.

### Tier 2 — 인프라 (Phase 2)

| # | 카테고리 | endpoint | 신청 한글명 (추정) |
|---|---|---|---|
| 2 | 주식 (sto) | `sto/stk_bydd_trd` | "유가증권 일별매매정보" |
| 3 | 지수 (idx) | `idx/kospi_dd_trd` | "KOSPI 시리즈 일별시세정보" |

### Tier 3 — 신규 thesis (장기)

| # | 카테고리 | endpoint | 신청 한글명 (추정) |
|---|---|---|---|
| 4 | 파생 (drv) | `drv/opt_bydd_trd` | "옵션 일별매매정보" |
| 5 | 파생 (drv) | `drv/fut_bydd_trd` | "선물 일별매매정보" |

## 5. 운영자 action

1. https://openapi.krx.co.kr 로그인
2. **Tier 1 (1개) 우선 신청**: 파생상품지수 일별시세정보
3. 1-2영업일 승인 대기
4. 승인 확인 (직접 curl 또는 MCP tool 호출):
   ```bash
   uv run python -c "
   import httpx, asyncio
   async def t():
       async with httpx.AsyncClient(timeout=15.0) as c:
           r = await c.get('https://data-dbg.krx.co.kr/svc/apis/idx/drvprod_dd_trd',
                           params={'AUTH_KEY': '<KEY>', 'basDd': '20260301'})
           print(r.status_code, r.text[:200])
   asyncio.run(t())
   "
   ```
   401 → 200 전환 시 GLOSTAT 통합 착수
5. Tier 2 → Tier 3 순차 신청

## 6. v1.10.18 plan 수정 사항

### 추가
- **openkrx-mcp 설치**: 31개 endpoint MCP tool로 노출, 권한 승인 후 즉시 사용
- VKOSPI endpoint 정확화: `idx/drvprod_dd_trd` (이전 가설 `idx/vol_idx_bydd_trd`은 부정확)

### 삭제
- Tier 2의 공매도 (`srt/sht_sell_bydd_trd`, `srt/sbd_stk`) — KRX OpenAPI에 없음, 기존 `krx_short_client.py` 유지
- Tier 2의 투자자별 매매 (`sto/inv_trd_invtr`) — KRX OpenAPI에 없음, 기존 Naver scrape 유지

### 보존
- Phase 1 (VKOSPI 실측) → Tier 1 endpoint 1개로 가능
- Phase 3 (신규 thesis E_PCR_KR, E_BASIS_KR) → Tier 3 endpoint 2개

## 7. ROI 재추정

| 신청 endpoint | 활성화 thesis | 변화 |
|---|---|---|
| `idx/drvprod_dd_trd` (Tier 1) | E_VKOSPI_MOOD_KR | bootstrap → measured |
| `sto/stk_bydd_trd` (Tier 2) | KR thesis OHLCV 가속 | yfinance 4hr → 1hr |
| `drv/opt_bydd_trd` (Tier 3) | E_PCR_KR 신규 | active 9 → 10 (추가) |
| `drv/fut_bydd_trd` (Tier 3) | E_BASIS_KR 신규 | active 9 → 10 (추가) |

**Tier 1만으로도** binding constraint 해소 + active 9 → 10. 가장 큰
single-step ROI.
