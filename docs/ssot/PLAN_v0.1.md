# GLOSTAT — Global Stock Action & Tearsheet Intelligence
## v0.1 — 2026-04-28 — Synthesis from MOET, TITAN, BETASTRIKE, v3_replay

## 0. 한 줄 정의

> **MOET의 MoE 게이팅 × TITAN의 5계층 시간 건축 × BETASTRIKE의 Adverse-Flow 우선 + ExitContextScorer × v3_replay의 결정론적 리플레이**를 **Bigdata MCP**(글로벌 기관급 데이터)를 1차 신호원으로 결합한 **글로벌 종목 변동 예측·증거기반 의사결정 엔진**.

기존 4개 스택의 약점(KRX 단일 시장, 자체 수집 한계, 분절된 도메인)을 Bigdata MCP가 보완 — **국가 매크로 + 종목 펀더 + 이벤트 캘린더 + 내러티브 검색 + 글로벌 자금흐름**을 한 데이터 평면으로 통합.

---

## 1. 차용 아이디어 인벤토리 (Idea Inventory)

| # | 출처 | 아이디어 | GLOSTAT 차용 방식 |
|---|------|---------|------------------|
| **A1** | MOET | IC 가중 softmax + entropy regularization | `gating/network.py` — Expert 8개 가중 |
| **A2** | MOET | Anti-herding (≥4 동의 시 0.80×) | 신호 다양성 강제, 군중 시그널 페널티 |
| **A3** | MOET | Minority premium (1.15×) + Meta-adjudicator veto | 소수의견 보호, LLM 메타 검증 |
| **A4** | MOET | **INV-001: edge_bps ≥ 1.5 × all_in_bps** | Cost-first 게이트 — 글로벌 시장별 비용 테이블 |
| **A5** | MOET | Hash-chained NDJSON evidence | 모든 verdict/예측을 감사 가능하게 append-only |
| **A6** | MOET | DEFCON 3단계 + 종목 일일 블랙리스트 | 2계층 위험 (변동성/극단) |
| **A7** | MOET | SeededRng + 결정론적 replay | 동일 입력→동일 verdict (재현성) |
| **A8** | MOET | Z-5 운영 규율 (대충/환각/변명/숨김/모호 금지) | 도구 규약화 |
| **B1** | TITAN | 5계층 (Regime→Time→Valuation→Accumulation→Execution) | 6계층으로 확장 (Bigdata layer 추가) |
| **B2** | TITAN | **일목 기본수치 (65/76/129/172/200/257)** + 수렴도 T값 | `time_arch/ichimoku.py` — 시간 게이트 |
| **B3** | TITAN | **W값 DCA 스케줄** (W=0.30R+0.25T+0.25V+0.20S) | Conviction → 분할 매수 강도 |
| **B4** | TITAN | 50일 외인 흐름 패턴 (REVERSAL_BUY 60.3%) | Bigdata fund_trends 활용 (글로벌 확장) |
| **B5** | TITAN | LLM 감성 + 키워드 폴백 | bigdata_search + 폴백 키워드 |
| **B6** | TITAN | macOS Menubar + 60초 폴링 + native Notification | UX 그대로 |
| **B7** | TITAN | 7엔진 통합 Verdict (STRONG_BUY ~ STRONG_SELL) | 8 Expert → 5단계 verdict |
| **C1** | BETASTRIKE | **MoE Scorer + LR calibration (AUC 0.611, 91K)** | 칼리브레이션 프레임워크 |
| **C2** | BETASTRIKE | **Adverse-Flow First gating** (40%+ false-cut) | 1차 거부 필터 (역흐름 검증) |
| **C3** | BETASTRIKE | Archetype 분류 (impulse/continuation/contrarian/mixed) | 신호 품질 분류 |
| **C4** | BETASTRIKE | **ExitContextScorer** (청산 확률 동적 재평가) | 보유 중 verdict 재계산 |
| **C5** | BETASTRIKE | Recovery handoff (-0.35%~0% 회복 대기) | 얕은 손실 보유 정책 |
| **C6** | BETASTRIKE | Regime-aware exit timeouts (180s→240s) | 체제별 동적 타임아웃 |
| **C7** | BETASTRIKE | Boot Position Restorer | 재시작 시 verdict 컨텍스트 복원 |
| **D1** | v3_replay | **Short-tick replay (0.8s smoke)** | CI/CD 회귀 테스트 |
| **D2** | v3_replay | 다중 신호 조합 (oa+ba+babs+de) | Expert score blending |
| **D3** | v3_replay | Kelly Sizing | 사이징 보조 (W값 보완) |
| **D4** | v3_replay | 5단계 시장국면 (STRONG_UP/DOWN/TRENDING/RANGING) | 글로벌 체제 5단계 |
| **D5** | v3_replay | JURY 자동 일시정지 | 성과 저하 시 auto-pause |
| **D6** | v3_replay | O(1) feature 계산 (VWAP/EMA 누적) | 고속 피처 |

---

## 2. Bigdata MCP 활용 극대화 매트릭스

6개 도구 → 8 Expert에 매핑.

| Expert | 1차 도구 | 보조 도구 | 출력 신호 |
|--------|---------|---------|----------|
| **E_MACRO** (체제) | `bigdata_country_tearsheet` (US+발행국) | `bigdata_market_tearsheet` (8 asset class) | regime∈{BULL_S, BULL_W, BEAR_S, BEAR_W, CRASH}, 매크로 surprise score |
| **E_FUNDAMENTAL** | `bigdata_company_tearsheet` (Public, quarter) | — | PER/PBR/ROE z-score, 어닝 surprise %, fwd estimate trend |
| **E_NARRATIVE** | `bigdata_search` (smart, sentiment 필터) | — | 14일 narrative drift, sentiment slope, theme cluster |
| **E_EVENT** | `bigdata_events_calendar` (±14일) | `bigdata_company_tearsheet` (어닝일) | pre/post earnings window, conference catalyst |
| **E_FUND_FLOW** | `bigdata_company_tearsheet` (fund_trends 섹션) | `bigdata_search` (filings: 13F) | 기관 순매수 변화 (D5/D20/D60), 옵션 활동 |
| **E_ESG** | `bigdata_company_tearsheet` (ESG + workforce) | `bigdata_search` (research) | ESG trajectory, 인력 in/out trend |
| **E_GLOBAL_FLOW** | `bigdata_market_tearsheet` | — | 섹터 로테이션, 팩터 회전 (growth↔value), risk-on/off |
| **E_TIME** (TITAN B2) | (외부) yfinance OHLCV + 일목 계산 | `bigdata_events_calendar` (시간 정렬) | 기본수치±3일 수렴도 T (1.0/1.5/2.0) |

### Bigdata MCP 호출 최적화 규칙

1. **`find_companies`는 종목당 1회만** — 결과 `rp_entity_id` 영구 캐시 (`cache/entity_map.parquet`)
2. **배치 병렬화** — 종목 N개 verdict 시 8 Expert × N 호출을 단일 메시지에 fan-out
3. **Smart vs Fast 예산** — narrative 탐색은 smart, 정형 데이터는 fast (3:7 비율 목표)
4. **재호출 정책** — country_tearsheet TTL 6h, company_tearsheet TTL 1h, market_tearsheet TTL 15m, events_calendar TTL 24h
5. **Sentiment 필터 적극 활용** — 양극단만 수집해 노이즈 제거
6. **Reporting periods 핀** — 어닝 시즌엔 최신 콜만 추적

---

## 3. 6계층 아키텍처

```
L0  Bigdata Data Plane          ─ MCP 6 tools + caching + rate budget
L1  Macro Regime                ─ E_MACRO, E_GLOBAL_FLOW → regime{5단계}
L2  Time Architecture            ─ E_TIME (일목) + E_EVENT (캘린더) → time_score T
L3  Signal Experts (MoE)         ─ 8 Expert → ExpertSignal[]
L4  Gating + Cost-First Sizing   ─ IC-softmax + anti-herd + adverse-flow + W값 + Kelly
L5  Verdict + Replay + Audit     ─ STRONG_BUY..STRONG_SELL + hash-chain + reproducibility
```

실행 모드:
- `predict <ticker>` — 단일 종목 즉시 verdict
- `screen <universe>` — 모든 종목 스크리닝 (병렬)
- `watch <ticker>` — 보유 중 ExitContextScorer 재평가
- `replay <date_range>` — 결정론 리플레이 (회귀 테스트)
- `smoke` — 0.8초 통합 프로브 (CI)

---

## 4. 데이터 모델

```python
@dataclass(frozen=True)
class ExpertSignal:
    expert_name: Literal["E_MACRO","E_FUND","E_NARR","E_EVENT","E_FFLOW","E_ESG","E_GFLOW","E_TIME"]
    ticker: str
    direction: Literal["LONG","SHORT","NEUTRAL"]
    net_score: float                # ±3 normalized
    confidence: float               # [0,1]
    archetype: Literal["impulse","continuation","contrarian","mixed"]
    basis: str
    sources: list[str]              # Bigdata MCP source URLs/IDs
    metadata: dict
    expires_at: datetime

@dataclass(frozen=True)
class Verdict:
    ticker: str
    action: Literal["STRONG_BUY","BUY","HOLD","SELL","STRONG_SELL"]
    conviction_w: float             # TITAN W값 [0, 3.5]
    target_price: float | None
    stop_price: float | None
    suggested_size_pct: float
    regime: str                     # 5단계
    time_T: float                   # 일목 수렴도
    edge_bps: float
    all_in_bps: float
    cost_passed: bool               # INV-001
    contributing_signals: list[ExpertSignal]
    next_trigger: str               # "외인 반전 + RSI<35 시 BUY 전환"
    evidence_hash: str              # hash chain
    git_commit: str
```

---

## 5. 불변식 (Invariants) — INV-GS

| ID | 불변식 |
|----|-------|
| **INV-GS-001** | `edge_bps ≥ 1.5 × all_in_bps` 미충족 시 verdict.cost_passed=False, BUY/STRONG_BUY 자동 강등 → HOLD |
| **INV-GS-002** | `find_companies` 결과는 영구 캐시; 재호출 금지 |
| **INV-GS-003** | E_NARRATIVE 가중치 ≤ 15% (LLM 폭주 방지) |
| **INV-GS-004** | regime∈{CRASH} 시 모든 신규 LONG verdict 강등 |
| **INV-GS-005** | 4 Expert 이상 동방향 시 가중치 0.80× 할인 (anti-herd) |
| **INV-GS-006** | 모든 verdict는 hash-chained NDJSON에 append; 누락 시 다음 verdict 거부 |
| **INV-GS-007** | DEFCON STOP 시 STRONG_BUY/BUY 차단 (sticky) |
| **INV-GS-008** | E_TIME T값 ≥ 1.5 충족 + V값 ≥ 1.0 → conviction_w 1.2× 보너스 |
| **INV-GS-009** | bigdata_search 결과는 timestamp 14일 내 + sentiment 양극단만 채택 |
| **INV-GS-010** | 동일 (ticker, date, seed) → 동일 verdict (결정론) |

---

## 6. 8 Sprint 구현 로드맵

| Sprint | 산출물 | DoD |
|--------|-------|-----|
| **S1** | Data Plane | bigdata_client.py 6 tools 래퍼 + entity_map 캐시 + budget; smoke 통과 |
| **S2** | E_MACRO + E_FUNDAMENTAL + E_EVENT 3 Expert | predict AAPL → 3 ExpertSignal 출력 |
| **S3** | E_NARRATIVE + E_FUND_FLOW + Gating v0 | Verdict (raw) 생성 |
| **S4** | Cost-Gate + W값 + Verdict v1 | INV-GS-001 통과; 5단계 action |
| **S5** | E_TIME + E_ESG + E_GLOBAL_FLOW (8 Expert 완성) | screen sp500 동작 |
| **S6** | Risk Layer (DEFCON + Blacklist + JURY) | DEFCON 시나리오 테스트 |
| **S7** | Replay + Hindcast + Evidence Chain | 90일 결정론 리플레이 |
| **S8** | macOS Menubar + Dashboard + Telegram | 60초 폴링 + localhost:7100 + 3 채널 |

---

## 7. 차별화 포인트

| 차원 | MOET | TITAN | BETASTRIKE | v3_replay | **GLOSTAT** |
|------|------|-------|-----------|-----------|------------|
| 시장 범위 | KRX | KRX | KRX | KRX | **글로벌 (US/EU/JP/KR/EM)** |
| 시간축 | 인트라데이 | 3-5년 | 인트라데이 | 인트라데이 | **인트라데이+스윙+장기** |
| 데이터 | 자체 수집 | yfinance+Naver | KRX 틱 | Parquet 틱 | **Bigdata MCP 1차** |
| 신호 다양성 | 5 Expert | 7 Engine | 2 Expert | 4 신호 | **8 Expert** |
| 이벤트 인식 | 약함 | 약함 | 없음 | 없음 | **events_calendar 1급 시민** |
| ESG/노동 | 없음 | 없음 | 없음 | 없음 | **E_ESG (workforce in/out)** |
| 실행 결합도 | 강(주문) | 중(DCA) | 강(주문) | 강(주문) | **약(verdict only) — 실행은 외부 위임** |

→ **GLOSTAT 정체성: 주문 실행 도구가 아닌 증거기반 변동 예측 인텔리전스 도구.** MOET/BETASTRIKE는 GLOSTAT verdict를 소비하는 하류 시스템.
