# GLOSTAT — Global Cascade Intelligence Engine
## v0.2 — 2026-04-28 — **Cascade-First Refactor** (incorporates user insight)

> **변경**: v0.1은 "글로벌 다중 종목 verdict 엔진"이 핵심이었음. v0.2는 **"이벤트 → 글로벌 인과 사슬 전파 예측"**을 1차 가치명제로 승격. 8 Expert는 그대로 유지하되, **9번째 Expert E_CASCADE**와 **Cascade Graph 데이터 평면**을 신설.

---

## 0. Vision (Refactored)

> **글로벌 인과 사슬(Global Cascade Graph) 위에서 9 Expert × Bigdata MCP 신호를 융합하고, 이벤트 발생 시 hop 단위로 다중 시장 자산에 미치는 파급을 결정론적으로 예측·리플레이하는 엔진.**

**핵심 가설** (반박 가능):
1. **H1**: S&P500/KOSPI 일중 가격 변동의 상당 부분은 단일 종목 펀더가 아닌 **타 시장 이벤트의 인과 전파**에서 발생 (예: 미국 어닝 → 아시아 overnight gap)
2. **H2**: 기존 4개 스택(MOET, TITAN, BETASTRIKE, v3_replay)은 모두 **단일 시장(KRX) × 단일 종목** 시야 → cross-market cascade 사각지대 큼
3. **H3**: Bigdata MCP의 `bigdata_search`(filings + transcripts + news) + `bigdata_company_tearsheet`(competitors + revenue segmentation by geography)는 cascade 그래프 자동 추출에 최적인 유일한 통합 데이터원

→ **GLOSTAT은 이 세 가설을 검증하고 활용하는 도구**.

---

## 1. 핵심 차별화 — Global Cascade Intelligence (GCI)

### 1.1 Cascade Graph (CG) — 데이터 구조

**노드 타입 (6종)**
- `COMPANY` — Apple, TSMC, 삼성전자, ASML, …
- `SECTOR` — Semiconductor, Auto, Energy, …
- `COUNTRY` — US, KR, TW, JP, CN, NL, …
- `COMMODITY` — Copper, Uranium, Lithium, Oil, …
- `CURRENCY` — USD, KRW, TWD, JPY, …
- `THEME` — AI accelerator, EV battery, GLP-1, Onshoring, Energy Transition, …

**엣지 타입 (7종) + 가중치 정의 + 데이터 소스**

| 엣지 | 의미 | 가중치 [0,1] | 데이터 소스 (Bigdata MCP) |
|------|------|------------|-------------------------|
| `SUPPLIES_TO` | A→B로 매출 | A 매출 중 B 비중 | `bigdata_search`(10-K Risk + EARNINGS_CALL) + `company_tearsheet`(revenue segmentation) |
| `COMPETES_WITH` | 동일 시장 점유율 중첩 | jaccard(시장점유) | `company_tearsheet`(competitors 섹션) |
| `OWNS_OF` | 지분 보유 % | 보유 % / float | `bigdata_search`(13F filings) |
| `GEO_EXPOSES` | A의 매출 지역 B 비중 | A 매출 중 B국가 % | `company_tearsheet`(revenue by geography) |
| `THEMATICALLY_LINKED` | co-mention 빈도 | normalized PMI | `bigdata_search`(topic clustering, 90일 rolling) |
| `MACRO_LAGS` | A 매크로 지표 → B 자산 IRF | impulse response 계수 | `country_tearsheet` + `market_tearsheet` 1D/5D/1M corr |
| `CURRENCY_HEDGES_TO` | 환율 베타 | 60일 FX β | `market_tearsheet`(currencies) |

**갱신 정책**
- 정적 엣지(`SUPPLIES_TO`, `OWNS_OF`, `GEO_EXPOSES`): **분기별** filings 갱신 트리거로 재구축
- 준동적 엣지(`COMPETES_WITH`, `MACRO_LAGS`, `CURRENCY_HEDGES_TO`): **월별**
- 동적 엣지(`THEMATICALLY_LINKED`): **14일 rolling window** + exponential half-life 90일
- 모든 엣지에 `confidence ∈ [0,1]` (추출 신뢰도) + `last_observed` 메타

**저장**: NetworkX (메모리) + Parquet 스냅샷 + Neo4j 옵션(scale-out)

---

### 1.2 Propagation Engine — Time-aware Belief Propagation

**알고리즘 의사코드**:
```
input:
  Event(node=X, type=PRODUCT_LAUNCH|EARNINGS|GEOPOLITICAL|MACRO|...,
        shock_magnitude=σ ∈ ±3, shock_direction=±1, ts=T0,
        confidence ∈ [0,1])

output:
  dict[Node Y → CascadeImpact(magnitude_bps, ts_horizon, confidence,
                              hop_count, paths[], dominant_edge_type)]

algorithm:
  frontier = {(X, σ, 0, [X])}      # (node, signed_magnitude, hop, path)
  visited = {}
  while frontier not empty AND hop ≤ MAX_HOP(=4):
    (node, mag, h, path) = pop
    for (neighbor, edge) in CG.edges(node):
      effective = mag × edge.weight × edge.type_multiplier × ATTENUATION^h
      ts_neighbor = T0 + edge.delay(node, neighbor)   # TZ-aware
      if |effective| < THRESHOLD: continue
      if neighbor in visited:
        # multi-path aggregation (signed)
        visited[neighbor].add(effective, ts_neighbor, path)
      else:
        visited[neighbor] = CascadeImpact(...)
        frontier.add((neighbor, effective, h+1, path+[neighbor]))
  
  # confidence calibration
  for Y in visited:
    visited[Y].confidence = historical_IRF_match(event_type, X→Y path, lookback=90d)

  return visited
```

**시간대(TZ) 동기화 — `edge.delay`**:
- 미국 장 마감 16:00 ET → 한국 장 시작 09:00 KST = **15h gap** (overnight cascade)
- 한국 장 마감 15:30 KST → 유럽 장 시작 09:00 CET = **2.5h gap**
- 미국 장 마감 → 유럽 장 시작 = **9h gap**
- 동일 시간대 내 cascade는 분 단위로 즉시 (단, 매크로 이벤트는 최대 2h delay)

**Edge type multiplier (초기값, 캘리브레이션 대상)**:
| 엣지 | multiplier | 근거 |
|------|----------|------|
| `SUPPLIES_TO` | 1.00 | 직접적 매출 영향 |
| `COMPETES_WITH` | -0.60 | 부정 상관 (점유율 zero-sum) |
| `OWNS_OF` | 0.40 | 지분 평가 영향 |
| `GEO_EXPOSES` | 0.50 | 지역 risk-on/off |
| `THEMATICALLY_LINKED` | 0.30 | 약한 narrative 동조 |
| `MACRO_LAGS` | 0.70 | 매크로 → 섹터 강한 영향 |
| `CURRENCY_HEDGES_TO` | -0.45 | 환율 역방향 |

**Attenuation per hop**: 0.6 (default), regime-conditioned (CRASH 시 0.8, 전염 강화)

---

### 1.3 E_CASCADE — 9번째 Expert (구조적 이종)

다른 8 Expert와 결정적 차이:
- **입력**: `Event` (단일 종목 ticker가 아닌 **사건**)
- **출력**: `CascadeVerdict[Ticker_i, hop, expected_impact_bps, ts_horizon, supply_chain_path[], confidence]` 의 **랭킹된 리스트**
- **트리거**:
  - `bigdata_events_calendar` 폴링 (매 1h)
  - `bigdata_search` news 폴링 (매 15m, sentiment |s| ≥ 0.5 + freshness_boost ≥ 5)
  - 매크로 surprise (`country_tearsheet` 신규 데이터 actual ≠ consensus)

**E_CASCADE 가중치 캡**: ≤ **20%** (E_NARRATIVE 15%보다 높음 — cascade는 검증 가능한 그래프 기반이므로 신뢰성 높음)

---

### 1.4 워크드 예시 — 애플 iPhone 16 Pro 발표

```
T0 = 2026-09-09 13:00 PT  (Apple Keynote)
Event: type=PRODUCT_LAUNCH, node=AAPL, σ=+0.8, theme="AI smartphone"
detection: bigdata_events_calendar (hit) + bigdata_search news (15m 후 polling)

═══ HOP 1 — 즉시 (미국 인트라데이) ═══
TSM    SUPPLIES_TO 0.25, σ=+0.20  →  +1.2% expected, ts=T0+30m, conf=0.78
QCOM   SUPPLIES_TO 0.15, σ=+0.12  →  +0.5%, conf=0.65
AVGO   SUPPLIES_TO 0.08, σ=+0.06  →  +0.3%, conf=0.58
SWKS   SUPPLIES_TO 0.06, σ=+0.05  →  +0.25%, conf=0.55

═══ HOP 2 — Overnight → 아시아 개장 (T0+15h) ═══
2317.TW (Hon Hai)   SUPPLIES_TO via AAPL 0.30 ⊕ TSM 0.10  →  +0.8%, conf=0.72
3008.TW (Largan)    SUPPLIES_TO via AAPL 0.18                →  +0.6%, conf=0.68
005930.KS (삼성전자) SUPPLIES_TO via AAPL 0.08 ⊖ COMPETES_WITH AAPL -0.40  
                    → net +0.4%, conf=0.55  (path: AAPL→Samsung[OLED+memory] vs AAPL→Samsung[phone])
000660.KS (SK하이닉스) SUPPLIES_TO via AAPL 0.05 (NAND/HBM)  →  +0.3%, conf=0.62

═══ HOP 3 — 다음 날 아시아+유럽 (T0+24h~33h) ═══
ASML.AS  SUPPLIES_TO via TSM (3nm 장비) 0.20  →  +0.4%, conf=0.65
4063.T (신에츠, Si wafer) SUPPLIES_TO via TSM 0.10  →  +0.2%, conf=0.50
4185.T (JSR, photoresist) SUPPLIES_TO via TSM 0.08  →  +0.15%, conf=0.45

═══ HOP 4 — 이론적 파생 (T0+48h+) ═══
NVDA      THEMATICALLY_LINKED via "AI compute"  →  +0.15%, conf=0.40 (낮음)
EXC, NEE  THEMATICALLY_LINKED via "data center power"  →  +0.10%, conf=0.30
FCX       THEMATICALLY_LINKED via "data center copper"  →  +0.08%, conf=0.25
중국 메모리 (보안 이유 미상장) — 차단

═══ Cascade Verdict 출력 ═══
Top 10 actionable tickers (ranked by |impact_bps × confidence|):
1. TSM    +94 bps × 0.78 = 73 → STRONG_BUY (US 인트라데이)
2. 2317   +58 bps × 0.72 = 42 → BUY (KR/TW overnight gap play)
3. 3008   +41 bps × 0.68 = 28 → BUY
4. ASML   +26 bps × 0.65 = 17 → BUY (다음 날 EU)
...

각 verdict는 supply_chain_path 명시 → 사용자가 인과 사슬 검증 가능.
```

---

## 2. v0.1 컨텐츠 (Idea Inventory, 8 Expert, Invariants, Sprint, etc.)

**v0.1 PLAN_v0.1.md 의 다음 섹션은 v0.2에서도 유효하며 변경 없음**:
- Section 1 (28개 차용 아이디어 인벤토리)
- Section 4 (`ExpertSignal`, `Verdict` 데이터 모델)
- Section 5 (INV-GS-001..010)
- Section 7 (4 워크스페이스 vs GLOSTAT 차별화 표)

**v0.2에서 변경/확장된 항목만 아래에 명시.**

---

## 3. Bigdata MCP 활용 매트릭스 (Updated)

### 3.1 9 Expert 매핑 (E_CASCADE 추가)

기존 8 Expert는 v0.1과 동일. 추가:

| Expert | 1차 도구 | 보조 도구 | 출력 |
|--------|---------|---------|-----|
| **E_CASCADE** (NEW) | `bigdata_search`(filings + transcripts) + `bigdata_events_calendar` | `bigdata_company_tearsheet`(competitors + revenue seg) + `bigdata_market_tearsheet` | CascadeVerdict[ticker_i, hop, bps, ts, paths] |

### 3.2 Cascade-specific Bigdata MCP usage

**오프라인 그래프 구축 (월 1회 + 분기 filings 트리거)**

```python
# 1. SUPPLIES_TO 추출 — universe 전체 종목
for ticker in universe:
    eid = entity_map[ticker]   # find_companies cache
    chunks = bigdata_search(request={
      "search_mode": "fast",
      "query": {
        "text": f"suppliers customers dependencies risk factors revenue",
        "filters": {
          "reporting_entities": [eid],
          "document_type": {"mode":"INCLUDE", "values":[
            {"type":"FILING", "subtypes":["SEC_10_K"]},
            {"type":"TRANSCRIPT", "subtypes":["EARNINGS_CALL"]}
          ]},
          "timestamp": {"start": "-365d"}
        },
        "max_chunks": 50
      }
    })
    # NER + relation extraction (Gemini/Claude 보조) → SUPPLIES_TO 엣지

# 2. THEMATICALLY_LINKED — 테마별 동시 등장
for theme in CG_THEMES:   # AI accelerator, EV battery, GLP-1, ...
    chunks = bigdata_search(request={
      "search_mode": "smart",
      "query": {"text": theme, "max_chunks": 200}
    })
    # entity co-occurrence matrix → normalized PMI → 엣지 가중치

# 3. COMPETITORS / GEO / REVENUE_SEG — tearsheet에서 직접
for entity_id in entity_ids:
    ts = bigdata_company_tearsheet(rp_entity_id=eid, company_type="Public")
    # ts.competitors → COMPETES_WITH
    # ts.revenue_segmentation_by_geography → GEO_EXPOSES
```

**실시간 이벤트 감지 (1h + 15m 폴링)**

```python
# 1h: 어닝/컨퍼런스 캘린더
events_today = bigdata_events_calendar(
  start_date=today, end_date=today+1d
)

# 15m: high-impact news
news = bigdata_search(request={
  "search_mode": "fast",
  "query": {
    "text": "major announcement product launch acquisition guidance",
    "filters": {
      "timestamp": {"start": now-15m},
      "sentiment": {"ranges": [{"min":-1,"max":-0.5}, {"min":0.5,"max":1}]},
      "category": {"mode":"INCLUDE", "values":["news_premium","news"]}
    },
    "ranking_params": {"freshness_boost": 8.0},
    "max_chunks": 30
  }
})

# Cascade 트리거
for event in events_today + news:
    cascade = propagation_engine(event, CG)
    if max(c.magnitude × c.confidence for c in cascade.values()) > THRESH:
        emit_cascade_verdict(cascade)
```

---

## 4. 6.5계층 아키텍처 (Updated)

```
L0   Bigdata Data Plane          ─ MCP 6 tools + caching + rate budget
L1   Macro Regime                ─ E_MACRO, E_GLOBAL_FLOW → regime{5단계}
L2   Time Architecture           ─ E_TIME (일목) + E_EVENT (캘린더) → time_score T
L2.5 Cascade Graph + Propagation ─ CG + Propagation Engine + E_CASCADE  ← NEW
L3   Signal Experts (MoE)        ─ 8 Expert + E_CASCADE → ExpertSignal[] + CascadeVerdict
L4   Gating + Cost-First Sizing  ─ IC-softmax + anti-herd + adverse-flow + W값 + Kelly
L5   Verdict + Replay + Audit    ─ STRONG_BUY..STRONG_SELL + cascade chain + hash + reproducibility
```

---

## 5. 데이터 모델 추가

```python
@dataclass(frozen=True)
class CGEdge:
    src: str                # node id
    dst: str
    edge_type: Literal["SUPPLIES_TO","COMPETES_WITH","OWNS_OF",
                       "GEO_EXPOSES","THEMATICALLY_LINKED",
                       "MACRO_LAGS","CURRENCY_HEDGES_TO"]
    weight: float           # [0,1]
    confidence: float       # 추출 신뢰도 [0,1]
    last_observed: datetime
    sources: list[str]      # Bigdata MCP source URLs

@dataclass(frozen=True)
class Event:
    node_id: str
    event_type: Literal["EARNINGS","PRODUCT_LAUNCH","M_AND_A",
                        "GEOPOLITICAL","MACRO_RELEASE","REGULATORY",
                        "GUIDANCE","TRANSCRIPT_HOT"]
    shock_magnitude: float  # σ, signed, ±3
    ts: datetime
    confidence: float
    sources: list[str]

@dataclass(frozen=True)
class CascadeImpact:
    target_node: str
    magnitude_bps: float    # signed
    ts_horizon: datetime
    confidence: float
    hop_count: int
    paths: list[list[str]]   # [[X→Y→Z], [X→W→Z], ...]
    dominant_edge_type: str

@dataclass(frozen=True)
class CascadeVerdict:
    triggering_event: Event
    impacts: dict[str, CascadeImpact]   # ticker → impact
    top_actionable: list[tuple[str, str, float]]  # (ticker, action, score)
    evidence_hash: str
```

---

## 6. Invariants 추가 (INV-GS-011..016)

| ID | 불변식 |
|----|-------|
| **INV-GS-011** | Cascade Graph 엣지는 모두 `sources[]` 비어있지 않아야 함 (출처 없는 엣지 금지) |
| **INV-GS-012** | Propagation Engine MAX_HOP=4 (성능+신뢰성), THRESHOLD=10bps (노이즈 컷) |
| **INV-GS-013** | E_CASCADE 가중치 ≤ 20% (E_NARRATIVE 15%보다 높지만 다른 7 Expert 합계와 균형) |
| **INV-GS-014** | CascadeVerdict에 `triggering_event.sources[]` 누락 시 emission 거부 |
| **INV-GS-015** | TZ delay는 시장별 정적 캘린더 (`configs/market_hours.yaml`)로만 계산 — 추정 금지 |
| **INV-GS-016** | Multi-path aggregation은 signed sum (상쇄 허용), 절대값 sum 금지 (overestimation 방지) |

---

## 7. Sprint Roadmap (Updated)

기존 8 sprint에 cascade workstream 삽입:

| Sprint | 산출물 |
|--------|-------|
| S1~S4 | (v0.1 그대로) Data Plane → 4 Expert → Gating + Cost-Gate → Verdict v1 |
| **S4.5** | **Cascade Graph 오프라인 빌더** — universe 100 종목, SUPPLIES_TO + COMPETES_WITH 엣지 추출, NetworkX 저장 |
| S5 | (v0.1) E_TIME + E_ESG + E_GLOBAL_FLOW |
| **S5.5** | **Propagation Engine + E_CASCADE Expert** — Apple iPhone 시뮬레이션 통과 |
| S6 | Risk Layer (DEFCON, Blacklist, JURY) |
| **S6.5** | **실시간 이벤트 → cascade alert** — events_calendar + 15m news polling |
| S7 | Replay + Hindcast + Evidence Chain (cascade 포함) |
| S8 | macOS Menubar + Dashboard + Telegram (cascade 시각화 포함) |

---

## 8. 차별화 매트릭스 (Updated)

기존 행에 추가:

| 차원 | MOET | TITAN | BETASTRIKE | v3_replay | **GLOSTAT v0.2** |
|------|------|-------|-----------|-----------|------------------|
| **인과 사슬 추론** | 없음 | 없음 | 없음 | 없음 | **Cascade Graph + Propagation Engine 1-4 hop** |
| **이벤트 → 다중 자산 시뮬** | 없음 | 없음 | 없음 | 없음 | **9th Expert E_CASCADE** |
| **TZ-aware overnight cascade** | 없음 | 없음 | 없음 | 없음 | **시장별 정적 캘린더 + 시간 시퀀스** |
| **Supply chain visibility** | 없음 | 없음 | 없음 | 없음 | **10-K Risk Factors 자동 추출 (Bigdata)** |

---

## 9. 핵심 메시지 (TL;DR)

1. **단일 종목 종합점수**가 아니라 **사건 → 다중 시장 인과 사슬**이 GLOSTAT의 1차 가치명제.
2. Cascade Graph는 **Bigdata MCP의 filings + transcripts + tearsheet**에서 자동 추출 — 기존 4개 스택의 KRX 단일 시장 한계를 정확히 보완.
3. 8 Expert는 **종목별 raw signal 생성기**, E_CASCADE는 **이벤트별 ranked ticker 출력기** — 두 출력은 verdict 단계에서 fuse.
4. 사용자가 verdict를 받을 때 **supply_chain_path**가 함께 나오므로 인과 검증 가능 → AI 환각 방지의 구조적 안전장치.

---

**v0.2 작성 완료. 10인 전문가 검토 대상 문서.**
