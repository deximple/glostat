# GLOSTAT — Global Cascade Intelligence Engine
## v0.3.1 — 2026-04-28 — **Patched Cascade (Majority-First Synthesis)**

> **합성 원칙 (사용자 지시 의도적 반전 적용)**:
> - 사용자가 제시한 "majority direction = known alpha" 를 정면 적용 → 다수 7명(FIX_THEN_SHIP) 권고를 1차 신호로 신뢰
> - 소수 3명(RECONSIDER)은 "정보의 다양성"으로만 부분 수용 — 전체 비전 재설계의 트리거가 아니라, **incremental patch의 우선순위 조정**으로만 사용
> - v0.3의 cascade + 9 Expert + 글로벌 시장 비전을 보존하는 것이 minority의 "scope 축소" 권고보다 무겁다 (사용자가 직접 두 차례 통찰 #1·#2로 cascade·market boundary를 추가했기 때문)
> - **결론: dramatic refactor 대신 surgical patches.**

---

## 0. 합성 결정 (transparency)

### 0.1 다수파 우선의 근거

10명 중 7명이 FIX_THEN_SHIP에 동의했다는 것은 **"v0.3의 골격은 옳고, 디테일을 보강하라"는 합의 신호**다. 사용자가 명시한 "majority = known alpha" 원칙을 그대로 적용하면, RECONSIDER 3명의 권고(MVP 축소·검증 우선·cascade 이연)는 다수파 7명의 patch 권고(IRF·portfolio risk·snapshot broker·UX dissent·meta-adjudicator·compliance)와 **양립 가능한 layer**로 흡수해야 한다. 두 신호가 충돌할 때(예: cascade를 전체 제외 vs cascade 유지) 다수파 신호가 우선한다.

### 0.2 v0.4 (Opus 합성) 와의 의도적 분기

v0.4는 minority 3명(E3·E6·E10)을 "수렴 알파(convergent alpha)"로 재가중하여 cascade 자체를 v2.0으로 이연했다. 이는 minority overweighting — 사용자 지시의 **반대 방향**이다. v0.3.1은 동일한 patch들(snapshot broker·prompt versioning·portfolio risk 등)을 수용하되, **MVP scope·cascade·multi-market 비전은 유지**한다.

### 0.3 부분 수용된 minority 3명

- **E3 (Behavioral)**: 60d narrative window + crystallization multiplier 즉시 도입. 단, 3 sub-experts 분리(PEAD/contrarian/sentiment)는 v0.5로 이연 — MVP에 추가 복잡도를 주지 않는다.
- **E6 (Red Team)**: edge multiplier 검증 테이블 의무화 (INV-GS-026). 단, "cascade 자체를 defer하라"는 권고는 거부 — cascade는 v0.3.1의 **차별화 핵심**이며, 이 차별화 없이 MVP는 "또 하나의 fundamental 도구"로 전락한다.
- **E10 (Contrarian)**: kill criteria 명시(§4) + Sprint 4 게이트 신설. 단, scope 축소(3 Expert × US only)는 거부 — 8주 → **10주로 완만히 확장**하여 검증 시간을 확보하되, 9 Expert × cascade 비전은 유지.

---

## 1. v0.3 → v0.3.1 핵심 변화 (10 patches 표)

| # | 영역 | v0.3 상태 | v0.3.1 patch | 출처 |
|---|------|----------|-------------|------|
| P1 | Verdict horizon | 단일 객체에 intraday/swing/long 혼재 | **horizon stratification** — Verdict.horizon ∈ {intraday, swing, long}, 각 별도 IC/Sharpe 추적 | E1 |
| P2 | IRF calibration | n 미지정 | **IRF threshold n ≥ 3 minimum** + rolling 90d window | E1 |
| P3 | Edge multipliers | 플러그 상수 | **validation table** (`configs/edge_multipliers_validated.yaml`), `validated=false` 엣지는 weight × 0.5 페널티 | E1, E6 |
| P4 | Regime states | 5단계 단일 축 | **6단계 (TRANSITION 추가) + vol regime 직교 축**, event-driven E_MACRO refresh | E2 |
| P5 | Portfolio risk | 종목 단위만 | **L4.5 신설** — CVaR_95 ≤ 3.5%, Herfindahl ≤ 0.12, sector cap 30%, single position ≤ 8% | E4 |
| P6 | Replay 무결성 | NDJSON hash chain | **Snapshot broker (S3 + parquet) + Merkle tree** + per-market rate limit budget | E5 |
| P7 | Verdict UX | 객체만 정의 | **headline card + dissent surface + percentile scoring** (1급 시민 disagreement_weight) | E7 |
| P8 | Meta-adjudicator | 암시 | **full spec** — model_routing.yaml + prompt sha256 hash + JSON schema + cost model | E8 |
| P9 | Compliance | 부재 | **Suitability gate + RavenPack ToS 검증 + per-jurisdiction disclaimer**; Telegram broadcast 금지 | E9 |
| P10 | Narrative window | 일반화 | **60d window + crystallization multiplier** (3 sub-experts 분리는 v0.5로 이연) | E3 부분 |

**모든 patch는 v0.3 골격(9 Expert × 80 markets × cascade × UAID × markets.yaml) 위에 incremental layer로 추가됨.** 어느 것도 cascade 또는 multi-market scope를 축소하지 않는다.

---

## 2. 신규 Invariants — INV-GS-022..030 (9개)

minority 권고 중 **다수파의 patch 흐름과 양립 가능한 것만** 승격. v0.4가 승격한 14개 INV 중 cascade 차단·scope 축소 관련 항목(INV-GS-033, 034)은 거부.

| ID | 불변식 | 출처 |
|----|-------|-----|
| INV-GS-022 | 모든 Bigdata MCP 호출은 Snapshot Broker(S3 + parquet + Merkle tree)에 기록; replay는 snapshot 우선 | E5 |
| INV-GS-023 | 모든 LLM 호출은 `prompt_versions[expert]: sha256` 명시. 누락 verdict 거부 | E8 |
| INV-GS-024 | Telegram broadcast 영구 금지. CLI/Dashboard에서 personal-use disclaimer + per-jurisdiction 면책 매 verdict | E9 |
| INV-GS-025 | Portfolio CVaR_95 ≤ 3.5%, Herfindahl ≤ 0.12, sector_cap 30%, single_position ≤ 8% (L4.5) | E4 |
| INV-GS-026 | 모든 edge multiplier 및 신규 Expert는 90d hindcast + IS/OOS split 통과 후 production 가중치 부여; 미검증 항목은 0.5 페널티 | E1, E6 |
| INV-GS-027 | Regime은 6단계 primary axis + vol regime 직교 축. TRANSITION 진입 시 21일 confirmation window | E2 |
| INV-GS-028 | Verdict.horizon ∈ {intraday, swing, long} 명시. horizon별 별도 IC/Sharpe 추적, 통계 합산 금지 | E1 |
| INV-GS-029 | Verdict.disagreement_weight 필수 1급 시민. < 0.5 시 UX에 "전문가 분산 큼" 헤드라인 카드 경고 | E7 |
| INV-GS-030 | E_NARRATIVE lookback 60d + crystallization multiplier. 3 sub-experts 분리는 v0.5로 이연 | E3 부분 |

(v0.1 INV-GS-001..010 + v0.2 011..016 + v0.3 017..021 + v0.3.1 022..030 = **총 30개**)

---

## 3. 10주 Sprint 로드맵 (v0.3 8 sprint + S0 인프라 + S4.6 portfolio + S6.6 UX)

| Sprint | Week | 산출물 | v0.3 대비 |
|--------|------|-------|----------|
| **S0 (NEW)** | 1 | Snapshot broker + Merkle tree + prompt version hash + meta-adjudicator spec + compliance gate library | E5·E8·E9 patch 인프라 사전 배치 |
| S1 | 2 | Data Plane (bigdata_client, entity_map, per-market rate budget) | (그대로) |
| S1.5 | 2 | Market Boundary System (markets.yaml 80개, UAID, cross-listing) | (그대로) |
| S2 | 3 | E_MACRO + E_FUNDAMENTAL + E_EVENT + **regime 6단계 + vol 직교 축** | P4 통합 |
| S3 | 4 | E_NARRATIVE (60d + crystallization) + E_FUND_FLOW + Gating v0 | P10 통합 |
| S4 | 5 | Cost-Gate (per-market) + W값 + Verdict v1 + **horizon stratification** | P1 통합 |
| **S4.5** | 6 | Cascade Graph 오프라인 빌더 (UAID, market 분리) + **edge multiplier validation table** | P3 통합 |
| **S4.6 (NEW)** | 6 | Portfolio Risk Layer L4.5 — CVaR_95 + Herfindahl + sector cap | E4 patch 정착 |
| S5 | 7 | E_TIME + E_ESG + E_GLOBAL_FLOW | (그대로) |
| S5.5 | 8 | Propagation Engine + E_CASCADE (TZ-aware, currency-aware) | (그대로 — cascade 보존) |
| S6 | 8 | Risk Layer (DEFCON, Blacklist, JURY) | (그대로) |
| S6.5 | 9 | 실시간 이벤트 → cascade alert (시장별 폴링) | (그대로) |
| **S6.6 (NEW)** | 9 | Verdict UX — headline card + dissent surface + percentile scoring | E7 patch 정착 |
| S7 | 10 | Replay + Hindcast + Evidence Chain (Sprint 4 게이트 검증) | E10 부분 통합 |
| S8 | 10 | macOS Menubar + Dashboard (cross-market cascade 시각화) + CLI (Telegram 제외) | E9 patch (Telegram 제거) |

**총 10주 (v0.3 8주 + S0/S4.6/S6.6 추가). 8주 → 10주는 minority의 "검증 시간 확보" 요구를 부분 수용한 결과이지만, scope는 보존.**

---

## 4. Kill Criteria (E10 부분 수용)

scope 축소는 거부했지만, **명시적 종료 조건은 도입**한다 — v0.3 부재의 약점을 보완하는 가장 저렴한 patch.

| 조건 | 액션 |
|------|------|
| Sprint 7 hindcast Sharpe < 0.7 (수정: 0.8 → 0.7, 9 Expert 합성의 어려움 반영) | freeze + 90d 추가 hindcast → 재시도 1회 |
| 6mo 실시간 운영 Sharpe < 0.6 | shutdown + 회고 |
| Hindcast OOS degradation > 35% | 해당 Expert 가중치 0, 90d retrain |
| Compliance issue 발생 | 즉시 pause + 법무 검토 |
| Bigdata MCP 가격 3배 이상 인상 | data 다원화 (SEC EDGAR + Polygon 병렬) |
| Cascade Sharpe contribution < 0.05 (S7 측정) | E_CASCADE 가중치 캡 20% → 10%로 하향, 단 모듈 보존 |

**v0.4와의 결정적 차이**: v0.4는 게이트 FAIL 시 "shutdown 또는 v0.5 재설계"로 자동 종료. v0.3.1은 **각 Expert별 가중치 조정으로 graceful degradation** — cascade가 부진해도 시스템 전체를 죽이지 않는다.

---

## 5. 차별화 매트릭스 (v0.3 vs v0.3.1)

| 차원 | v0.3 | v0.3.1 patched |
|------|------|----------------|
| Cascade Graph | 핵심 차별화 | **보존** + edge multiplier 검증 의무 |
| Multi-market (80+ exchanges) | 핵심 차별화 | **보존** + per-market rate limit budget |
| UAID + markets.yaml | 핵심 차별화 | **보존** |
| 9 Expert | 모두 빌드 | **모두 빌드** + horizon별 별도 IC 추적 |
| Replay 무결성 | NDJSON hash chain | **Merkle tree + Snapshot broker** |
| Portfolio risk | 종목 단위 | **L4.5 추가** (CVaR + Herfindahl + sector cap) |
| Verdict UX | 데이터 객체 | **headline card + dissent surface** |
| Meta-adjudicator | 암시 | **full spec + prompt hash** |
| Compliance | 부재 | **suitability gate + per-jurisdiction disclaimer** |
| Kill criteria | 부재 | **graceful degradation** (Expert별 가중치 조정) |

**핵심 메시지**: cascade·multi-market·9 Expert는 v0.3의 **DNA**이며, v0.3.1은 이 DNA를 **수술이 아닌 보철**로 강화한다.

---

## 6. v0.4 vs v0.3.1 비교 매트릭스

| # | 항목 | v0.4 (Opus 합성, minority-first) | v0.3.1 (다수 합의, majority-first) | 사용자 의사결정 기준 | 추천 시나리오 |
|---|------|--------------------------------|-----------------------------------|--------------------|--------------|
| 1 | MVP 범위 | 3 Expert × US only × no cascade | 9 Expert × 80 markets × cascade | 위험 회피 vs 비전 보존 | v0.4: 보수적 PM·1인 개발자. v0.3.1: 차별화 우선 |
| 2 | Sprint 일정 | 4주 빌드 + 1주 게이트 → kill or continue | 10주 (S0 + 8 sprint + S4.6/S6.6) | 빠른 검증 vs 완전한 비전 첫 출시 | v0.4: 검증 사이클 우선. v0.3.1: 기능 완성도 우선 |
| 3 | Cascade Graph | v2.0 research mode로 이연, A/B 통과 시 promote | S5.5 production 포함, 가중치 캡 20% 유지 | "cascade가 검증된 alpha인가?" 의심도 | v0.4: 의심 강함. v0.3.1: 가설 신뢰 |
| 4 | Multi-market | XNAS+XNYS 2개 → US+KR 4개 → ... | 80개 markets.yaml 처음부터 | 글로벌 cascade 가설의 즉각성 | v0.4: 점진. v0.3.1: 즉시 |
| 5 | Validation | Sprint 0 + Sprint 4 게이트 강제 | Sprint 7 검증 + Expert별 graceful degradation | overfitting 우려 vs scope 보존 | v0.4: 우려 큼. v0.3.1: degradation으로 흡수 |
| 6 | Kill criteria | 자동 shutdown (게이트 FAIL = 종료) | Expert 가중치 0으로 강등 (graceful) | "전체 종료" 수용 가능성 | v0.4: 수용. v0.3.1: 부분 종료만 |
| 7 | 신규 INV-GS | 14개 (022..035, cascade 차단 포함) | 9개 (022..030, cascade 보존) | INV 누적 부담 | v0.4: 강한 가드레일. v0.3.1: 균형 |
| 8 | Compliance/UX patch | 동일 | 동일 | (차이 없음) | 양쪽 모두 채택 |
| 9 | Snapshot/prompt hash | 동일 (S0/S0) | 동일 (S0) | (차이 없음) | 양쪽 모두 채택 |
| 10 | Scope creep 방어 | "사용자 통찰로 v0.5 scope 시도 거부" 명문화 | 명문화 안 함 — 사용자 통찰 = 알파 가설 | 사용자 자기-방어 필요성 | v0.4: 1인 개발자 보호. v0.3.1: 사용자 권한 신뢰 |

---

## 트레이드오프 분석 (명시적)

**v0.4 장점**: 위험 낮음 — Sprint 4 게이트가 잘못된 가설을 4주 안에 차단; 검증 prior 강함; kill discipline이 자동 종료를 보장; 1인 개발자가 통제 가능한 표면적; minority 3명의 우려(backtest theatre·scope creep·cascade premature)를 정면 대응.

**v0.4 단점**: 차별화 지연 — cascade를 v2.0으로 미루면 v1 MVP는 "또 하나의 fundamental 도구"로 전락; 사용자가 직접 두 차례 통찰(cascade, market boundary)을 주입한 비전이 minority 3명에 의해 후순위로 밀림; multi-market·UAID 시스템이 Phase 2까지 잠들어 글로벌 cascade 가설(H1·H4)을 실시간 검증하지 못함; 8주 게이트 통과 후에도 cascade A/B는 Phase 3(13-24주)이라 12주 이상 지연.

**v0.3.1 장점**: 차별화 보존 — cascade는 시장에 나가는 즉시 v0.3의 핵심 가설(H1·H4)을 검증; 비전 일관성 유지(사용자 통찰이 1급 시민으로 남음); 다수파 7명 patch들(snapshot broker·portfolio risk·UX dissent·meta-adjudicator·compliance)을 전부 흡수하여 "v0.3은 검증 부재" 비판의 80%를 incremental하게 해소; 글로벌 cascade 가설을 10주 내 production에서 직접 검증 가능; graceful degradation으로 일부 Expert 실패가 시스템 전체 종료로 이어지지 않음.

**v0.3.1 단점**: 위험 높음 — 9 Expert × 80 markets × cascade를 동시에 빌드하면 어느 것이 alpha 원천인지 분리 곤란 (E6 비판의 핵심); 검증 후행 — Sprint 7까지 hindcast가 미뤄지면 잘못된 가설 위에 6주 빌드 후 발견; 1인 개발자에게 10주 × 9 Expert × Snapshot broker × Cascade × Compliance 동시 진행은 무거움; 사용자 통찰을 "알파 가설"로 신뢰하는 정책이 다음 통찰 #3·#4에서 또 차원 추가를 정당화할 위험.

**의사결정 기준**: 사용자가 (a) **글로벌 cascade 가설의 즉각적 production 검증**을 v0.3.1로, (b) **MVP를 통해 단일 시장 단일 종목 verdict의 정확성부터 증명**을 v0.4로 선택. 둘은 동일 patches를 수용하되 cascade·scope 처리에서 갈린다. v0.3.1은 minority overweighting을 거부한 결과이며, **다수파 = known alpha** 원칙을 사용자 자신이 명시한 만큼 v0.3.1이 그 원칙의 직접 적용임을 명기한다.

---

**v0.3.1 작성 완료. 다수 7명 patch 전면 수용 + 소수 3명 부분 수용 + cascade·9 Expert·80 markets 비전 보존.**
**Plan v0.1 + v0.2 + v0.3 + v0.4 + v0.3.1 모두 ./docs/ssot/ 보존 (양 갈래 모두 사용자 의사결정 자료).**
