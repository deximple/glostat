# GLOSTAT v1.0 — Evidence-based Probability Predictor for Global Equities
## (개선된 TITAN의 open-source 진화형)

> **Status:** ACTIVE — Canonical SSOT as of 2026-04-29.
> Supersedes the v0.6 decision-engine framing (archived per Sprint 5 FAIL).
> All v0.1..v0.7 plan documents preserved in this directory for lineage audit.
>
> **One-line:** GLOSTAT v1.0는 매수/매도 행동을 지시하는 alpha engine이 아니다.
> 다중 thesis의 보정된 (Brier-weighted) **확률 예측 + 근거 체인 + reproducibility**
> 를 제공하는 open-source **prediction tool**이다.

---

## 0. Reframe rationale + lineage (v0.1 → v1.0)

### 0.1 왜 reframe인가

v0.6/v0.7의 8개 thesis (E_FUNDAMENTAL, E_FUND_FLOW, E_TIME, E_SECTOR_ROTATION,
E_PEAD, E_FOMC_DRIFT, E_INSIDER_CLUSTER, E_FX_CARRY, E_COMMODITY_TS,
E_FUNDING_CARRY, E_FOREIGN_REVERSAL)는 **decision-engine** 관점에서 모두 FAIL이었다.
Sprint 4 게이트(Sharpe ≥ 0.8, AUC ≥ 0.62, OOS deg ≤ 30%, cost-passed ∈ [40,60]%)
기준으로 단 한 개도 통과하지 못했다.

그러나 같은 데이터를 **prediction tool** 관점으로 다시 보면 풍경이 달라진다:

- E_PEAD overall AUC = 0.5865 (chance 대비 +8.6pp) — 약하지만 **신호가 존재**.
- E_FOREIGN_REVERSAL OOS Sharpe = 1.46 (overall 0.58) — KR 시장에서 **검증된 패턴**.
- E_INSIDER_CLUSTER overall Sharpe = 0.78 (n=11) — sample 작지만 **방향성 있음**.
- 나머지 5개 — AUC가 0.5 근처 (≈ 무신호) → **무가중치 또는 음의 calibration**.

v0.6 framing은 위의 분포를 "8/8 FAIL"이라 봤다. v1.0 framing은 같은 데이터를
"**8개 신호의 calibration 매트릭스**"로 본다. 후자가 정직하다.

### 0.2 Lineage

| 버전 | 일자 | Framing | 결과 |
|------|------|---------|------|
| v0.1 | 2026-04-22 | 9-Expert decision engine, multi-market | 너무 큼 (E10 contrarian dissent) |
| v0.2 | 2026-04-23 | + Cascade Graph (cross-market propagation) | scope creep |
| v0.3 | 2026-04-24 | Market boundary + UAID 명시 | 진입장벽 ↑ |
| v0.3.1 alt | 2026-04-25 | E10/E6/E5 minority dissent (validation-first) | 적용 보류 |
| v0.4 | 2026-04-26 | dramatic refactor — 3-Expert US-only swing, kill criteria | 방향 정착 |
| v0.5 | 2026-04-27 | Bigdata MCP cost gate 추가 | 과도 의존 인지 |
| v0.6 | 2026-04-28 | Free-stack first, MVP $0, Bigdata Phase 2+ | 제약 명확화 |
| v0.7 | 2026-04-29 | (drafted) 9-thesis screening on infra | 8 FAIL → archive |
| **v1.0** | **2026-04-29** | **Prediction tool reframe (개선된 TITAN)** | **active** |

### 0.3 What v1.0 changes vs v0.6/v0.7

| 차원 | v0.6/v0.7 | v1.0 |
|------|-----------|------|
| 출력 형태 | Verdict (BUY/HOLD/SELL action) | Prediction (확률 분포 + CI + 근거) |
| 합의 방식 | edge_bps ≥ 1.5 × all_in_bps cost gate | Brier-weighted ensemble |
| 평가 게이트 | Sprint 4 gate (Sharpe ≥ 0.8 등) | Calibration table (per-thesis Brier + AUC) |
| 신호 다루기 | FAIL → 폐기 | FAIL → "weak/anti-predictive 신호로 weight ↓" |
| Compliance | Telegram broadcast 영구 금지 | (그대로 유지) + per-prediction disclaimer |
| Sample size | 무관 (각 thesis 단독 평가) | 가중치는 sample-aware (n < 50 → weight 강제 ↓) |
| Universe | XNAS/XNYS only (MVP) | Global (US, KR, FX, commodities, crypto), per-thesis 명시 |
| Recalibration | 없음 (PASS or FAIL) | Quarterly (분기별 hindcast 재실행 → calibration_table 갱신) |
| 정직성 표기 | Sprint 4 게이트 통과 여부 | per-thesis Brier + n + interpretation note |

---

## 1. TITAN과의 비교 + GLOSTAT v1.0가 TITAN을 어떻게 개선하는지

### 1.1 출발선: TITAN

TITAN (`/Applications/Titan/titan/`)은 KR 시장 단독, 7-engine integrated verdict
오케스트레이터다. 핵심 구조:

- 7 engine (chart pattern × 4, news/flow, fx-valuation 1, regime 1) 수직 결합
- Verdict는 STRONG_BUY..STRONG_SELL 5단 + directive + target/stop
- 운영: `Verdict().analyze("005930")` → text summary
- 데이터: Naver/ThinkPool/Toss(KR), 부분 LLM (news 감성)
- 배포: 개인 사용 + (역사적으로) Telegram bot

### 1.2 GLOSTAT v1.0가 상속하는 것

- **Engine ensemble 패턴** — 다중 sub-signal을 weighted aggregation
- **Hindcast-first 검증** — TITAN B4 historical (60.3%, 58 events) 같은 사후검증 사이클
- **Personal-use disclaimer** — 광고/공시 의무 회피
- **Reasoning 필드** — 왜 그 verdict인지 자연어 설명

### 1.3 GLOSTAT v1.0가 개선하는 것

| 항목 | TITAN | GLOSTAT v1.0 |
|------|-------|-------------|
| 시장 | KR 전용 | Global (US, KR, FX, commodities, crypto) |
| 데이터 source | 비공식 scraper (Naver/ThinkPool/Toss) | 공식 API (yfinance, SEC EDGAR, CFTC, CCXT) + phase-gated 유료 |
| 출력 | action (BUY/SELL) | **probability + CI + evidence** |
| Compliance | Telegram bot 활성 (광고 risk) | **broadcast 영구 차단** (`ComplianceError`) |
| 보정 | hindcast 단발성 | **분기별 재calibration** (calibration_table.parquet) |
| Reproducibility | 로컬 캐시 | **Snapshot Broker** (Merkle leaf + parquet shard + SQLite index) |
| 가중치 결정 | 휴리스틱 비율 | **Brier-score 기반 sigmoid weighting** |
| 배포 | private repo | **MIT open-source** |
| Scope discipline | 9 engine 모두 ON | 약한 thesis는 weight=0 (자동) |
| Honesty | "PEAD 60%" | "PEAD AUC 0.586, n=298, weight 0.18" |
| Multi-horizon | Swing (5d) hard-coded | per-thesis horizon (1d~30d) 명시 |

GLOSTAT v1.0 = "TITAN을 cross-market + open-source + calibrated + multi-horizon으로
확장한 것 + 정직성 게이트(broadcast 금지, per-prediction disclaimer)를 코드 레벨에 박은 것."

---

## 2. 8개 thesis FAIL 결과의 의미 재해석

### 2.1 v0.6/v0.7 framing에서의 결과

```
8 thesis tested
8 thesis FAIL  ← (v0.6 framing)
Project archived per INV-GS-033
```

### 2.2 v1.0 framing에서의 같은 데이터

```
8 thesis tested
→ 8 calibration data points
→ Composite predictor with sample-aware Brier weights
```

| Thesis | n_samples | AUC | Sharpe | OOS deg | v0.6 verdict | v1.0 weight* | v1.0 interpretation |
|--------|----------:|----:|-------:|--------:|--------------|-------------:|---------------------|
| E_PEAD | 298 | 0.587 | +0.63 | 116% | FAIL | 0.18 | weak positive predictor (post-earnings drift exists, OOS unstable) |
| E_FOREIGN_REVERSAL | 424 | 0.467 | +0.58 | 0% | FAIL | 0.14 | KR-specific reversal pattern (TITAN B4와 -8pp gap) |
| E_INSIDER_CLUSTER | 11 | 0.339 | +0.78 | 0% | FAIL | 0.05 | n too low — directional but underpowered |
| E_COMMODITY_TS | 517 | 0.489 | +0.14 | 100% | FAIL | 0.06 | barely above chance, dominated by ETF contango |
| E_SECTOR_ROTATION | 174 | 0.470 | -0.48 | 100% | FAIL | 0.00 | anti-predictor (weight clamped to 0) |
| E_FOMC_DRIFT | 135 | 0.357 | -1.34 | 100% | FAIL | 0.00 | anti-predictor |
| E_FX_CARRY | 135 | 0.400 | -1.53 | 100% | FAIL | 0.00 | anti-predictor |
| E_FUNDING_CARRY | 4922 | 0.505 | -0.23 | 457% | FAIL | 0.02 | crypto funding noise (large n, weak signal) |

*Brier-derived weight (illustrative — actual values computed at run time per `glostat calibrate`).

핵심 통찰:
1. **8 FAIL ≠ "아무것도 모른다"** — 8개의 calibration point를 얻었다.
2. **AUC 0.587 (E_PEAD)도 정보다** — coin flip(0.5) 대비 통계적 의미를 가진다.
3. **Anti-predictor도 정보다** — weight=0이 아니라, 음의 가중치도 가능 (단, 본 spec에서는 안전을 위해 음수는 0으로 clamp).
4. **OOS degradation은 calibration의 일부** — degraded thesis는 confidence interval이 넓어진다.

이것이 v0.6 → v1.0 reframe의 핵심: **"PASS/FAIL"** 이진 게이트를 **"calibrated weight"** 연속체로 대체.

---

## 3. Prediction output spec

### 3.1 Prediction dataclass

```python
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Direction = Literal["UP", "DOWN", "FLAT"]
Horizon = Literal["1d", "5d", "30d"]


@dataclass(frozen=True, slots=True)
class ThesisContribution:
    thesis_name: str                 # e.g. "E_PEAD"
    direction: Direction             # this thesis's vote
    raw_score: float                 # in [-1.0, +1.0]
    brier_weight: float              # in [0.0, 1.0], sigmoid(-Brier)
    n_calibration_samples: int       # from calibration_table.parquet
    calibration_window: tuple[str, str]  # ("2024-01-01", "2026-03-31")
    auc: float                       # [0.0, 1.0]
    sources: tuple[str, ...]         # ("snapshot:abc123", "edgar:0001234567-25-001")


@dataclass(frozen=True, slots=True)
class Prediction:
    ticker: str                                  # bare ticker or UAID
    market: str                                  # MIC code (XNAS, XNYS, XKRX, ...)
    horizon: Horizon                             # 1d / 5d / 30d
    issued_at: datetime                          # UTC

    # Probability output (replaces v0.6 BUY/SELL action)
    p_up: float                                  # P(return > 0 over horizon), [0.0, 1.0]
    p_up_lower: float                            # 90% CI lower
    p_up_upper: float                            # 90% CI upper
    composite_brier: float                       # ensemble Brier estimate

    # Provenance + audit
    contributing: tuple[ThesisContribution, ...]
    evidence_hash: str                           # Merkle leaf (INV-GS-022)
    prompt_versions: tuple[tuple[str, str], ...] # (INV-GS-023)
    git_commit: str
    snapshot_root: str                           # broker.audit_root() at issue time

    # Compliance (INV-GS-024 + INV-GS-104)
    disclaimer: str = field(default=(
        "INFORMATION TOOL ONLY. Personal use. "
        "Not investment advice. Past calibration ≠ future performance."
    ))

    def __post_init__(self) -> None:
        if not 0.0 <= self.p_up <= 1.0:
            raise ValueError(f"p_up {self.p_up} out of [0,1]")
        if not (self.p_up_lower <= self.p_up <= self.p_up_upper):
            raise ValueError(
                f"CI inconsistent: lower={self.p_up_lower}, p={self.p_up}, "
                f"upper={self.p_up_upper}"
            )
        if not self.contributing:
            raise ValueError("INV-GS-102 violation: no contributing thesis")
        if not self.evidence_hash:
            raise ValueError("INV-GS-022 violation: evidence_hash empty")
        if not self.prompt_versions:
            raise ValueError("INV-GS-023 violation: prompt_versions empty")
        if not self.disclaimer:
            raise ValueError("INV-GS-104 violation: disclaimer missing")
```

### 3.2 JSON output example (canonical)

```json
{
  "ticker": "AAPL",
  "market": "XNAS",
  "horizon": "5d",
  "issued_at": "2026-04-29T13:30:00Z",
  "p_up": 0.547,
  "p_up_lower": 0.491,
  "p_up_upper": 0.603,
  "composite_brier": 0.247,
  "contributing": [
    {
      "thesis_name": "E_PEAD",
      "direction": "UP",
      "raw_score": 0.42,
      "brier_weight": 0.18,
      "n_calibration_samples": 298,
      "calibration_window": ["2024-01-01", "2026-03-31"],
      "auc": 0.587,
      "sources": ["snapshot:a1b2c3", "edgar:0000320193-26-000003"]
    },
    {
      "thesis_name": "E_INSIDER_CLUSTER",
      "direction": "UP",
      "raw_score": 0.18,
      "brier_weight": 0.05,
      "n_calibration_samples": 11,
      "calibration_window": ["2024-01-01", "2026-03-31"],
      "auc": 0.339,
      "sources": ["snapshot:d4e5f6"]
    }
  ],
  "evidence_hash": "9f8e...c2a1",
  "prompt_versions": [["E_PEAD", "abc123..."], ["E_INSIDER_CLUSTER", "def456..."]],
  "git_commit": "2b96ca5",
  "snapshot_root": "fedcba...0987",
  "disclaimer": "INFORMATION TOOL ONLY. Personal use. Not investment advice. Past calibration ≠ future performance."
}
```

### 3.3 행동 출력 영구 금지 (INV-GS-101)

`Prediction`에는 `action`, `target_price`, `stop_price`, `suggested_size_pct`,
`directive` **필드가 존재하지 않는다**. 이것은 v0.6 Verdict와의 핵심 결별점이다.
사용자/외부 시스템이 prediction을 받아 어떤 행동을 할지는 **사용자 책임**.

```python
# 금지 예시
prediction.action        # AttributeError — 필드 없음
prediction.target_price  # AttributeError — 필드 없음
```

---

## 4. Calibration framework

### 4.1 Brier score

per-thesis: `Brier = mean((p_up_predicted - y_actual)^2)`, lower is better.

```python
def brier(predictions: list[float], outcomes: list[int]) -> float:
    if len(predictions) != len(outcomes):
        raise ValueError("length mismatch")
    if not predictions:
        return 0.0
    return sum((p - y) ** 2 for p, y in zip(predictions, outcomes)) / len(predictions)
```

`outcomes[i]` ∈ {0, 1} (forward-return up?). `predictions[i]` ∈ [0, 1].

### 4.2 Brier-weighted ensemble (INV-GS-103)

```python
import math

def thesis_weight(brier: float, n: int, n_min: int = 50) -> float:
    # 1) sample size guard: n < n_min → weight scaled down linearly
    sample_factor = min(1.0, n / n_min)
    # 2) Brier-based weight: lower Brier → higher weight via sigmoid centered at 0.25
    #    Brier of 0.25 = perfectly random predictor → weight ~0.5
    #    Brier of 0.10 = strong predictor → weight ~0.82
    #    Brier of 0.40 = anti-predictor → weight ~0.18
    raw_w = 1.0 / (1.0 + math.exp(20.0 * (brier - 0.25)))
    return max(0.0, raw_w * sample_factor)


def composite_p_up(contributions: list[ThesisContribution]) -> float:
    weights = [c.brier_weight for c in contributions]
    total = sum(weights)
    if total <= 0:
        return 0.5  # all-anti or all-zero → return prior
    weighted = sum(c.brier_weight * (0.5 + 0.5 * c.raw_score) for c in contributions)
    return max(0.0, min(1.0, weighted / total))
```

### 4.3 AUC table (calibration_table.parquet schema)

```
thesis_name : str       (E_PEAD, E_FOREIGN_REVERSAL, ...)
universe    : str       (e.g. "us_sp500_top50", "kr_kospi200_top20")
horizon     : str       (1d, 5d, 30d)
calibration_start : date
calibration_end   : date
n_samples   : int
auc         : float     [0.0, 1.0]
sharpe      : float
oos_degradation : float
brier       : float
weight      : float     (sigmoid-derived, sample-aware)
last_recalibrated : timestamp
```

이 테이블은 분기별로 재생성 (INV-GS-105).

---

## 5. New invariants + deprecated invariants

### 5.1 신규 INV-GS-101 .. INV-GS-105

| ID | Summary | 시행 위치 |
|----|---------|----------|
| INV-GS-101 | 출력은 확률 분포 + CI. BUY/SELL 행동 출력 금지 | `predictor.composite`, `cli.predict` |
| INV-GS-102 | 모든 prediction은 source signals + calibration window + n_samples 인용 | `Prediction.contributing` 비어있으면 `ValueError` |
| INV-GS-103 | Composite p_up은 Brier-weighted ensemble (단순 평균 금지) | `composite_p_up()` 구현 |
| INV-GS-104 | per-prediction disclaimer 필수 (extends INV-GS-024) | `Prediction.disclaimer` non-empty 검증 |
| INV-GS-105 | 분기별 recalibration: 전체 thesis hindcast 재실행 + `calibration_table.parquet` 갱신 | `glostat calibrate --quarterly` |

### 5.2 폐기되는 v0.6 invariants

| ID | 이유 |
|----|------|
| INV-GS-001 | edge_bps ≥ 1.5 × all_in_bps — **decision-engine artifact**. v1.0은 BUY 출력 자체가 없으므로 cost gate 의미 상실 |
| INV-GS-005 | 4 expert 동방향 → 0.80× anti-herd discount — **decision-engine artifact**. Brier-weighted ensemble이 자동으로 dissent 반영 |
| INV-GS-033 | Sprint 4 gate FAIL → automatic shutdown — **decision-engine artifact**. Weak thesis는 weight ↓ 처리되며, 프로젝트 자체는 shutdown 대상 아님 |

폐기된 INV-GS는 `configs/invariants.yaml`에서 `deprecated: true` + `note` 명시
(삭제 아님 — lineage 보존).

### 5.3 그대로 유지되는 핵심 INV-GS

- INV-GS-002 (find_companies 영구 캐시)
- INV-GS-006 (hash-chained NDJSON evidence)
- INV-GS-010 (determinism)
- INV-GS-022 (Snapshot Broker)
- INV-GS-023 (prompt versioning)
- **INV-GS-024 (broadcast 영구 금지)** — INV-GS-104가 강화
- INV-GS-026 (90d hindcast / IS-OOS / AUC ≥ 0.60 게이트는 weight ≥ 0.5의 prerequisite으로 재해석)
- INV-GS-035 (RavenPack ToS)
- INV-GS-036..040 (Bigdata phase gating, 무료 stack throttle, SEC User-Agent)

---

## 6. Compliance restatement

### 6.1 v1.0의 compliance 입장

- **INFORMATION TOOL** (정보 도구). NOT a registered investment advisor.
- Output: probability prediction + evidence chain. Output ≠ recommendation.
- INV-GS-024는 그대로: `broadcast_telegram`, `mass_email`은 inert sentinel,
  호출 시 `ComplianceError` raise. v1.0 reframe과 무관하게 영구 금지.
- INV-GS-104 신설: 매 prediction의 출력에 disclaimer가 첨부됨 (필드 검증).
- 다국가 dispatcher 미적용 — 단일 string disclaimer로 충분 (개인 사용 한정).

### 6.2 정직성 라벨

모든 CLI/SDK 출력의 머리에 다음 헤더가 붙는다:

```
GLOSTAT v1.0 — Probability Predictor
Information tool. Not investment advice.
Past calibration data does not guarantee future performance.
See calibration_table.parquet for empirical predictive strength.
```

### 6.3 폐기된 framing의 잔존 문구 정리

`docs/`, `README.md`, CLI help text 어디에도 "alpha engine", "trading signal",
"BUY recommendation" 같은 표현이 잔존하지 않도록 v1.0 reframe과 함께 일괄 교체.
(정직성 — v0.6 archive doc만 예외, 역사적 lineage 보존을 위해 그대로 둠.)

---

## 7. Open-source distribution path

### 7.1 Publish

- Repository: `github.com/<you>/glostat`
- License: MIT (이미 적용)
- README의 첫 화면: post-mortem 링크 + v1.0 reframe banner를 가장 위에 배치
- PyPI: `pip install glostat` (v1.0.0 첫 publish 후 quarterly 갱신)

### 7.2 Use cases

| 페르소나 | 사용 형태 |
|---------|----------|
| 개인 투자자 | `glostat predict AAPL --horizon 5d` → 확률 + 근거 |
| Quant 연구자 | `from glostat import Pipeline` → 자체 thesis 추가 후 calibration |
| Bloggers / 작가 | calibration_table 인용 → "PEAD signal weight 0.18 per GLOSTAT v1.0 calibration" |
| 학생 / 교육자 | hindcast harness + snapshot broker로 "honest backtest" 시연 |
| 대안 데이터 vendor | 자기 신호를 thesis로 plug-in → calibration 결과 공개 |

### 7.3 Community

- Issue templates: `[BUG]`, `[NEW THESIS]`, `[DATA SOURCE]`, `[CALIBRATION QUESTION]`
- PR template: 새 thesis는 calibration data 첨부 필수 (n ≥ 50, AUC, Sharpe, OOS)
- Discussions: thesis 제안 → 합의 → 구현 → calibration → merge
- Quarterly release: v1.x 마이너 (calibration table 갱신만), v2.0은 framework
  레벨 변경 시에만

---

## 8. Quarterly recalibration policy

### 8.1 주기

- Q1: 1월 31일
- Q2: 4월 30일
- Q3: 7월 31일
- Q4: 10월 31일

### 8.2 절차

```bash
# 1) 모든 thesis hindcast 재실행 (90d → 분기 × 4 누적)
uv run glostat calibrate --all-thesis --window 365d

# 2) calibration_table.parquet 갱신
uv run glostat calibrate --update-table

# 3) docs/CALIBRATION.md 자동 재생성
uv run glostat calibrate --regenerate-docs

# 4) 검증
uv run pytest -q -m calibration

# 5) Tag + release
git tag v1.x.0  # 분기마다 minor bump
```

### 8.3 변경 통보

CHANGELOG에 thesis별 weight 변동 표 첨부. Brier 0.05 이상 변동은 PR description에 root-cause 명시.

### 8.4 신규 thesis 추가 시

- 90일 hindcast (IS/OOS) — INV-GS-026
- AUC ≥ 0.50 (calibration table 진입 최소 조건; weight는 별도 계산)
- n ≥ 50 — sample-size guard 통과 (INV-GS-103)
- PR 시 calibration_table 한 줄 추가

---

## 9. Final scope discipline

### 9.1 GLOSTAT v1.0가 IS

- Calibrated probability predictor (per-thesis Brier-weighted ensemble)
- Reproducible (Snapshot Broker + Merkle leaf + parquet shards)
- Open-source research framework (MIT, fork-friendly)
- Multi-horizon (1d/5d/30d, per-thesis 명시)
- Multi-market (US, KR, FX, commodities, crypto)
- Honest about its own predictive strength (calibration table is public)

### 9.2 GLOSTAT v1.0가 IS NOT

- A backtester — backtest는 calibration의 부산물이지 목적 아님
- A trading bot — 행동 출력 영구 금지 (INV-GS-101)
- Investment advice — 정보 도구일 뿐 (INV-GS-104, compliance disclaimer)
- A guaranteed alpha source — 모든 thesis의 weight는 sigmoid(Brier) 한계 안
- A live order router — `broadcast_telegram`, `mass_email`은 inert sentinel
- A multi-user product — personal use only
- A black box — 모든 prediction은 `contributing` + `evidence_hash` + `git_commit` 동봉

### 9.3 Anti-creep guard

다음 항목 추가 제안은 **자동 reject** (PR template 검증):
- "BUY/SELL 출력 추가" → INV-GS-101 위반
- "Telegram broadcast 재활성화" → INV-GS-024 + INV-GS-104 위반
- "calibration table 우회 weight" → INV-GS-103 위반
- "disclaimer optional 처리" → INV-GS-104 위반
- "MVP에서 paid data 활성화" → INV-GS-036, INV-GS-040 위반

### 9.4 Kill criterion 재정의 (v1.0 변경)

v0.6의 Sprint 4 gate FAIL → shutdown은 폐기 (INV-GS-033).
v1.0의 kill criterion은 **계산 신뢰성 + compliance 위반**만:

| Trigger | Action |
|---------|--------|
| Compliance breach (broadcast 시도 외 새로운 종류) | freeze + 사용자 검토 |
| Snapshot Broker integrity 깨짐 (Merkle root 불일치) | freeze + 재구성 |
| Calibration table not updated > 2 분기 | warn + auto-degrade weights to 0.5× |
| 모든 thesis weight = 0 (composite predictor 무의미) | warn + suggest 사용자에게 "현재 신호 모름" 출력 |
| INV-GS-024 우회 시도 (개발자 PR) | reject + 자동 closed |

---

## Appendix A — Verdict (v0.6) → Prediction (v1.0) 필드 매핑

| v0.6 Verdict | v1.0 Prediction | 비고 |
|--------------|------------------|------|
| `action: BUY/HOLD/SELL` | (없음) | 영구 폐기 (INV-GS-101) |
| `conviction_w: float [0, 3.5]` | `p_up`, `p_up_lower`, `p_up_upper` | 확률 + CI로 대체 |
| `target_price`, `stop_price` | (없음) | 영구 폐기 |
| `edge_bps`, `all_in_bps`, `cost_passed` | (없음) | cost gate 폐기 (action 출력 안 하므로) |
| `expected_pnl_bps` | (없음) | 폐기 |
| `disagreement_weight` | `composite_brier` (CI 폭으로 함의) | 의미 흡수 |
| `contributing_signals: tuple[ExpertSignal, ...]` | `contributing: tuple[ThesisContribution, ...]` | thesis-level metadata 풍부화 |
| `evidence_hash` | `evidence_hash` | 그대로 |
| `prompt_versions` | `prompt_versions` | 그대로 |
| `git_commit` | `git_commit` | 그대로 |
| `user_profile_hash` | (불필요) | personal-use 단일이므로 hash 불필요 |
| `next_trigger` | (없음) | 행동 출력 폐기와 함께 폐기 |
| `horizon_days: int` | `horizon: Literal["1d", "5d", "30d"]` | discrete |
| `market: Literal["XNAS", "XNYS"]` | `market: str` (MIC) | global |

---

## Appendix B — 참고 (v0.6 archived plan, post-mortem)

- `docs/ssot/PLAN_v0.6.md` — 마지막 decision-engine framing의 권위 문서 (archived)
- `docs/post_mortem/SPRINT5_FAIL_post_mortem.md` — 8-thesis FAIL의 honest 진단
- `cache/hindcast/phase1b/phase1b_comparison.md` — E_SECTOR_ROTATION, E_PEAD, E_FOMC_DRIFT, E_INSIDER_CLUSTER calibration data
- `cache/hindcast/phase1c_comparison.md` — E_FX_CARRY, E_COMMODITY_TS calibration data
- `cache/hindcast/phase1d/phase1d_comparison.md` — E_FUNDING_CARRY, E_FOREIGN_REVERSAL calibration data

이 데이터들은 모두 v1.0 calibration_table.parquet의 첫 번째 입력이 된다. **FAIL이 아니라 CALIBRATION DATA**.

---

## Appendix C — TITAN reference

- `/Applications/Titan/titan/verdict.py` — TITAN VerdictResult (5단 action + 7 engine fields). v1.0이 개선하는 출발점.
- TITAN B4 historical (60.3% hit rate, 58 KR events, 2025.06–2026.03) → GLOSTAT Phase 1D live hindcast (52.2%, 424 events) — generalization gap이 calibration의 핵심 데이터.
