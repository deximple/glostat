# GLOSTAT — Global Cascade Intelligence Engine
## v0.4 — 2026-04-28 — **Validation-First MVP** (Opus 4.7 Final Synthesis)

> **합성 원칙 (사용자 지시: "다수 = known alpha, 소수의견 무시 금지")**:
> - 10명 전문가 투표: SHIP 0 / FIX_THEN_SHIP 7 / RECONSIDER_PREMISE 3
> - 다수파(7) FIX_THEN_SHIP은 "patch and ship" — known alpha로 처리, 인프라/플러밍에 반영
> - 소수파(3) RECONSIDER (E3 Behavioral, E6 Red Team, E10 Contrarian) → **3 voices converge: scope/validation/priority 모두 틀렸다**
> - 10개 minority insight 모두 INV-GS로 승격
> - **결론: v0.3은 backtest theatre + scope creep. v0.4는 dramatic refactor.**

---

## 0. v0.3 → v0.4 합성 결정 (투명성)

### 0.1 전문가 투표 분포

| # | Expert | Verdict | 5단어 핵심 |
|---|--------|---------|-----------|
| E1 | Microstructure Quant | FIX_THEN_SHIP | Horizon conflation + IRF + multipliers |
| E2 | Macro Strategist | FIX_THEN_SHIP | Regime shift lag matters (day 21) |
| **E3** | **Behavioral** | **RECONSIDER** | **신호 역방향, 속도, 낡은 정보** |
| E4 | Risk Manager | FIX_THEN_SHIP | Portfolio-level constraints mandatory |
| E5 | MLOps | FIX_THEN_SHIP | Snapshot broker non-negotiable |
| **E6** | **Red Team** | **RECONSIDER** | **Cascade idea valid, 검증 부재** |
| E7 | UX | FIX_THEN_SHIP | 기술이 UX를 이길 수 없다 |
| E8 | LLM | FIX_THEN_SHIP | Meta-adjudicator + prompt versioning |
| E9 | Compliance | FIX_THEN_SHIP | Verdict 규제 게이트 필수 |
| **E10** | **Contrarian** | **RECONSIDER** | **Cascade before fundamentals: Wrong order** |

### 0.2 3 RECONSIDER 수렴 메시지 (alpha 신호)

세 독립적 시각이 같은 결론에 도달:
- **E3**: 신호 모델이 PEAD-focused, pre-event narrative buildup 60일 사각지대, sell-the-news 미반영
- **E6**: Apple iPhone 예시는 backtest theatre, edge multiplier는 plug values, supply chain LLM hallucination 검증 없음, 시장 경계는 MVP 속도를 꺾음
- **E10**: Cascade는 연구이지 사용자 가치 아님, 9 Expert 중 5-6은 IC 0에 수렴 예정, scope creep (v0.1→v0.2→v0.3 한 세션)

→ **수렴 진단: scope-first가 아닌 validation-first가 필요. Cascade는 fundamentals 검증 후로 미룸.**

### 0.3 10개 Minority Insight 인벤토리 (모두 INV-GS로 승격)

| Expert | Minority Insight | v0.4 반영 |
|--------|------------------|----------|
| E1 | BETASTRIKE AUC 0.611 OOS calibration decay risk | INV-GS-031: 상속 가중치 ≤10% pending retraining |
| E2 | Regime shift confirms day 21, 20일 false signal hell | INV-GS-027: TRANSITION regime + 21일 confirmation |
| E3 | PEAD vs cascade drift, pre-event narrative buildup 60일 | INV-GS-030: E_NARRATIVE lookback 60d + crystallization multiplier |
| E4 | Cascade upside vs current_loss = expected_pnl 재정의 | INV-GS-028: Verdict.expected_pnl = upside − current_loss |
| E5 | NDJSON hash chain breaks during vol spike → Merkle tree | INV-GS-022: parquet shards + Merkle tree |
| E6 | Market boundary 복잡성 = MVP 속도 꺾음, US만 시작 | v0.4 Sprint 1-4: US only, markets.yaml 80개 → 2개 (XNAS+XNYS) |
| E7 | Dissent = alpha, disagreement weight UI 필수 | INV-GS-029: Verdict.disagreement_weight 1급 시민 |
| E8 | Prompt version hash 없으면 INV-GS-010 = fiction | INV-GS-023: prompt_versions{expert: sha256} 필수 |
| E9 | RavenPack EU Database Directive + chained licensing | INV-GS-024: Telegram broadcast 금지, personal-use only |
| E10 | "Cascade는 연구 프로젝트, 사용자 가치 아님" | Cascade를 v2.0으로 이연, MVP에서 완전 제외 |

---

## 1. 아키텍처 리팩터 요약 (v0.3 → v0.4)

| 항목 | v0.3 | v0.4 | 근거 |
|-----|------|------|-----|
| **MVP 범위** | 9 Expert × 80 시장 × Cascade × per-market cost | **3 Expert × US (XNAS+XNYS) × No Cascade × USD only** | E10, E6, E3 |
| **Sprint 일정** | 8주 모두 빌드 | **4주 빌드 + 1주 검증 게이트 → kill or continue** | E10, E6 |
| **Cascade Graph** | MVP 핵심 차별화 | **v2.0 research mode (MVP 통과 후 A/B 테스트)** | E10, E6 |
| **시장 경계 시스템** | UAID + 80개 markets.yaml + cross-listing | **MVP는 XNAS+XNYS 하드코딩, UAID 전체 시스템은 Phase 2** | E6, E10 |
| **신호 검증** | 암묵적 (calibration is "future work") | **Sprint 0 first-class. IS/OOS split, hindcast DB, 90d backtest** | E1, E6 |
| **Kill criteria** | 부재 | **명시 (Sharpe < 0.8 @ 6mo = shutdown)** | E10 |
| **Edge multipliers** | 플러그 상수 | **검증 테이블 필수, "never tested" 엣지는 weight=0** | E6, E1 |
| **Meta-adjudicator** | 암시 | **Sprint 0에서 모델/프롬프트/스키마/가드레일 전체 명세** | E8 |
| **Snapshot broker** | 부재 | **Sprint 0 필수 — INV-GS-010 redeem** | E5 |
| **Prompt versioning** | 부재 | **Sprint 0 — 모든 LLM 호출에 sha256 prompt hash** | E8 |
| **Compliance gate** | 부재 | **Sprint 0 — personal-use disclaimer, Telegram broadcast 금지** | E9 |
| **Portfolio risk** | 종목별만 | **Sprint 1.5 — CVaR_95 + Herfindahl + sector cap** | E4 |
| **Verdict horizon** | 인트라데이+스윙+장기 단일 객체 | **단일 horizon (swing 1d-30d) 명시** | E1 (category error) |
| **Dissent surface** | 부재 | **Sprint 3 — disagreement_weight 1급 시민** | E7 |
| **Regime states** | 5단계 | **6단계 (5 + TRANSITION) + vol regime 직교 축** | E2 |

---

## 2. Sprint 0 — Validation & Infrastructure (Week 1, 사전조건)

**원칙: 모든 feature 빌드 전에 scaffolding 먼저.**

| 산출물 | DoD | 근거 |
|-------|-----|-----|
| **Snapshot Broker** (S3 + parquet + DynamoDB index + Merkle tree) | 동일 (UAID, edge_type, ts) → 동일 hash 검증 | E5, INV-GS-022 |
| **Prompt Version Hash 시스템** | 모든 LLM 호출이 prompt sha256 + version 자동 기록 | E8, INV-GS-023 |
| **Meta-adjudicator 명세** | model_routing.yaml + prompt_template + JSON schema + 4가지 hallucination guard | E8 |
| **Validation Harness** | 90일 hindcast 자동 실행, IS/OOS split, Sharpe/IC/AUC 출력 | E1, E6 |
| **Compliance Gate Library** | personal_use_only=True 기본, Telegram 송신 시 ComplianceError raise | E9, INV-GS-024 |
| **Hindcast DB** | 2020-2026 Bigdata MCP 응답 스냅샷 저장 (S3) | E5, E6 |
| **Kill Criteria 모니터** | Sharpe/maxdd 일일 모니터링, threshold 위반 시 alert | E10 |

**Sprint 0 통과 없이 Sprint 1 시작 금지.**

---

## 3. Sprint 1-3 — 3-Expert MVP (Weeks 2-4)

### 3.1 3 Expert (검증된 신호만, E10 권고)

| Expert | 1차 도구 | 가중치 캡 | 근거 |
|--------|---------|---------|-----|
| **E_FUNDAMENTAL** | `bigdata_company_tearsheet` (Public, quarter) — PER/ROE/EPS surprise/fwd estimate | 40% | TITAN B7 검증, 가장 stable |
| **E_FUND_FLOW** | `bigdata_company_tearsheet` (fund_trends 섹션) — 기관 D5/D20 순매수, 옵션 활동 | 35% | TITAN B4 검증 (REVERSAL_BUY 60.3%) |
| **E_TIME** | `bigdata_events_calendar` (어닝/컨퍼런스 ±14d) + 일목 (외부 OHLCV) | 25% | TITAN B2 검증 |

### 3.2 단일 horizon: Swing (1d-30d only)

E1 minority insight (horizon conflation = category error) 반영:
- **No intraday** (별도 인프라 필요, BETASTRIKE/v3_replay 영역)
- **No long-term** (TITAN 영역, 기존 도구로 충분)
- **Swing only**: 1일 ~ 30일 보유 가정. all_in_bps와 alpha decay가 같은 스케일.

### 3.3 단일 시장: XNAS + XNYS

E6, E10 minority insight 반영:
- markets.yaml에 2개만: XNAS, XNYS
- USD only, no FX conversion
- Bare ticker 허용 (US 단일 시장이므로 모호성 없음, UAID는 Phase 2)
- 종목 universe: S&P 500 (500 종목), 후 S&P 1500으로 확장

### 3.4 Verdict v1 (simplified)

```python
@dataclass(frozen=True)
class Verdict:
    ticker: str                       # bare ticker (US only, MVP)
    action: Literal["BUY","HOLD","SELL"]   # 5단계 → 3단계 (E1 horizon discipline)
    conviction_w: float               # [0, 3.5] TITAN W값
    target_price: float | None
    stop_price: float | None
    suggested_size_pct: float
    horizon_days: int                 # explicit, 1-30
    edge_bps: float
    all_in_bps: float                 # XNAS=0.6bps fee + 0.24bps SEC sell
    cost_passed: bool
    expected_pnl_bps: float           # = upside − current_loss (E4 minority)
    disagreement_weight: float        # [0,1] 1=full consensus, 0=split (E7 minority)
    contributing_signals: list[ExpertSignal]  # 3 signals
    next_trigger: str
    evidence_hash: str                # Merkle leaf (E5)
    prompt_versions: dict[str, str]   # {expert: sha256} (E8 minority)
    git_commit: str
    user_profile_hash: str            # personal-use audit (E9)
```

---

## 4. Sprint 4 — Validation Gate (Week 5) — KILL OR CONTINUE

**의사결정 게이트. 통과 못하면 즉시 종료.**

### 4.1 Pass criteria (모두 충족 필수)

| 메트릭 | Threshold | 측정 방법 |
|-------|----------|---------|
| **90일 hindcast Sharpe** | ≥ 0.8 | Validation Harness, IS/OOS split 70/30 |
| **OOS degradation** | ≤ 30% (e.g., IS Sharpe 1.2 → OOS ≥ 0.84) | 동일 |
| **Verdict 결정론** | 100% (snapshot replay 검증) | INV-GS-010 + INV-GS-022 |
| **Hindcast AUC (BUY signals)** | ≥ 0.60 | BETASTRIKE C1 baseline와 동등 이상 |
| **Cost-passed verdict 비율** | ≥ 30% (너무 적으면 cost 가정 잘못, 너무 많으면 cost 너무 관대) | 90일 verdict log |

### 4.2 게이트 결과

- **PASS**: Phase 2로 진입 (확장)
- **FAIL**: SHUTDOWN (E10 권고). v0.5 처음부터 재설계.
- **AMBIGUOUS** (일부만 통과): 90일 더 hindcast + parameter tuning, 두 번째 게이트 시도. 두 번째도 ambiguous면 shutdown.

---

## 5. Phase 2 (Weeks 6-12) — 통과 시에만

### 5.1 Expert 추가 (3 → 6)

| 추가 Expert | 1차 도구 | 우선순위 근거 |
|-----------|---------|--------------|
| **E_MACRO** (확장: vol regime + TRANSITION state) | `bigdata_country_tearsheet` + `bigdata_market_tearsheet` | E2 minority (regime shift lag) 반영 |
| **E_NARRATIVE** (확장: 60d window + crystallization + contrarian sub-expert) | `bigdata_search` (smart) | E3 minority 핵심 반영 |
| **E_EVENT** | `bigdata_events_calendar` (±14d, pre/post earnings window) | 기본 |

### 5.2 시장 확장: US → US + KR (E6 점진 권고)

- markets.yaml: XNAS + XNYS + XKRX + XKOS (4개)
- UAID 도입 (단, 4개 시장 한정)
- Cross-listing resolver (단, 주요 ADR/GDR 50개 한정)

### 5.3 Portfolio Risk Layer (E4 minority)

- L4.5 추가: portfolio_constraints
  - cvar_95_pct ≤ 3.5%
  - herfindahl_max 0.12
  - sector_cap (semis 25%, single sector 30%)
  - country_cap (TW 15%, CN 10%)
  - single_position_max 8% (Half-Kelly cap)

### 5.4 Regime 확장 (E2 minority)

```yaml
regime_axes:
  primary: [BULL_S, BULL_W, BEAR_S, BEAR_W, CRASH, TRANSITION]   # 6단계
  vol: [LOW_VOL, NORMAL, HIGH_VOL, EXTREME]                      # 4단계
  carry: [RISK_ON, RISK_OFF, UNWIND]                            # 3단계
  rates: [RISING, FALLING, TERMINAL, INVERTING]                 # 4단계
```

조합 = 6×4×3×4 = 288 셀이지만 실제 점유 = ~30 셀. 각 셀별로 expert weight 조정.

---

## 6. Phase 3 (Weeks 13-24) — Cascade Research Mode (E10 핵심 양보)

E10의 "cascade는 연구"라는 통찰을 정면 수용:
- Cascade Graph + Propagation Engine은 **별도 research mode 모듈** (`/glostat/research/cascade/`)
- **Production verdict에 영향 주지 않음** (read-only output)
- A/B 테스트: control (3-6 Expert MVP) vs treatment (+ E_CASCADE 가중치)
- **A/B 결과 Sharpe lift > 0.2** 시에만 production promote
- 실패 시 cascade는 "intellectual artifact"로만 보존, 사용자에 노출 안함

이는 v0.3 cascade 가설에 대한 **정직한 검증 메커니즘**.

---

## 7. v0.4 Invariants 추가 (10개 minority insight 승격)

| ID | 불변식 | 출처 |
|----|-------|-----|
| INV-GS-022 | 모든 Bigdata MCP 호출은 Snapshot Broker에 기록; replay는 snapshot 우선 | E5 |
| INV-GS-023 | 모든 LLM 호출은 prompt_versions[expert]: sha256 명시. 누락 verdict 거부 | E8 |
| INV-GS-024 | Telegram broadcast 영구 금지. CLI/Dashboard에서 personal-use disclaimer 매 verdict | E9 |
| INV-GS-025 | Portfolio CVaR_95 ≤ 3.5%, Herfindahl ≤ 0.12, single_position ≤ 8% | E4 |
| INV-GS-026 | 새 Expert 출시 전 90d hindcast + IS/OOS split + AUC ≥ 0.60 | E1, E6 |
| INV-GS-027 | Regime TRANSITION state + 21일 confirmation window. 5→6 state | E2 |
| INV-GS-028 | Verdict.expected_pnl_bps = cascade_upside − current_loss (disposition 방어) | E4 |
| INV-GS-029 | Verdict.disagreement_weight 필수, < 0.5 시 UX에 "전문가 분산 큼" 경고 | E7 |
| INV-GS-030 | E_NARRATIVE lookback 60d + crystallization multiplier + contrarian sub-expert | E3 |
| INV-GS-031 | BETASTRIKE 상속 calibration 가중치 ≤ 10% (OOS retraining 전까지) | E1 |
| INV-GS-032 | Edge multiplier 검증 테이블 필수. "never tested" 엣지는 weight=0 | E6 |
| INV-GS-033 | Sprint 4 게이트 FAIL 시 자동 shutdown. 우회 금지 | E10 |
| INV-GS-034 | Cascade Graph는 research/ 격리. production verdict 영향 0 | E10, E6 |
| INV-GS-035 | RavenPack ToS 검증 완료 + 매월 재확인 | E9 |

(v0.1 INV-GS-001..010 + v0.2 011..016 + v0.3 017..021 + v0.4 022..035 = 총 35개)

---

## 8. v0.4가 명시적으로 NOT 하는 것 (scope discipline)

E10 권고 + 합성 결정에 따른 **금지 목록**:

- ❌ Cross-market cascade (Phase 2/3)
- ❌ 9 Expert 동시 빌드 (3 → 6 → 9 단계적)
- ❌ 80+ 시장 (US 2개 → US+KR 4개 → ...)
- ❌ Telegram broadcast (compliance gate)
- ❌ Order execution (verdict only, 실행은 외부)
- ❌ Intraday horizon (BETASTRIKE 영역)
- ❌ Long-term (3-5y) horizon (TITAN 영역)
- ❌ 사용자 다중 배포 (personal use only)
- ❌ macOS Menubar Phase 1 (CLI + localhost dashboard 만, Phase 2 menubar)

---

## 9. Kill Criteria (E10 minority insight 정착)

명시적 종료 조건:

| 조건 | 액션 |
|------|------|
| Sprint 4 validation gate FAIL | 즉시 shutdown |
| 6mo 실시간 운영 Sharpe < 0.8 | 즉시 shutdown |
| Hindcast OOS degradation > 30% | 즉시 freeze + 90d 추가 hindcast |
| Compliance issue 발생 | 즉시 pause + 법무 검토 |
| Bigdata MCP 가격 3배 이상 인상 | data 다원화 검토 (SEC EDGAR + Polygon 병렬) |
| Sprint 0 → Sprint 1 사이 신규 사용자 통찰로 v0.5 scope creep 시도 | **거부**. v0.4 완료 후 v0.5 검토 |

마지막 항목은 자기-방어: v0.1→v0.3 1세션 scope creep 패턴(E10 지적) 재발 방지.

---

## 10. 의사결정 트리 (Opus 4.7 최종)

```
Sprint 0 (Week 1)
  └─ Validation harness + Snapshot broker + Compliance gate + Meta-adjudicator spec
     └─ Sprint 1-3 (Weeks 2-4): 3 Expert × US × Swing
        └─ Sprint 4 Gate (Week 5)
           ├─ PASS → Phase 2 (Weeks 6-12)
           │           ├─ Expert 3→6, Market US→US+KR, Portfolio risk, Regime 확장
           │           └─ Phase 2 게이트
           │              ├─ PASS → Phase 3 (Weeks 13-24)
           │              │           └─ Cascade research mode A/B test
           │              │              ├─ Sharpe lift > 0.2 → cascade promote
           │              │              └─ Sharpe lift ≤ 0.2 → cascade kept as research only
           │              └─ FAIL → Phase 2 redesign or shutdown
           ├─ AMBIGUOUS → 90d 추가 hindcast + retry (1회만)
           └─ FAIL → SHUTDOWN
```

각 게이트는 **명시적 통과 기준**과 **자동 shutdown 조건** 보유.

---

## 11. Bigdata MCP 활용 (v0.4 simplified)

MVP에서 사용하는 도구:
- `find_companies` — entity_id 영구 캐시 (Sprint 0)
- `bigdata_company_tearsheet` — E_FUNDAMENTAL + E_FUND_FLOW 1차
- `bigdata_events_calendar` — E_TIME 보조 (어닝/컨퍼런스)

Phase 2 추가:
- `bigdata_country_tearsheet` — E_MACRO
- `bigdata_market_tearsheet` — E_MACRO 보조 + regime
- `bigdata_search` — E_NARRATIVE (smart, 60d window)

Phase 3 추가:
- `bigdata_search` (filings) — Cascade graph extraction (research only)

**비용 가시화**: Sprint 0에 cost audit. fast/smart 단가 측정 → Phase 별 월 예산 책정.

---

## 12. Opus 4.7 합성 메시지

**v0.3은 야심적이고 지적으로 매력적이지만 다음 3가지 점에서 위험했음**:

1. **Validation 부재** (E6): 28개 차용 아이디어 중 어느 것이 실제 alpha인지 검증 없이 모두 통합
2. **Scope creep 한 세션 내 3회** (E10): v0.1 → v0.2 → v0.3 사용자 통찰마다 차원 추가 — 패턴은 지속
3. **Cascade가 fundamentals 검증을 우회** (E10, E6, E3): "글로벌 인과 사슬"이라는 화려한 차별화가 "단일 종목 verdict가 정확한가?"라는 기본 질문을 가림

**v0.4는 정반대**:
- **Validation-first**: Sprint 0이 가장 길고 중요
- **Scope-discipline**: 명시적 금지 목록 + Kill criteria
- **Bottom-up**: 3 Expert × 1 시장 → 검증 → 확장. cascade는 fundamentals 통과 후 research mode

이는 **MOET의 INV-001 (cost-first edge validation) 정신을 plan 자체에 적용**한 것:
- v0.3은 "edge가 있다"는 가정 위에 cost를 계산
- v0.4는 "edge가 있는지"를 먼저 검증 (Sprint 4 gate), 통과 시에만 확장

**핵심 원칙**: 빌드 비용 < 검증 비용 < 잘못된 결정 비용. 후자 두 개를 줄이는 것이 v0.4.

---

## 13. 즉시 다음 액션

1. **사용자 승인 게이트**: v0.4가 (a) Sprint 0 시작 (b) v0.4 더 수정 (c) 다른 방향 중 어느 것?
2. 승인 시 즉시:
   - `/Applications/GLOSTAT/CLAUDE.md` 작성 (v0.4 요약 + INV-GS-001..035)
   - `pyproject.toml` (uv, Python 3.14, MOET 패턴)
   - `configs/markets.yaml` 2개 시장만 (XNAS, XNYS)
   - Sprint 0 스캐폴딩: snapshot_broker.py, prompt_versioning.py, compliance_gate.py, validation_harness.py
3. Sprint 0 완료 시 PR + 사용자 리뷰 → Sprint 1 진입

---

**v0.4 작성 완료. 10인 합의(7) + 소수 프리미엄(3) 모두 반영.**
**Plan v0.1 + v0.2 + v0.3 + v0.4 모두 ./docs/ssot/ 보존 (감사 + 학습 자료).**
