# GLOSTAT — Global Cascade Intelligence Engine
## v0.3 — 2026-04-28 — **Cascade + Market Boundary** (incorporates 2 user insights)

> **변경 이력**:
> - v0.1 → v0.2: 사용자 통찰 #1 ("글로벌 cascade가 의미") → Cascade Graph + Propagation Engine + E_CASCADE 9번째 Expert 신설
> - v0.2 → v0.3: 사용자 통찰 #2 ("국가별 시장 경계 혼동 방지") → **Market Boundary & Disambiguation System** 신설 (UAID, markets.yaml, cross-market cascade 명시)

---

## 0. Vision

> **국가별 시장 경계가 명확히 정의된 글로벌 인과 사슬(Global Cascade Graph) 위에서 9 Expert × Bigdata MCP 신호를 융합하고, 이벤트 발생 시 hop 단위로 다중 시장 자산에 미치는 파급을 시간대·통화·체결가능성을 모두 표기하여 결정론적으로 예측·리플레이하는 엔진.**

**핵심 가설** (반박 가능):
1. **H1**: S&P500/KOSPI 일중 가격 변동의 상당 부분은 단일 종목 펀더가 아닌 **타 시장 이벤트의 인과 전파**에서 발생
2. **H2**: 기존 4개 스택은 모두 **단일 시장 × 단일 종목** 시야 → cross-market cascade 사각지대 큼
3. **H3**: Bigdata MCP의 filings + transcripts + tearsheet는 cascade 그래프 자동 추출에 최적
4. **H4 (NEW v0.3)**: Cross-market cascade는 **시장 경계(UAID, TZ, currency, 체결 가능성)를 명시하지 않으면 환각·오발주 위험이 cascade 가치보다 큼** → 경계 시스템이 cascade의 전제

---

## 1. 핵심 차별화 — Global Cascade Intelligence (GCI)

### 1.1 Cascade Graph (CG) — 데이터 구조

**노드 타입 (6종)**: COMPANY, SECTOR, COUNTRY, COMMODITY, CURRENCY, THEME
**노드 식별자**: 항상 UAID (Section 1.5 참조). bare ticker 금지.

**엣지 타입 (7종)** + 가중치 + 데이터 소스

| 엣지 | 가중치 [0,1] | 데이터 소스 (Bigdata MCP) |
|------|------------|-------------------------|
| `SUPPLIES_TO` | 매출 % | `bigdata_search`(10-K + EARNINGS_CALL) + `company_tearsheet`(revenue seg) |
| `COMPETES_WITH` | jaccard(점유) | `company_tearsheet`(competitors) |
| `OWNS_OF` | 보유 % | `bigdata_search`(13F) |
| `GEO_EXPOSES` | 매출 지역 % | `company_tearsheet`(revenue by geography) |
| `THEMATICALLY_LINKED` | normalized PMI | `bigdata_search`(topic, 90일) |
| `MACRO_LAGS` | IRF 계수 | `country_tearsheet` + `market_tearsheet` corr |
| `CURRENCY_HEDGES_TO` | 60일 FX β | `market_tearsheet`(currencies) |

**갱신 정책**: 정적 분기/준동적 월/동적 14일 rolling. 모든 엣지에 confidence + last_observed + sources[].

---

### 1.2 Propagation Engine — Time-aware Belief Propagation

```
input:
  Event(node=UAID, type, shock_magnitude=σ ∈ ±3, ts=T0, confidence)

output:
  dict[UAID Y → CascadeImpact(magnitude_bps, ts_horizon (UTC),
                              market_local_ts, execution_feasibility,
                              confidence, hop_count, paths[],
                              dominant_edge_type, currency_conversion)]

algorithm:
  frontier = {(X, σ, hop=0, [X])}
  visited = {}
  while frontier and hop ≤ 4:
    (node, mag, h, path) = pop
    for (neighbor, edge) in CG.edges(node):
      effective = mag × edge.weight × edge.type_multiplier × ATTENUATION^h
      delay = TZ_DELAY(node.market, neighbor.market, T0)   # markets.yaml 기반
      ts_neighbor = T0 + delay
      feasibility = market_session_at(neighbor.market, ts_neighbor)
      if |effective| < THRESHOLD: continue
      visited[neighbor].update(effective, ts_neighbor, feasibility, path)
      frontier.add((neighbor, effective, h+1, path+[neighbor]))
  
  # confidence calibration via historical IRF
  for Y in visited:
    Y.confidence = match_historical_IRF(event_type, X.market→Y.market path, lookback=90d)
  return visited
```

**Edge type multiplier 초기값**:
| 엣지 | mult |
|------|------|
| SUPPLIES_TO | +1.00 |
| COMPETES_WITH | -0.60 |
| OWNS_OF | +0.40 |
| GEO_EXPOSES | +0.50 |
| THEMATICALLY_LINKED | +0.30 |
| MACRO_LAGS | +0.70 |
| CURRENCY_HEDGES_TO | -0.45 |

**Hop attenuation**: 0.6 (default), regime-conditioned (CRASH→0.8, 전염 강화)

---

### 1.3 E_CASCADE — 9번째 Expert (구조적 이종)

- **입력**: `Event` (사건, 단일 ticker가 아님)
- **출력**: `CascadeVerdict[UAID, hop, expected_impact_bps, ts_horizon, supply_chain_path[], execution_feasibility, confidence]` 의 ranked 리스트
- **트리거**:
  - `bigdata_events_calendar` (1h 폴링)
  - `bigdata_search` news (15m, |sentiment|≥0.5 + freshness_boost≥5)
  - 매크로 surprise (`country_tearsheet` actual vs consensus)
- **가중치 캡**: ≤ **20%** (E_NARRATIVE 15%보다 높음, 검증 가능한 그래프 기반이므로)

---

### 1.4 워크드 예시 — 애플 iPhone 16 Pro 발표

```
T0 = 2026-09-09 13:00 PT = 2026-09-09 20:00 UTC
Event: type=PRODUCT_LAUNCH, node=XNAS.AAPL, σ=+0.8, theme="AI smartphone"
detection: bigdata_events_calendar (XNAS) + bigdata_search news polling

═══ HOP 1 — 즉시 (US 인트라데이) ═══
Target            Edge                Path             Impact     ts (UTC)              Feasibility    Currency
XNYS.TSM (TSMC ADR)  SUPPLIES_TO 0.25    AAPL→TSM         +1.20%     2026-09-09 20:30      NOW            USD
XNAS.QCOM         SUPPLIES_TO 0.15    AAPL→QCOM        +0.50%     2026-09-09 20:30      NOW            USD
XNAS.AVGO         SUPPLIES_TO 0.08    AAPL→AVGO        +0.30%     2026-09-09 20:30      NOW            USD

═══ HOP 2 — Overnight → Asia 개장 (T0+15h) ═══
XTAI.2317 (Hon Hai)  SUPPLIES_TO 0.30 AAPL→2317        +0.80%     2026-09-10 01:00      WAIT_NEXT (TWT 09:00 = 2026-09-10 01:00 UTC)  TWD (FX β USD/TWD 0.85)
XTAI.2330 (TSMC)     SUPPLIES_TO 0.25 AAPL→2330        +0.95%     2026-09-10 01:00      WAIT_NEXT      TWD
XTAI.3008 (Largan)   SUPPLIES_TO 0.18 AAPL→3008        +0.60%     2026-09-10 01:00      WAIT_NEXT      TWD
XKRX.005930 (삼성)   net (SUPPLIES_TO 0.08 ⊖ COMPETES_WITH -0.40) →  +0.40%   2026-09-10 00:00 (KST 09:00)   WAIT_NEXT      KRW
XKRX.000660 (SK하이닉스) SUPPLIES_TO 0.05  →  +0.30%     2026-09-10 00:00       WAIT_NEXT      KRW

═══ HOP 3 — 다음 날 EU+Asia (T0+24~33h) ═══
XAMS.ASML        SUPPLIES_TO via TSM 0.20  →  +0.40%     2026-09-10 06:00 UTC (CET 08:00)  WAIT_NEXT  EUR
XJPX.4063 (신에츠) SUPPLIES_TO via TSM 0.10  →  +0.20%     2026-09-10 00:00 (JST 09:00)     WAIT_NEXT  JPY
XJPX.4185 (JSR)    SUPPLIES_TO via TSM 0.08  →  +0.15%     2026-09-10 00:00                 WAIT_NEXT  JPY

═══ HOP 4 — 이론적 파생 (T0+48h+) ═══
XNAS.NVDA   THEMATICALLY_LINKED via "AI compute"  +0.15%   conf=0.40 (낮음)
XNYS.EXC, XNYS.NEE  THEMATICALLY_LINKED via "data center power"  +0.10%   conf=0.30
XNYS.FCX   THEMATICALLY_LINKED via "data center copper"  +0.08%   conf=0.25
XSHG, XSHE 중국 메모리: foreign_access=stock_connect_only → 사용자 계정 자격 확인 필요

═══ Cascade Verdict (Top 10 actionable, |impact_bps × confidence| 기준 정렬) ═══
1. XNYS.TSM   +120 bps × 0.78 = 94    STRONG_BUY (NOW, USD, US 인트라데이)
2. XTAI.2330  +95 bps × 0.75 = 71     BUY (WAIT_NEXT, TWD 환산 후 USD 기준 +57 bps)
3. XTAI.2317  +80 bps × 0.72 = 58     BUY (WAIT_NEXT, TWD)
4. XTAI.3008  +60 bps × 0.68 = 41     BUY (WAIT_NEXT)
5. XAMS.ASML  +40 bps × 0.65 = 26     BUY (WAIT_NEXT, EUR)
...

각 verdict는 supply_chain_path 명시 → 사용자가 인과 사슬 검증 가능.
경계 메타: target_market, currency, tz, execution_feasibility 모두 표기.
```

---

### 1.5 Market Boundary & Ticker Disambiguation (NEW v0.3) — 핵심 안전장치

#### 1.5.1 Universal Asset ID (UAID)

**문제**:
- "001" 등 짧은 코드는 시장 간 충돌 (KR `000001`은 어떤 KRX 종목? CN `000001`은 평안은행)
- 동일 회사 다중 listing: 삼성 `005930.KS`(보통주) vs `005935.KS`(우선주) vs `SSNLF`(US OTC)
- ADR/GDR 혼동: TSMC `XTAI.2330`(원장) vs `XNYS.TSM`(ADR) — 가격·변동성·접근성 다름
- 'C' = NYSE에선 Citigroup, 다른 시장에선 다른 종목
- 한국 시장의 "삼성전자" 검색 시 보통주/우선주/SDR/관계사 혼동

**해결 — UAID 표준**:
```
UAID = "{MIC}.{LOCAL_TICKER}"      # 시장-종목 단일 식별 (1차)
     | "RP:{rp_entity_id}"          # entity-level, 시장 무관 (2차, cross-listing 통합용)
```

MIC = ISO 10383 (Bigdata MCP `exchanges` enum과 1:1):
- US: `XNAS` (NASDAQ), `XNYS` (NYSE), `XASE` (NYSE American), `ARCX` (NYSE Arca)
- KR: `XKRX` (KOSPI), `XKOS` (KOSDAQ)
- TW: `XTAI`, `ROCO`
- JP: `XJPX` (Tokyo), `XNGO`, `XFKA`, `XSAP`
- HK: `XHKG`
- CN: `XSHG` (Shanghai), `XSHE` (Shenzhen)
- EU: `XLON`, `XPAR`, `XFRA`, `XAMS`, `XETR`, `XMIL`, `XMAD`, `XBRU`, `XAMS`, `XCSE`, `XHEL`, `XSTO`, `XSWX`
- 기타: `BVMF`(브라질), `XBOM`/`XNSE`(인도), `XIDX`(인니), `XSAU`(사우디)

**예시**:
- `XNAS.AAPL` = Apple on NASDAQ (primary)
- `XKRX.005930` = 삼성전자 보통주
- `XKRX.005935` = 삼성전자 우선주
- `XTAI.2330` = TSMC Taiwan (원장)
- `XNYS.TSM` = TSMC ADR on NYSE (별개 노드, FX 베타 다름)
- `RP:4A6F00` = Apple entity-level (모든 listing 통합)

#### 1.5.2 Cross-listing Resolution

```python
@dataclass(frozen=True)
class CrossListing:
    rp_entity_id: str       # RavenPack canonical (find_companies로 1회 resolve)
    primary_uaid: str       # 주 거래소 (유동성 최대)
    listings: list[str]     # 모든 UAID
    relationships: dict[str, str]  # {UAID: "common"|"preferred"|"ADR"|"GDR"|"DR"|"dual_primary"}
```

**Resolution rules**:
1. `find_companies(query)` → `rp_entity_id` → primary listing 추론 (캐시)
2. `bigdata_company_tearsheet`는 `rp_entity_id` 기반 → 자동으로 모든 listing 통합 fundamentals
3. Cascade Graph 노드는 **UAID 단위** (cross-listing은 별개 노드, 단 `OWNS_OF self` 엣지로 연결)
4. Verdict 출력 시 사용자 선호 시장 (`user_market_preference: [XNAS, XKRX, ...]`) 의 UAID로 변환

#### 1.5.3 markets.yaml — 시장별 명시 정의

```yaml
markets:
  XKRX:
    name: "Korea Exchange (KOSPI)"
    country: KR
    currency: KRW
    tz: Asia/Seoul
    sessions:
      - {name: regular, open: "09:00", close: "15:30"}
      - {name: pre_market, open: "08:30", close: "09:00"}
      - {name: after_market, open: "15:40", close: "16:00"}
    settlement: T+2
    daily_limit_pct: 30
    fee_bps: 1.5            # 0.015% 평균 위탁수수료
    tax_bps_buy: 0
    tax_bps_sell: 20        # 거래세 0.20%
    tick_size: tiered
    holidays_calendar: kr_2026.yaml
    bigdata_mcp_coverage: HIGH
    foreign_access: open

  XKOS:
    inherits: XKRX
    tax_bps_sell: 18        # 0.18%

  XNAS:
    name: "NASDAQ"
    country: US
    currency: USD
    tz: America/New_York
    sessions:
      - {name: regular, open: "09:30", close: "16:00"}
      - {name: pre_market, open: "04:00", close: "09:30"}
      - {name: after_market, open: "16:00", close: "20:00"}
    settlement: T+1         # 2024-05-28부터
    fee_bps: 0.35
    tax_bps_sell: 0.24      # SEC fee + FINRA TAF
    tick_size: 1c
    holidays_calendar: us_2026.yaml
    bigdata_mcp_coverage: HIGH
    foreign_access: open

  XNYS: { inherits: XNAS }

  XTAI:
    name: "Taiwan SE"
    country: TW
    currency: TWD
    tz: Asia/Taipei
    sessions: [{name: regular, open: "09:00", close: "13:30"}]
    settlement: T+2
    daily_limit_pct: 10
    fee_bps: 14.25          # 0.1425% 위탁
    tax_bps_sell: 30        # 0.3% 증권거래세
    bigdata_mcp_coverage: HIGH
    foreign_access: registered_only

  XJPX:
    name: "Japan Exchange (Tokyo)"
    country: JP
    currency: JPY
    tz: Asia/Tokyo
    sessions:
      - {name: morning, open: "09:00", close: "11:30"}
      - {name: afternoon, open: "12:30", close: "15:00"}
    settlement: T+2
    fee_bps: 5
    tax_bps_sell: 0
    bigdata_mcp_coverage: HIGH

  XHKG:
    name: "Hong Kong Exchanges"
    country: HK
    currency: HKD
    tz: Asia/Hong_Kong
    sessions:
      - {name: morning, open: "09:30", close: "12:00"}
      - {name: afternoon, open: "13:00", close: "16:00"}
    settlement: T+2
    fee_bps: 27             # 0.27%
    tax_bps_sell: 13        # 0.13% stamp duty
    bigdata_mcp_coverage: HIGH
    foreign_access: open  # H-share, Red Chip
                          # A-share는 stock connect 별도

  XSHG:
    name: "Shanghai SE"
    country: CN
    currency: CNY
    tz: Asia/Shanghai
    sessions:
      - {name: morning, open: "09:30", close: "11:30"}
      - {name: afternoon, open: "13:00", close: "15:00"}
    settlement: T+1
    daily_limit_pct: 10     # ChiNext는 ±20%
    fee_bps: 6
    tax_bps_sell: 5
    bigdata_mcp_coverage: MEDIUM
    foreign_access: stock_connect_only

  XSHE: { inherits: XSHG }

  XLON:
    name: "London SE"
    country: GB
    currency: GBP
    tz: Europe/London
    sessions: [{name: regular, open: "08:00", close: "16:30"}]
    settlement: T+2
    fee_bps: 5
    tax_bps_buy: 50         # 0.5% stamp duty
    bigdata_mcp_coverage: HIGH

  XAMS:
    name: "Euronext Amsterdam"
    country: NL
    currency: EUR
    tz: Europe/Amsterdam
    sessions: [{name: regular, open: "09:00", close: "17:30"}]
    settlement: T+2
    fee_bps: 4
    tax_bps_sell: 0
    bigdata_mcp_coverage: HIGH

  # ... Bigdata MCP 지원 80+ 거래소 (전체 enum 참조)
```

#### 1.5.4 Cross-Market Cascade의 명시 표기

CascadeVerdict 모든 hop은 다음을 **반드시** 명시:

```yaml
hop:
  source: XNAS.AAPL
  source_ts_utc: 2026-09-09T20:00:00Z
  source_ts_local: 2026-09-09 13:00 PT (XNAS regular session)
  target: XTAI.2317
  target_market: XTAI
  target_currency: TWD
  target_tz: Asia/Taipei
  edge: SUPPLIES_TO weight=0.30
  tz_delay: 15h (XNAS regular close 16:00 ET → XTAI regular open 09:00 TWT next day)
  target_ts_utc: 2026-09-10T01:00:00Z
  target_ts_local: 2026-09-10 09:00 TWT
  execution_feasibility: WAIT_NEXT_SESSION
  currency_conversion: USD shock → TWD impact via FX β=0.85 (60d rolling)
  expected_impact: +0.80% local TWD = +0.68% USD-equivalent
  confidence: 0.72
  caveats:
    - "TWSE foreign ownership cap applies (per-issuer)"
    - "TWD/USD volatility ±0.5% over 15h gap"
    - "TWSE daily ±10% limit may clip impact in extreme scenarios"
```

#### 1.5.5 Universe Tagging

```yaml
universes:
  US_LARGE:    {markets: [XNAS, XNYS], min_mcap_usd: 10B}
  US_MID:      {markets: [XNAS, XNYS], mcap_usd: [2B, 10B]}
  KR_KOSPI:    {markets: [XKRX], min_mcap_krw: 1T}
  KR_KOSDAQ:   {markets: [XKOS], min_mcap_krw: 100B}
  TW_TWSE:     {markets: [XTAI], min_mcap_twd: 50B}
  JP_PRIME:    {markets: [XJPX], section: prime}
  HK_HSI:      {markets: [XHKG], index_member: HSI}
  CN_CSI300:   {markets: [XSHG, XSHE], index_member: CSI300, foreign_access: stock_connect_eligible}
  EU_STOXX50:  {markets: [XPAR, XFRA, XAMS, XMIL, ...], index_member: STOXX50}
  GLOBAL_LARGE: {composite: [US_LARGE, KR_KOSPI, JP_PRIME, EU_STOXX50, HK_HSI]}
```

CLI:
- `glostat predict XNAS.AAPL` (UAID 명시)
- `glostat predict AAPL` → 모호: "Did you mean XNAS.AAPL? Multiple listings: XNAS.AAPL, XLON.0R2V (CDI). Specify."
- `glostat screen US_LARGE`
- `glostat cascade XNAS.AAPL --max_hop 4 --target_universes GLOBAL_LARGE`

---

## 2. v0.1 컨텐츠 (변경 없음)

PLAN_v0.1.md 의 다음 섹션은 v0.3에서도 그대로 유효:
- Section 1: 28개 차용 아이디어 인벤토리 (MOET A1~A8, TITAN B1~B7, BETASTRIKE C1~C7, v3_replay D1~D6)
- Section 4: 데이터 모델 (`ExpertSignal`, `Verdict`)
- Section 5: INV-GS-001~010
- Section 7: 4 워크스페이스 vs GLOSTAT 차별화 표

---

## 3. Bigdata MCP 활용 매트릭스 (v0.3)

### 3.1 9 Expert 매핑

| Expert | 1차 도구 | 보조 |
|--------|---------|-----|
| E_MACRO | `bigdata_country_tearsheet` | `bigdata_market_tearsheet` |
| E_FUNDAMENTAL | `bigdata_company_tearsheet` (Public, quarter) | — |
| E_NARRATIVE | `bigdata_search` (smart) | — |
| E_EVENT | `bigdata_events_calendar` (±14일) | `bigdata_company_tearsheet`(어닝) |
| E_FUND_FLOW | `bigdata_company_tearsheet` (fund_trends) | `bigdata_search` (13F) |
| E_ESG | `bigdata_company_tearsheet` (ESG + workforce) | `bigdata_search` (research) |
| E_GLOBAL_FLOW | `bigdata_market_tearsheet` | — |
| E_TIME | (외부 OHLCV) + 일목 계산 | `bigdata_events_calendar` |
| **E_CASCADE** | `bigdata_search`(filings+transcripts) + `bigdata_events_calendar` | `bigdata_company_tearsheet`(competitors+revenue seg) + `bigdata_market_tearsheet` |

### 3.2 호출 최적화 (v0.3 강화)

1. `find_companies` → `rp_entity_id` 영구 캐시 + UAID 매핑 동시 저장
2. Tearsheet TTL: country 6h, company 1h, market 15m, events 24h
3. Cascade 그래프 빌드는 **시장별로 분리**하여 병렬 (KR universe / US universe / TW universe ...) — `bigdata_search` 호출 quota 분산
4. `events_calendar` 호출 시 `exchanges` 파라미터로 시장별 분리 폴링
5. Smart vs Fast: cascade 추출(smart, expensive) 30% / 정형 데이터(fast) 70%

### 3.3 Cascade 그래프 빌드 — 시장별 분리

```python
for market_universe in [US_LARGE, KR_KOSPI, TW_TWSE, JP_PRIME, ...]:
    for uaid in market_universe.tickers:
        eid = entity_map[uaid].rp_entity_id
        # 1. SUPPLIES_TO via filings
        chunks = bigdata_search(request={
          "search_mode": "fast",
          "query": {
            "text": "suppliers customers dependencies risk factors",
            "filters": {
              "reporting_entities": [eid],
              "document_type": {"mode":"INCLUDE", "values":[
                {"type":"FILING", "subtypes":["SEC_10_K","SEC_20_F"]},
                {"type":"TRANSCRIPT", "subtypes":["EARNINGS_CALL"]}
              ]},
              "timestamp": {"start": "-365d"}
            },
            "max_chunks": 50
          }
        })
        # NER + relation extraction → SUPPLIES_TO 엣지 (target도 UAID로 변환)

# Cross-market 엣지는 source UAID와 target UAID가 다른 market인 경우
# 자동으로 MACRO_LAGS 또는 GEO_EXPOSES로 분류
```

---

## 4. 6.5계층 아키텍처

```
L0   Bigdata Data Plane          ─ MCP 6 tools + caching + rate budget per market
L0.5 Market Boundary System      ─ markets.yaml + UAID + cross-listing resolver  ← v0.3 NEW
L1   Macro Regime                ─ E_MACRO, E_GLOBAL_FLOW → regime{5단계, per market or global}
L2   Time Architecture           ─ E_TIME (일목, market-local) + E_EVENT (캘린더, UTC)
L2.5 Cascade Graph + Propagation ─ CG (UAID nodes) + Propagation (TZ-aware) + E_CASCADE
L3   Signal Experts (MoE)        ─ 8 Expert + E_CASCADE → ExpertSignal[] + CascadeVerdict
L4   Gating + Cost-First Sizing  ─ IC-softmax + anti-herd + adverse-flow + W값 + Kelly + per-market cost
L5   Verdict + Replay + Audit    ─ STRONG_BUY..STRONG_SELL + cascade + UAID + 시장 메타 + hash
```

---

## 5. 데이터 모델 추가 (v0.3)

```python
@dataclass(frozen=True)
class UAID:
    market: str             # MIC
    local_ticker: str
    rp_entity_id: str       # cross-listing 통합용
    listing_type: Literal["common","preferred","ADR","GDR","DR","dual_primary"]
    
    def __str__(self): return f"{self.market}.{self.local_ticker}"

@dataclass(frozen=True)
class MarketMeta:
    mic: str
    country: str            # ISO 3166-1 alpha-2
    currency: str           # ISO 4217
    tz: str                 # IANA
    sessions: list[Session]
    settlement_days: int
    fee_bps: float
    tax_bps_buy: float
    tax_bps_sell: float
    daily_limit_pct: float | None
    foreign_access: Literal["open","registered_only","stock_connect_only","restricted"]
    bigdata_mcp_coverage: Literal["HIGH","MEDIUM","LOW","NONE"]

@dataclass(frozen=True)
class CGEdge:
    src: UAID
    dst: UAID
    edge_type: str
    weight: float
    confidence: float
    last_observed: datetime
    sources: list[str]
    is_cross_market: bool   # auto-derived

@dataclass(frozen=True)
class CascadeImpact:
    target: UAID
    magnitude_bps_local: float    # local currency
    magnitude_bps_usd: float      # USD-normalized for ranking
    ts_horizon_utc: datetime
    ts_horizon_market_local: str
    execution_feasibility: Literal["NOW","WAIT_NEXT_SESSION","WAIT_NEXT_DAY","CLOSED_HOLIDAY","RESTRICTED_FOREIGN"]
    currency_conversion: dict     # {fx_pair, beta, lookback}
    confidence: float
    hop_count: int
    paths: list[list[str]]        # UAID lists
    dominant_edge_type: str
    caveats: list[str]            # daily limit, FX vol, foreign access 등

@dataclass(frozen=True)
class Verdict:
    # ... v0.1 필드 ...
    target_uaid: UAID                  # NEW v0.3
    target_market_meta: MarketMeta     # NEW v0.3
    edge_bps: float                    # market-specific from markets.yaml
    all_in_bps: float                  # market-specific
    applicable_session: str            # NEW v0.3
```

---

## 6. Invariants (v0.1 INV-GS-001..010 + v0.2 011..016 + v0.3 017..021)

| ID | 불변식 |
|----|-------|
| ... | (v0.1 + v0.2 그대로) |
| **INV-GS-017** | 모든 ticker 입력은 UAID 형식 (MIC.LOCAL or RP:entity_id). bare ticker 입력 시 모호성 검사 → 다중 매칭 시 사용자 disambiguation prompt; 단일 매칭이어도 명시적 confirm |
| **INV-GS-018** | Verdict는 반드시 `target_uaid` + `target_market_meta` (currency, tz, fee, tax, foreign_access) 포함 |
| **INV-GS-019** | Cross-market cascade hop은 `tz_delay`, `execution_feasibility` (NOW/WAIT_NEXT_SESSION/WAIT_NEXT_DAY/CLOSED_HOLIDAY/RESTRICTED_FOREIGN), `currency_conversion` 명시 |
| **INV-GS-020** | Cost-Gate (INV-GS-001)의 `all_in_bps`는 `target_market`의 `markets.yaml`에서 조회 — 시장별 차등 적용 (예: KR sell 20bps, US sell 0.24bps, HK sell 13bps) |
| **INV-GS-021** | `foreign_access ≠ open` 시장은 verdict에 `executable_for_user: bool` 명시. 기본값 false → 사용자 계정 자격 명시 후 true 가능 |

---

## 7. Sprint Roadmap (v0.3)

| Sprint | 산출물 |
|--------|-------|
| S1 | Data Plane (bigdata_client, entity_map, budget) |
| **S1.5 NEW** | **Market Boundary System** — markets.yaml + UAID resolver + cross-listing map; `glostat resolve "삼성전자"` → 4 후보 UAID 반환 |
| S2 | E_MACRO + E_FUNDAMENTAL + E_EVENT |
| S3 | E_NARRATIVE + E_FUND_FLOW + Gating v0 |
| S4 | Cost-Gate (per-market) + W값 + Verdict v1 |
| S4.5 | Cascade Graph 오프라인 빌더 (UAID 노드, market 분리 빌드) |
| S5 | E_TIME + E_ESG + E_GLOBAL_FLOW |
| S5.5 | Propagation Engine + E_CASCADE (TZ-aware, currency-aware) |
| S6 | Risk Layer (DEFCON, Blacklist, JURY) |
| S6.5 | 실시간 이벤트 → cascade alert (시장별 폴링) |
| S7 | Replay + Hindcast + Evidence Chain |
| S8 | macOS Menubar + Dashboard (cross-market cascade 시각화) + Telegram |

---

## 8. 차별화 매트릭스 (v0.3)

| 차원 | MOET | TITAN | BETASTRIKE | v3_replay | **GLOSTAT v0.3** |
|------|------|-------|-----------|-----------|------------------|
| 시장 범위 | KRX | KRX | KRX | KRX | **글로벌 80+ 거래소 (Bigdata MCP coverage)** |
| 시장 경계 명시 | 없음 | 없음 | 없음 | 없음 | **UAID + markets.yaml + foreign_access flag** |
| TZ-aware cascade | 없음 | 없음 | 없음 | 없음 | **시장별 정적 캘린더 + execution_feasibility** |
| Currency normalization | KRW only | KRW+USD | KRW only | KRW only | **모든 verdict 2-view: local + USD** |
| Cost gate | KR only | — | KR only | KR only | **per-market cost table (markets.yaml)** |
| Cross-listing | — | — | — | — | **rp_entity_id 통합, 다중 listing 별개 노드** |
| 인과 사슬 추론 | 없음 | 없음 | 없음 | 없음 | **Cascade Graph + Propagation 1-4 hop** |
| Supply chain visibility | 없음 | 없음 | 없음 | 없음 | **10-K + 20-F + EARNINGS_CALL 자동 추출** |

---

## 9. 핵심 메시지 (TL;DR v0.3)

1. **사건 → 다중 시장 인과 사슬 예측**이 GLOSTAT의 1차 가치 (cascade)
2. **시장별 명확한 경계 (UAID + markets.yaml + 체결가능성 플래그)**가 cascade의 안전장치 — 환각/오발주 방지의 구조적 보호
3. Cascade Graph는 Bigdata MCP에서 자동 추출, 시장별로 분리 빌드하여 quota 분산
4. 모든 verdict는 `(UAID, currency, tz, session, foreign_access)` 메타와 `supply_chain_path[]` 동반 → 사용자가 인과·실행가능성 모두 검증 가능
5. 8 Expert + E_CASCADE 융합. E_CASCADE 가중치 캡 20%, E_NARRATIVE 15%

---

**v0.3 작성 완료. 10인 전문가 검토 대상 문서. Plan v0.1 + v0.2 + v0.3 모두 ./docs/ssot/ 에 보존.**
