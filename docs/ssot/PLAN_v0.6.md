# GLOSTAT — Free-Stack-First Refactor
## v0.6 — 2026-04-28 — **Option B applied** (Bigdata MCP demoted to optional Phase 2+)

> **🛑 ARCHIVED 2026-04-29** — Sprint 5 FAIL → INV-GS-033 SHUTDOWN. Sprint 4 PR #2 (live SHUTDOWN, infra noise) → PR #3 (real SHUTDOWN, 4 specific bugs) → Sprint 5 (3/4 fixes worked, alpha still absent). Composite Sharpe = 0.000, AUC = 0.496 on US megacaps. v0.4 minority premium (E10/E6/E5) fully validated. See `docs/post_mortem/SPRINT5_FAIL_post_mortem.md` for full diagnosis. **DO NOT propose Sprint 6 or v0.7 against this plan** — pick a different alpha thesis if continuing.

> **변경 v0.5 → v0.6**: 사용자 결정 — Bigdata MCP는 RavenPack 유료. MVP free stack (yfinance + SEC EDGAR + FRED) 1차, Bigdata는 Phase 2부터 narrative/cascade 영역 한정 옵션 활성화. v0.5의 다른 모든 결정은 보존.

---

## 0. Option B 결정 근거

E10 contrarian minority insight (PLAN_v0.4 §0.3, kill criteria #5)가 정확히 짚었던 risk:
- Bigdata MCP 의존 = vendor lock-in + 운영 cost risk
- MVP cost $0로 만들 수 있다면 Sprint 4 게이트 통과 부담 최소
- Bigdata 진짜 차별화 = narrative + semantic search (Phase 2/3에서 검증)

→ **MVP cost $0 + Bigdata는 옵션 enrichment layer로 격리**.

이는 v0.4 minority premium 원칙의 자연스러운 귀결 — E10 + E5 ("vendor lock-in 위험") + E9 ("RavenPack ToS 검증") 세 voice의 수렴 적용.

---

## 1. Free Stack Data Plane (NEW)

### 1.1 데이터 source 매트릭스

| Expert | MVP source (FREE) | Phase 2+ option (Bigdata) |
|--------|------------------|--------------------------|
| **E_FUNDAMENTAL** | yfinance (PER/ROE/EPS/배당) + SEC EDGAR (10-K/8-K XBRL) | bigdata_company_tearsheet (premium financials, surprise) |
| **E_FUND_FLOW** | SEC EDGAR 13F (분기, 45d 지연) | bigdata_company_tearsheet fund_trends (real-time, 옵션 활동) |
| **E_TIME** | yfinance OHLCV + earnings calendar (yfinance) | bigdata_events_calendar (UTC 표준화 보조) |
| **E_NARRATIVE** (Phase 2) | (free 대체 매우 제한적 — Google News RSS만) | **bigdata_search smart (필수, 핵심 차별화)** |
| **E_ESG** (Phase 2) | (제한적 — Yahoo ESG basic) | **bigdata_company_tearsheet ESG (필수)** |
| **E_MACRO** (Phase 2) | FRED (US Fed) + ECB + BOJ APIs | bigdata_country_tearsheet (G7 비교, surprise score) |
| **E_GLOBAL_FLOW** (Phase 2) | yfinance multi-ticker ETF | bigdata_market_tearsheet (factor breakdown 추가) |
| **E_CASCADE** (Phase 3) | (free 대체 사실상 불가) | **bigdata_search filings+transcripts (필수, Cascade 핵심)** |

### 1.2 Data Router (NEW 모듈)

`src/glostat/data/data_router.py`:
- Expert별 데이터 요청을 phase-gated source로 라우팅
- `route(expert, data_type) → (client_instance, method_name)` 반환
- **Phase gating**:
  - `mvp` (default): free sources only. Bigdata 호출 시 `ConfigError`
  - `phase_2`: free + Bigdata optional (user consent + budget activation 필요)
  - `phase_3`: free + Bigdata required for cascade

### 1.3 신규 데이터 클라이언트

| 모듈 | 무료 출처 | 제한 |
|------|---------|------|
| `yfinance_client.py` | Yahoo Finance (비공식 wrapper) | 8 req/sec 자체 throttle + exponential backoff retry (Sprint 4 PR #3 — PR #2 93% throttle ratio 완화) |
| `sec_edgar_client.py` | SEC EDGAR API (공식, 무료) | User-Agent 헤더 필수, 10 req/sec 정책 제한 + 429/5xx exponential backoff retry (Retry-After 헤더 honor) |
| `fred_client.py` (Phase 2) | FRED API (St. Louis Fed) | API key 필수, 무료 (https://fred.stlouisfed.org/docs/api/api_key.html) |

---

## 2. Sprint 1 PR 재배치 (v0.5 §5.2 → v0.6)

| 순서 | v0.5 작업 | v0.6 변경 |
|------|----------|----------|
| 1 | Bigdata cost audit ($50 budget) | **삭제** (MVP $0이므로 불필요) |
| 2 | bigdata_client MCP 와이어 | **연기 → Phase 2** |
| **NEW 1** | — | **yfinance_client + sec_edgar_client 와이어** (실제 호출) |
| **NEW 2** | find_companies × 500 | **SEC EDGAR ticker→CIK 매핑** (`company_tickers.json`, 무료) |
| 3 | E_FUNDAMENTAL via tearsheet | **E_FUNDAMENTAL via yfinance + SEC EDGAR** |
| 4 | E_FUND_FLOW via fund_trends | **E_FUND_FLOW via SEC EDGAR 13F** (분기 지연 OK for swing) |
| 5 | E_TIME via events_calendar | **E_TIME via yfinance (OHLCV + earnings)** |
| 6 | AAPL verdict 검증 (cost 발생) | **AAPL verdict 검증 (cost $0)** |

---

## 3. D6 Budget 재정의 (Free-First)

```yaml
# configs/budget.yaml
budget:
  mvp_phase:                          # Sprint 0 ~ Sprint 4
    cap_usd_per_month: 0              # FREE STACK 100%
    sources_active: [yfinance, sec_edgar]
    bigdata_enabled: false            # bigdata_client 호출 시 ConfigError raise
    
  phase_2_optional:                   # Sprint 4 게이트 PASS 후, 사용자 consent 시
    cap_usd_per_month: 50
    activation: explicit_user_consent_required
    sources_added: [bigdata_search_smart, bigdata_company_tearsheet_ESG, fred]
    bigdata_enabled: true
    
  phase_3_cascade:                    # Phase 2 PASS + cascade A/B 시작 시
    cap_usd_per_month: 200
    activation: explicit_user_consent + sprint_4_strong_pass
    sources_added: [bigdata_search_filings, bigdata_search_transcripts]
    
  hard_cap_max: 500                   # 어떤 phase에서도 절대 초과 금지
```

→ **MVP에서 bigdata_client 호출 자체가 코드 레벨 차단** (INV-GS-036).

---

## 4. INV-GS 추가 (v0.6, 5개)

| ID | 불변식 |
|----|-------|
| **INV-GS-036** | MVP phase에서 `bigdata_client` 호출 차단 (`assert_phase_2_or_later()` 가드, 위반 시 `ConfigError`) |
| **INV-GS-037** | yfinance_client는 8 req/sec 자체 throttle + exponential backoff retry (Sprint 4 PR #3 update — PR #2 93% throttle ratio 완화, Yahoo 비공식 cap 10–20/s 연구 기반 보수적 8) |
| **INV-GS-038** | sec_edgar_client는 User-Agent 헤더 필수 (SEC 정책 준수), 10 req/sec 제한 |
| **INV-GS-039** | data_router는 Phase에 따라 source 활성화 — MVP={free only}, Phase 2={free + optional bigdata}, Phase 3={free + bigdata cascade} |
| **INV-GS-040** | Phase 2/3 Bigdata 활성화 시 `configs/budget.yaml` + dashboard 사용자 명시 consent 필요 |

활성 invariants 총: v0.5 24개 + v0.6 5개 = **29개 active**, 11개 deferred (cascade INV-GS-011..021).

---

## 5. v0.5 → v0.6 변경 인벤토리

### 5.1 보존 (변경 없음)
- Direction: validation-first MVP (3 Expert × US × no cascade)
- Sprint 0 코드 16 파일 (다음 §6에서 부분 보강)
- v0.3.1 pivot 옵션 (조건은 그대로)
- INV-GS-001..035 (24개 active)
- Cautious tuning 프로필 (T1=0.8, T2=30%, T3=0.62, T5=3, T6=S&P 500)
- Kill criteria, compliance gate, snapshot broker, prompt versioning

### 5.2 변경
- D6 budget: 3-tier ($50/$200/$500 모두 Bigdata 가정) → **MVP $0 + Phase 활성화 시 escalation**
- Sprint 1 PR 우선순위: Bigdata 와이어 → **free stack 와이어**
- Data source 매트릭스 (Expert별 free vs Bigdata)
- INV-GS 5개 추가 (036..040)

### 5.3 신규 (Sprint 0 보강)
- `src/glostat/data/yfinance_client.py`
- `src/glostat/data/sec_edgar_client.py`
- `src/glostat/data/data_router.py`
- `tests/test_yfinance_client.py`, `test_sec_edgar_client.py`, `test_data_router.py`
- `configs/budget.yaml` (Phase별 cap)

### 5.4 비활성화 (코드 보존, 호출 차단)
- `bigdata_client` 호출 — MVP에서 `ConfigError`. Phase 2 활성화 시 unlock.

---

## 6. 차별화 매트릭스 (v0.5 vs v0.6)

| 차원 | v0.5 | **v0.6** |
|-----|------|---------|
| MVP cost | $200-500/월 (Bigdata) | **$0/월 (free stack only)** |
| 1차 데이터 source | bigdata MCP 1차 | **yfinance + SEC EDGAR 1차** |
| Bigdata 역할 | 핵심 | **Phase 2+ 옵션 enrichment** |
| Sprint 4 게이트 부담 | cost risk + 검증 | **검증만** (cost 0) |
| Vendor lock-in | High (RavenPack) | **Low (multi-source)** |
| 글로벌 cascade | MVP 차별화 → Phase 3 | **Phase 3 (Bigdata 옵션 활성 시)** |
| Trial 의존 | 7일 trial 내 audit 필수 | **trial 불필요** (Phase 2 진입 시 가입) |

---

## 7. Phase 2 Bigdata 활성화 게이트 (명시화)

Sprint 4 게이트 PASS 후, Phase 2 진입 시 Bigdata 활성화 조건 (모두 충족):

```
1. Sprint 4 hindcast Sharpe ≥ 0.8 (Cautious 프로필, T1)
2. 사용자가 RavenPack Bigdata.com 계정 보유 (trial 또는 paid)
3. configs/budget.yaml의 phase_2_optional.activation = "user_consented"
4. INV-GS-024 (broadcast 금지) 그대로 유지
5. RavenPack ToS 검증 완료 (E9 권고, INV-GS-035)
```

미충족 시 Phase 2는 **free stack만으로 진행** (E_MACRO via FRED, E_NARRATIVE 비활성, E_ESG 비활성, E_GLOBAL_FLOW via yfinance ETF).

---

## 8. 즉시 다음 액션

1. ✅ **PLAN_v0.6.md 작성** (이 문서)
2. **Sprint 0 보강**: yfinance_client + sec_edgar_client + data_router + tests + configs/budget.yaml + INV-GS-036~040 (병렬 agent에 위임)
3. **CLAUDE.md 업데이트**: Option B 1줄 반영
4. **bigdata_client.py 가드 추가**: `assert_phase_2_or_later()` (INV-GS-036)
5. **pyproject.toml**: yfinance + httpx 의존성 추가

→ Sprint 0 보강 완료 후 **Sprint 1 첫 PR (yfinance 와이어 + AAPL verdict $0 검증)** 즉시 시작 가능.

---

## 9. 합성 메시지

v0.6은 v0.5의 부정이 아니라 **자기 일관성 회복**:
- v0.4 minority premium 원칙은 그대로
- E10/E5/E9 contrarian voice가 **이미 가리키고 있던 곳**으로 plan이 따라간 것
- Bigdata 가설은 죽지 않음 — Phase 2/3 옵션으로 살아있음, 단 free stack 검증 통과 후

**핵심**: 빌드 비용 < 검증 비용 < 잘못된 결정 비용 (v0.5 §10) — v0.6은 빌드 비용까지 0으로 만들면서 다른 조건은 보존. 가장 작은 가설을 가장 빠르게 검증.

---

**v0.6 작성 완료. Sprint 0 보강 PR 진입.**
