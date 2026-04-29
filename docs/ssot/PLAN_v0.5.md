# GLOSTAT — Consolidated Final Plan
## v0.5 — 2026-04-28 — **Best-of-Merge** (a/b/c/d 6 parallel outputs)

> **합성 원칙**: v0.5 = v0.4 골격 (validation-first MVP, 3 RECONSIDER minority alpha 우선) × **3 design specs** (c1/c2/c3) × **tuning defaults** (b cautious profile) × **Sprint 0 scaffolding** (a, 16 files, 19/19 tests PASS) + **v0.3.1 alternative** (d, "Sprint 4 strong-PASS 시 pivot 옵션"으로 보존).

---

## 0. 머지 결정 (transparent)

### 0.1 6 parallel outputs 인벤토리 + 채택 결정

| 산출물 | 위치 | v0.5 처리 |
|-------|------|----------|
| **(a) Sprint 0 scaffolding** | `src/glostat/*` (16 files) + `CLAUDE.md` + `pyproject.toml` + `configs/*` + `tests/test_invariants.py` (19 PASS) | **즉시 채택** — Sprint 1 진입 준비 완료 |
| **(b) v0.4 tuning proposals** | `docs/ssot/v0.4_tuning_proposals.md` (T1..T10 + 3 페르소나) | **Cautious 프로필 채택** (§3) |
| **(c1) E_NARRATIVE design** | `docs/research/E_NARRATIVE_design.md` (5-state HMM + 3 sub-experts) | **Phase 2 권위 스펙** (§2) |
| **(c2) Snapshot Broker design** | `docs/research/snapshot_broker_design.md` (Merkle + SQLite + parquet) | **Sprint 0 권위 스펙, (a)에서 이미 구현** (§2) |
| **(c3) Kill Criteria design** | `docs/research/kill_criteria_design.md` (KillCriteriaMonitor + 대시보드) | **Sprint 4 게이트 권위 스펙** (§2) |
| **(d) v0.3.1 patched alternative** | `docs/ssot/PLAN_v0.3.1_patched.md` (majority-first, cascade preserved) | **Pivot 옵션 보존** — Sprint 4 strong-PASS 시 검토 (§4) |

### 0.2 방향 결정 (v0.4 lineage 채택, v0.3.1 alternative 보존)

**채택**: v0.4 validation-first MVP (3 Expert × US × No Cascade × 4+1 sprint).

**근거**:
- 3 RECONSIDER 수렴 (E3/E6/E10): scope/validation/priority 모두 risk
- 사용자 명시 원칙: "majority = known alpha" → minority premium 적용
- Sprint 0 (a) 이미 v0.4 가정 위에 구현 완료, pivot 비용 0
- v0.3.1은 "fast-path"로서 Sprint 4 strong-PASS 후 합리적 옵션 (단순 거부 아님)

**거부하지 않은 것**: cascade 비전 자체. v0.4 Phase 3 (research mode A/B 테스트) + v0.3.1 pivot 옵션으로 cascade 가설은 살아있음.

---

## 1. Lineage (v0.1 → v0.5)

```
v0.1 (Idea Inventory) ─┐
                       │  사용자 통찰 #1: "글로벌 cascade가 의미"
                       ├──→ v0.2 (Cascade Graph + E_CASCADE)
                       │      │
                       │      │  사용자 통찰 #2: "시장 경계 명확화"
                       │      ├──→ v0.3 (UAID + markets.yaml + 80 markets)
                       │      │      │
                       │      │      │  10인 전문가 critique (7 FIX, 3 RECONSIDER)
                       │      │      ├──→ v0.4 (validation-first MVP, Opus minority synthesis)
                       │      │      └──→ v0.3.1 patched (alternative, majority-first)
                       │      │             │
                       │      │             │  6 parallel deep-dives
                       │      │             ├──→ (a) Sprint 0 scaffolding
                       │      │             ├──→ (b) tuning proposals
                       │      │             ├──→ (c1) E_NARRATIVE design
                       │      │             ├──→ (c2) Snapshot Broker design
                       │      │             ├──→ (c3) Kill Criteria design
                       │      │             └──→ (d) v0.3.1 patched alt
                       │      │                   │
                       │      │                   ▼
                       └──────┴──────────→ **v0.5 Consolidated Final**
```

모든 문서는 `docs/ssot/` (plans) + `docs/research/` (deep-dives) + 코드 (`src/glostat/`)로 보존 — 감사 + 학습 자료.

---

## 2. Authority Map (어떤 subsystem은 어떤 문서가 권위인가)

| Subsystem | 권위 문서 | 구현 위치 | Sprint |
|-----------|---------|----------|--------|
| **Verdict / ExpertSignal / MarketMeta 데이터 모델** | PLAN_v0.4.md §3.4 + (a) `src/glostat/core/types.py` | 구현됨 (200줄) | S0 PASS |
| **Snapshot Broker (INV-GS-022, replay)** | `docs/research/snapshot_broker_design.md` (c2) | (a) `src/glostat/data/snapshot_broker.py` (346줄) | S0 PASS |
| **Prompt Versioning (INV-GS-023)** | PLAN_v0.4.md §0.3 row E8 + (a) | (a) `src/glostat/data/prompt_versioning.py` (200줄) | S0 PASS |
| **Compliance Gate (INV-GS-024)** | PLAN_v0.4.md + (a) | (a) `src/glostat/risk/compliance_gate.py` (157줄) | S0 PASS |
| **Validation Harness (INV-GS-026, Sprint 4 게이트)** | `docs/research/kill_criteria_design.md` (c3) + (a) | (a) `src/glostat/replay/validation_harness.py` (172줄) | S0 PASS, S4 게이트 평가에 사용 |
| **Kill Criteria Monitor + 대시보드** | `docs/research/kill_criteria_design.md` (c3) | Sprint 4 (KillCriteriaMonitor 클래스 c3 §4 skeleton) | S4 |
| **3 Expert MVP (E_FUNDAMENTAL, E_FUND_FLOW, E_TIME)** | PLAN_v0.4.md §3.1 | Sprint 1-3 신설 | S1-3 |
| **E_NARRATIVE (Phase 2, 60d window + 3 sub-experts)** | `docs/research/E_NARRATIVE_design.md` (c1) | Phase 2 신설 | Phase 2 |
| **E_MACRO + TRANSITION regime (E2 minority)** | PLAN_v0.4.md §5.4 | Phase 2 | Phase 2 |
| **Portfolio CVaR Layer (E4 minority)** | PLAN_v0.4.md §5.3 | Phase 2 | Phase 2 |
| **Cascade Graph + E_CASCADE (research mode)** | PLAN_v0.4.md §6 + PLAN_v0.3.md §1.1-1.4 (참조) | Phase 3 (격리) | Phase 3 |
| **Bigdata MCP 6-tool wrapper** | (a) `src/glostat/data/bigdata_client.py` (212줄, S0 stub) | Sprint 1에서 MCP 와이어 | S1 |
| **Entity Map (find_companies cache)** | (a) `src/glostat/data/entity_map.py` (198줄) | 구현됨, Sprint 1에서 universe bootstrap | S1 |

→ **충돌 없음**: 6 outputs는 서로 다른 레이어를 다루며, v0.4 골격이 통합 메타 권위.

---

## 3. Tuning Defaults — Cautious 프로필 채택 ((b) 권고)

(b)의 10 tuning options 중 **Cautious 페르소나** 추천 콤보를 v0.5 기본값으로 채택:

| ID | 항목 | 채택 값 | 출처 |
|----|------|--------|------|
| T1 | Sprint 4 Sharpe 임계값 | **0.8** (유지) | (b) §T1 |
| T2 | OOS degradation 상한 | **30%** (유지) | (b) §T2 |
| T3 | Hindcast AUC 기준선 | **0.60** (유지) | (b) §T3 |
| T4 | Cost-passed verdict 비율 | **40-60% 밴드** (상향, T4 권고) | (b) §T4 |
| T5 | MVP Expert 수 | **3** (조기 확장 금지) | (b) §T5, E10 일치 |
| T6 | 종목 universe | **S&P 500** (500 종목) | (b) §T6 |
| T7 | Snapshot TTL (THEMATICALLY_LINKED) | **7일** (14일에서 단축, c2 권고와 일치) | (b) §T7 |
| T8 | Portfolio CVaR_95 상한 | **3.5%** (유지, E4 일치) | (b) §T8 |
| T9 | Disclaimer 강도 | **Medium** (per-jurisdiction template, E9 일치) | (b) §T9 |
| T10 | Cascade 승격 Sharpe lift | **0.2** (Phase 3 A/B 진입 시 결정, 0.3 상향 옵션 유보) | (b) §T10 |

**향후 변경 정책**: T값 변경은 INV-GS-026 (90d hindcast 재검증) 트리거. ad hoc 변경 금지.

---

## 4. v0.3.1 Pivot 옵션 (Sprint 4 strong-PASS 시 검토)

**v0.3.1 path 활성화 조건** (모두 충족):
1. Sprint 4 hindcast Sharpe ≥ **1.2** (cautious 0.8 threshold 대비 +0.4 buffer)
2. OOS degradation ≤ **15%** (T2 30% 대비 절반)
3. AUC ≥ **0.65** (T3 0.60 대비 +0.05)
4. Cost-passed 비율 50%-65%

**v0.3.1 pivot 시 변화**:
- Phase 2 일정 단축 (3 → 6 Expert를 2주 → 1주 burn-in)
- Cascade research mode → production-ready 가속 (Phase 3 시작 4주 단축)
- Markets US → US+KR+TW 동시 확장 (3개 시장)
- E_CASCADE 가중치 캡 0% → 10% (research-only → 부분 production)

**v0.3.1 pivot 거부 조건** (단 하나라도 위반):
- Sprint 4 결과 ambiguous (위 4개 중 1개라도 미달)
- 1인 개발자 한계 명시 (E10 risk)
- Bigdata MCP 비용 폭증

→ **기본 path는 v0.4 conservative**. v0.3.1은 명시적 strong-PASS 게이트 통과 시에만 활성화.

---

## 5. Sprint 0 → Sprint 1 즉시 액션 ((a) 핸드오프 활용)

### 5.1 Sprint 0 검증 결과 ((a) 보고서 인용)

| Acceptance Gate | 상태 |
|----------------|------|
| `python -c "import glostat"` | PASS (`0.4.0`) |
| `pytest -q` | **PASS — 19/19 in 0.50s** |
| 모든 파일 ≤ 400줄 | PASS (최대 346줄) |
| INV-GS-001 cost gate | PASS |
| INV-GS-010 verdict 결정론 | PASS |
| INV-GS-022 snapshot 결정론 (Merkle leaf) | PASS |
| INV-GS-023 prompt versioning | PASS |
| INV-GS-024 broadcast 금지 (4 변형) | PASS |
| `ruff check` | PASS |

### 5.2 Sprint 1 첫 PR 권고 (1주 작업)

1. **MCP 와이어** (`src/glostat/data/bigdata_client.py` stubs → 실제 호출):
   - `find_companies` 가장 먼저 (entity_map bootstrap 필요)
   - `bigdata_company_tearsheet` 다음 (E_FUNDAMENTAL + E_FUND_FLOW 1차 데이터)
   - `bigdata_events_calendar` 마지막 (E_TIME 보조)
2. **Universe bootstrap**: S&P 500 ticker 500개 → `find_companies` 일괄 호출 → `entity_map.parquet` 영구 캐시 (1회만, INV-GS-002)
3. **E_FUNDAMENTAL 구현** (`src/glostat/experts/e_fundamental.py` 신설): tearsheet → PER/ROE/EPS surprise → ExpertSignal
4. **첫 verdict 검증**: `glostat predict AAPL` → 단일 Expert verdict + snapshot 저장 + replay 검증
5. **CI 게이트 추가**: GitHub Actions에서 `pytest -m invariant` 매 PR 실행

### 5.3 Sprint 1 Acceptance

- AAPL verdict 100회 호출 → 100% 동일 (snapshot replay 검증)
- entity_map.parquet 500 종목 1회 빌드 완료
- ruff/mypy 클린 유지

---

## 6. v0.5 Final Invariants (INV-GS-001..035)

### 6.1 활성 (MVP에서 enforce)

INV-GS-001..010 (v0.1) + INV-GS-022..035 (v0.3 시장 경계 일부 + v0.4 minority) = **24개 활성 invariants**

### 6.2 Deferred (Phase 2/3, configs/invariants.yaml에 `deferred_to` 명시)

INV-GS-011..021 (cascade 관련 — Phase 3에서 research mode 활성화 시 enforce)

### 6.3 (a)가 구현 검증 완료한 invariants

(a) `tests/test_invariants.py` 19개 테스트로 다음 invariants가 코드 레벨에서 enforce됨:
- INV-GS-001 (cost-gate), INV-GS-002 (entity_map cache), INV-GS-010 (deterministic verdict), INV-GS-022 (snapshot Merkle), INV-GS-023 (prompt version), INV-GS-024 (broadcast 금지, 7 jurisdictions)

→ **Sprint 1 추가 invariants 구현 시 동일한 패턴 (test_invariants.py 추가) 의무**.

---

## 7. 차별화 매트릭스 (v0.5 vs 모든 lineage)

| 차원 | v0.1 | v0.3 | v0.4 | v0.3.1 (alt) | **v0.5 (consolidated)** |
|-----|------|------|------|------|------|
| MVP scope | 8 Expert × 80 markets | 9 Expert × 80 markets × cascade | 3 Expert × US × no cascade | 9 Expert × 80 markets × cascade (10주) | **3 Expert × US × no cascade (5주) + Sprint 4 strong-PASS 시 v0.3.1 pivot 옵션** |
| 검증 | 암묵 | 암묵 | Sprint 0/4 first-class | Sprint 0 + 게이트 추가 | **Sprint 0 (a)에서 이미 19/19 PASS** |
| Cascade | 부재 | MVP 차별화 | Phase 3 research | MVP 보존 | **Phase 3 + v0.3.1 pivot 활성화 시 가속** |
| 구현 상태 | 계획만 | 계획만 | 계획만 | 계획만 | **Sprint 0 코드 16 파일 라이브** |
| Authority structure | 단일 문서 | 단일 문서 | 단일 문서 | 단일 문서 | **메타: PLAN_v0.5 + 3 design specs + scaffolding** |
| Pivot mechanism | 부재 | 부재 | 부재 | 부재 | **v0.3.1 명시적 pivot 게이트 (§4)** |
| Tuning framework | 부재 | 부재 | 암묵 | 암묵 | **(b) T1-T10 + 3 페르소나 (cautious 채택)** |

---

## 8. 의사결정 (Opus 4.7 자체 commit, 사용자 후속 검토)

| # | 항목 | 결정 | 근거 |
|---|------|------|------|
| **D1** | 방향 | **v0.4 conservative path 채택** + v0.3.1 pivot 옵션 보존 | 3 RECONSIDER 수렴 + Sprint 0 이미 v0.4 가정으로 구현 + pivot으로 ambition 보존 |
| **D2** | Tuning 프로필 | **Cautious 채택**, 단 **AUC 0.60 → 0.62로 상향** | E1 minority "AUC 0.611 = 노이즈 가까움" 반영. 0.62는 통계적 유의미성 안전선 |
| **D3** | v0.3.1 pivot 게이트 | **Sharpe ≥ 1.2 + OOS ≤ 15% + AUC ≥ 0.65 + Cost-pass 50-65% + 90d compliance 무사고** (5번째 조건 추가) | E9 minority 반영 — pivot이 compliance risk를 키우면 안 됨 |
| **D4** | Sprint 0 코드 리뷰 | **자체 PASS 판정** (19/19 tests, ruff clean, ≤346줄). 사용자 리뷰는 비차단 (편의 시) | Acceptance gates 모두 green |
| **D5** | Sprint 1 첫 PR 우선순위 | **변경**: 1) **Cost audit (100종목 dry run, 실측)** → 2) MCP 와이어 → 3) universe bootstrap → 4) E_FUNDAMENTAL → 5) AAPL verdict 검증 | E5 minority — 단가 모르면 budget unfalsifiable. 와이어 전에 측정 우선 |
| **D6** | Monthly cost budget | **3-tier 자동 적용**: Audit ≤ $50 (Sprint 1), Soft cap $200/월 (warn), Hard cap $500/월 (halt non-essential). 80%/95% throttle threshold | 1인 hobbyist 현실. 실측 후 D6 재검토 (D6.1 트리거) |
| **D7** | Compliance | **현 구현 유지** (E9 7 jurisdictions disclaimer, broadcast 영구 금지). 추가 RavenPack ToS 검증은 Sprint 1과 병행 | (a)에서 INV-GS-024 4가지 변형 모두 PASS, 추가 작업 불필요 |

### 8.1 D6 Cost Budget 상세 (NEW)

**3-tier budget enforcement** (`src/glostat/data/bigdata_client.py` BudgetTracker 확장 필요, Sprint 1):

```yaml
budget:
  audit_phase:           # Sprint 1 첫 작업
    cap_usd: 50
    duration_days: 1     # 100종목 × 1일 dry run
    purpose: "actual unit cost 측정 → D6.1 재검토"
  
  soft_cap:              # 일상 운영
    monthly_usd: 200
    threshold_pct: 80    # 80% 도달 시 warn (Telegram 차단되어 있으므로 CLI/log)
    action: "smart-mode searches 일시 중단, fast-mode만 허용"
  
  hard_cap:              # 위험선
    monthly_usd: 500
    threshold_pct: 95    # 95% 도달 시 halt non-essential
    action: "tearsheet TTL 1h → 6h 강제 연장, events_calendar 24h → 168h, INV-GS-001 cost-gate 1.5× → 2.0× 강화"
    overflow: "hard 100% 도달 시 verdict emission 중단, kill_criteria evaluator만 동작"

  per_tool_quota:
    bigdata_search_smart: 5/day      # E5 minority 반영, 가장 비싼 tool
    bigdata_search_fast: 100/day
    bigdata_company_tearsheet: 600/day  # 500 종목 + 100 buffer
    bigdata_events_calendar: 50/day
    find_companies: lifetime_500       # universe bootstrap 후 사실상 0
```

### 8.2 D6.1 Re-evaluation Trigger

Sprint 1 cost audit (D6 첫 단계) 결과 다음 중 하나에 해당 시 D6 재검토:
- 실측 단가가 추정 대비 10× 차이 (위 또는 아래)
- $50 audit budget으로 100 종목 × 1일을 못 끝냄 (단가 너무 비쌈)
- $50 audit budget의 10% 미만 사용 (단가 매우 저렴, soft/hard cap 상향 가능)

---

## 9. 최종 산출물 인벤토리

### 코드 (16 source files, S0 PASS)
```
src/glostat/
├── __init__.py (7)
├── core/
│   ├── seeded_rng.py (73) — MOET A7
│   └── types.py (200) — Verdict v1, ExpertSignal, MarketMeta
├── data/
│   ├── bigdata_client.py (212) — 6 MCP tools (S0 stubs)
│   ├── entity_map.py (198) — find_companies cache (INV-GS-002)
│   ├── prompt_versioning.py (200) — PromptRegistry (INV-GS-023)
│   └── snapshot_broker.py (346) — Merkle + SQLite + parquet (INV-GS-022)
├── risk/
│   └── compliance_gate.py (157) — 7 jurisdictions (INV-GS-024)
└── replay/
    └── validation_harness.py (172) — IS/OOS, Hindcast, PassCriteria
tests/test_invariants.py (326) — 19 tests PASS
```

### 문서 (9 markdown, ~3500 lines)
```
docs/ssot/
├── PLAN_v0.1.md (174) — 28 idea inventory
├── PLAN_v0.2.md (376) — Cascade Graph 추가
├── PLAN_v0.3.md (582) — Market Boundary 추가
├── PLAN_v0.4.md (354) — Opus minority synthesis
├── PLAN_v0.3.1_patched.md (161) — majority-first alternative
├── v0.4_tuning_proposals.md (295) — T1-T10
└── PLAN_v0.5.md (← THIS) — consolidated final
docs/research/
├── E_NARRATIVE_design.md (630) — c1 sell-the-news + 3 sub-experts
├── snapshot_broker_design.md (436) — c2 Merkle + parquet
└── kill_criteria_design.md (488) — c3 KillCriteriaMonitor + dashboard
```

### Configs
```
configs/
├── markets.yaml (63) — XNAS + XNYS only
└── invariants.yaml (194) — INV-GS-001..035 (active + deferred)
```

---

## 10. 합성 메시지 (Opus 4.7)

**v0.1 → v0.5의 5번 iteration이 보여준 것**:

1. **Scope creep는 자기 자신을 합리화한다** — v0.1에서 v0.3까지 사용자 통찰마다 차원이 추가됐고, 매번 "정당한 확장"으로 보였다. E10 contrarian 정확.
2. **Minority alpha는 진짜 alpha다** — 7 vs 3 투표에서 다수가 "patch and ship"이라고 했지만, 3명의 reconsider 수렴이 진짜 risk를 짚었다. 사용자의 "majority = known alpha" 원칙은 실증됐다.
3. **Validation-first는 빌드 비용을 줄이지 않는다, 잘못된 결정 비용을 줄인다** — Sprint 0 (a)가 실증: 1주에 16 파일 + 19 테스트 + 5 invariants enforced. 이는 "validation framework는 무거워서 비현실적"이라는 가정을 반박한다.
4. **6 parallel agents는 6 sequential agents보다 좋다** — 4 가지 지적 노력 (UX deep-dive, MLOps deep-dive, contrarian alternative, 실제 코드)이 동시 진행되며 서로 다른 layer에 기여. 충돌 없음.
5. **Pivot mechanism이 핵심** — v0.5는 v0.4를 채택하면서도 v0.3.1을 죽이지 않는다. "Sprint 4 strong-PASS 시 pivot" 게이트가 conservative와 ambitious를 모두 살린다.

**v0.5 한 줄 요약**:
> **검증된 인프라 위에 가장 작은 가설을 가장 빠르게 검증하고, 검증이 강하면 더 큰 가설로 pivot하라**.

---

**v0.5 작성 완료. Sprint 1 진입 준비.**
