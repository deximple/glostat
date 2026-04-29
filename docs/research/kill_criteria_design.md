# Kill Criteria 메트릭 + 모니터링 대시보드 설계 (E10 Contrarian Deep-Dive)

## 요약
E10의 핵심 주장: **Cascade는 연구이지 사용자 가치가 아님. Zombie project 방지가 핵심.** 이는 명시적 Kill Criteria와 자동 shutdown 메커니즘을 요구한다. 본 문서는 구체적 메트릭 정의, 측정 윈도우, 자동 판정 로직, 대시보드 설계를 제시한다.

---

## 1. 메트릭 정의 (정확한 수학 공식)

### 1.1 Annualized Sharpe Ratio

**공식**:
```
Sharpe = (mean(daily_returns) - risk_free_rate) / std(daily_returns) × sqrt(252)
```

- `daily_returns`: 종가 기준 일일 수익률 (로그 또는 백분율)
- `risk_free_rate`: US 3M T-Bill 수익률 (Bigdata Country Tearsheet에서 동적 로드)
  - Sprint 4 (2025-12-01~2026-02-28): 고정값 4.2% p.a. 사용 (현재 시장)
  - Phase 2+ (rolling): 매월 업데이트
- `std()`: 252일 기준 연환산 표준편차
- **단위**: 배수 (예: 0.8 = 0.8 Sharpe)

**해석**:
- SPY 장기 평균 Sharpe ≈ 0.45 (과거 10년)
- Active manager 중위 Sharpe ≈ 0.5-0.7
- 0.8은 "정당화 가능한 active 운용" 하한

---

### 1.2 Maximum Drawdown (MDD)

**공식**:
```
MDD = min( (cumulative_return[t] - cumulative_return_peak) / cumulative_return_peak )
```

- Peak: 각 시점 이전의 최대 누적 수익률
- Trough-to-peak: 달력 기준 (고정 windows 미사용)
- 일일 mark 기준 (end-of-day close)

**예시**:
- 누적 수익 +20% → -5% (drawdown = 5/120 = -4.2%)
- 다시 +25% → -8% (drawdown = 8/125 = -6.4%)
- MDD = -6.4%

---

### 1.3 Out-of-Sample (OOS) Degradation

**공식**:
```
OOS_degradation = max(0, 1 - (OOS_Sharpe / IS_Sharpe))
```

- IS (In-Sample): 학습 윈도우
- OOS (Out-of-Sample): 검증 윈도우
- 음수는 0으로 처리 (OOS가 더 좋은 경우는 우호적)

**예시**:
- IS Sharpe 1.2, OOS Sharpe 0.84 → degradation = 1 - (0.84/1.2) = 30%
- IS Sharpe 1.0, OOS Sharpe 0.75 → degradation = 25%

---

### 1.4 Hindcast AUC (ROC Area)

**공식**:
```
AUC = ∫[0,1] TPR(FPR) d(FPR)
```

- BUY 신호: 30일 forward return ≥ 중위값 → label = 1
- SELL/HOLD: label = 0
- ROC 곡선: FPR(false positive rate) vs TPR(true positive rate)

**목표**:
- BETASTRIKE baseline: AUC ≥ 0.61
- GLOSTAT 최소: AUC ≥ 0.60 (동등 이상)

---

### 1.5 Cost-Passed Verdict 비율

**공식**:
```
cost_passed_ratio = COUNT(Verdict.cost_passed == True) / COUNT(all_verdicts)
```

- `cost_passed`: 예상 엣지 > 거래 비용
  - XNAS/XNYS: fee 0.6bps + SEC sell 0.24bps = 0.84bps
- 30% 이상: "cost assumptions reasonable"
- > 80%: "cost assumptions too generous"
- < 20%: "cost assumptions too pessimistic or edge too small"

---

### 1.6 Verdict Reproducibility

**공식**:
```
reproducibility = COUNT(replay_match == True) / COUNT(replays)
```

- Snapshot Broker로 기록된 Bigdata MCP 응답 재실행
- 동일 응답 → `replay_match = True`
- 모델/프롬프트 변경 시 기대값 < 100% (좋음)
- 확정적 논리 변경 없이 변경되면 → 경고

---

## 2. 측정 윈도우

### 2.1 Sprint 4 게이트 (Validation Window)

| 항목 | 값 |
|-----|-----|
| 기간 | 2025-12-01 ~ 2026-02-28 (90일) |
| IS/OOS split | 70/30 (63일 / 27일) |
| 측정 기준 | Hindcast (과거 데이터 역행) |
| 수업 | Snapshot Broker 우선 사용 |

---

### 2.2 Phase 2+ (Production Rolling Window)

| 항목 | 값 |
|-----|-----|
| Sharpe | Rolling 60일 + cumulative since launch |
| MDD | 누적 (달력 기준, 리셋 없음) |
| OOS degradation | 매월 재검증 (새 30일 OOS cycle) |
| 모니터링 빈도 | 일일 (장 종료 후) |

---

## 3. Threshold 근거 (왜 0.8?)

### 3.1 벤치마크 비교

| 기준 | Sharpe |
|-----|--------|
| S&P 500 long-only (10y avg) | 0.45 |
| Active manager median | 0.5-0.7 |
| **GLOSTAT 목표 (정당화 가능)** | **0.8** |
| 보수적 모드 (research) | 0.6 |
| 기관 수준 (institutional) | 1.0 |

### 3.2 근거

- 0.45-0.7: Passive 대비 "정당화 불가" (수수료 커버 실패)
- 0.8: 3개 Expert + US 2개 시장 한정 시 "reasonable active" 수준
- 0.6: Phase 1에서 너무 관대 (alpha illusion)
- 1.0: Phase 2+에서 고려 가능 (9개 Expert + 다중 시장)

---

## 4. Auto-Shutdown 메커니즘 (Python 코드 스켈레톤)

### 4.1 KillCriteriaMonitor 클래스

```python
from enum import Enum
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timedelta

class KillDecision(Enum):
    CONTINUE = "continue"
    SUSPEND_7D = "suspend_7d"  # 1회 defer 허용
    SHUTDOWN = "shutdown"       # 즉시 폐기

@dataclass
class KillEvaluation:
    decision: KillDecision
    violated_metrics: list[str]
    timestamp: datetime
    reason: str
    evidence: dict  # metric값들

class KillCriteriaMonitor:
    def __init__(self, config: dict):
        self.sharpe_threshold = config.get('sharpe_threshold', 0.8)
        self.maxdd_threshold = config.get('maxdd_threshold', 0.15)  # 15%
        self.oos_degradation_threshold = config.get('oos_degradation_threshold', 0.30)
        self.auc_threshold = config.get('auc_threshold', 0.60)
        self.violation_grace_days = 5  # Sharpe만

    def evaluate(self, metrics: dict) -> KillEvaluation:
        """
        메트릭 평가 → Kill Decision 반환
        
        Args:
            metrics: {
                'phase': str,  # 'sprint4' | 'phase2' | 'phase3'
                'sharpe_60d': float,
                'sharpe_is': float,
                'sharpe_oos': float,
                'maxdd': float,
                'auc_buy': float,
                'cost_passed_ratio': float,
                'consecutive_violation_days': int  # Sharpe 연속 위반
            }
        
        Returns:
            KillEvaluation(decision, violated_metrics, ...)
        """
        violated = []
        evidence = {}

        # 1. Sharpe check (grace: 5연속 trading days)
        sharpe_current = metrics.get('sharpe_60d', 0)
        evidence['sharpe_60d'] = sharpe_current
        
        if sharpe_current < self.sharpe_threshold:
            if metrics.get('consecutive_violation_days', 0) >= 5:
                violated.append('sharpe_below_0.8_sustained')

        # 2. MDD check (즉시, 누적이므로 단발도 의미)
        maxdd = metrics.get('maxdd', 0)
        evidence['maxdd'] = maxdd
        
        if maxdd > self.maxdd_threshold:
            violated.append('maxdd_exceeds_15pct')

        # 3. OOS degradation (2 consecutive validation cycles)
        oos_deg = metrics.get('oos_degradation', 0)
        evidence['oos_degradation'] = oos_deg
        
        if oos_deg > self.oos_degradation_threshold:
            if metrics.get('consecutive_cycles_failed', 0) >= 2:
                violated.append('oos_degradation_sustained')

        # 4. AUC check (hindcast only, sprint4)
        if metrics.get('phase') == 'sprint4':
            auc = metrics.get('auc_buy', 0)
            evidence['auc_buy'] = auc
            
            if auc < self.auc_threshold:
                violated.append('auc_below_0.60')

        # 5. Cost-passed ratio (sanity check, not kill by itself)
        cost_ratio = metrics.get('cost_passed_ratio', 0)
        evidence['cost_passed_ratio'] = cost_ratio
        
        if cost_ratio < 0.05:
            violated.append('warning_cost_ratio_too_low')
        elif cost_ratio > 0.95:
            violated.append('warning_cost_ratio_too_high')

        # Decision logic
        if len(violated) >= 2:
            decision = KillDecision.SHUTDOWN
            reason = f"Multiple metrics violated: {', '.join(violated)}"
        elif len(violated) == 1 and any(
            x in violated[0] for x in ['sharpe', 'maxdd', 'oos']
        ):
            decision = KillDecision.SHUTDOWN
            reason = f"Single metric violation: {violated[0]}"
        elif len(violated) == 1 and 'warning' in violated[0]:
            decision = KillDecision.CONTINUE
            reason = "Warning flagged, monitoring"
        else:
            decision = KillDecision.CONTINUE
            reason = "All metrics within threshold"

        return KillEvaluation(
            decision=decision,
            violated_metrics=violated,
            timestamp=datetime.utcnow(),
            reason=reason,
            evidence=evidence
        )

    def log_decision(self, eval: KillEvaluation, user_override: Optional[str] = None):
        """
        Decision을 audit log + DB에 저장
        user_override = "deferred_7d_reason:..." 면 1회 defer 허용
        """
        pass
```

---

## 5. False-Positive 방어

### 5.1 Sustained vs Single Violation

| 메트릭 | 판정 | Grace Period |
|-------|------|-------------|
| **Sharpe < 0.8** | Sustained | 5 consecutive trading days |
| **MDD > 15%** | Immediate | 없음 (누적이므로 의미) |
| **OOS degradation > 30%** | Sustained | 2 consecutive validation cycles (월 1회) |
| **AUC < 0.60** | Immediate (sprint4) | 없음 |

### 5.2 예시

- Day 1: Sharpe 0.78 → 위반 기록, 계속 모니터링
- Day 2-4: Sharpe 0.75~0.77 → 위반 누적
- Day 5: Sharpe 0.76 → 5일 연속 → SHUTDOWN trigger
- Day 5: Sharpe 0.85 (회복) → 위반 카운터 리셋

---

## 6. 모니터링 대시보드 (localhost:7100/kill_criteria)

### 6.1 레이아웃

```
┌─────────────────────────────────────────────────────────────┐
│ GLOSTAT Kill Criteria Monitor                  Phase: phase2 │
├─────────────────────────────────────────────────────────────┤
│                                                               │
│  ┌─ Sharpe (60d rolling) ──────────────────────────────┐   │
│  │ Current: 0.82  │███████████░░░│  Target: ≥ 0.8       │   │
│  │ Violation: None  Consecutive: 0/5 days             │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─ Max Drawdown ──────────────────────────────────────┐   │
│  │ Current: -8.3%  │░░███░░░░░░░░│  Threshold: ≤ -15% │   │
│  │ Status: OK                                          │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─ OOS Degradation ───────────────────────────────────┐   │
│  │ IS: 1.10  OOS: 0.88  Degradation: 20%             │   │
│  │ Threshold: ≤ 30%  Cycles: 1/2 violations          │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  ┌─ Kill Probability Heatmap ──────────────────────────┐   │
│  │ Metric       1w      2w      4w      8w             │   │
│  │ Sharpe       5%      3%      2%      1%             │   │
│  │ MDD          2%      1%      1%      0%             │   │
│  │ OOS_Deg      10%     8%      5%      2%             │   │
│  │ AUC          0%      0%      0%      0%             │   │
│  │ Multi        2%      1%      0%      0%             │   │
│  └──────────────────────────────────────────────────────┘   │
│                                                               │
│  Days to Mandatory Shutdown: 157 (target: 180)              │
│  Last Eval: 2026-04-28 16:45 UTC  Next: 2026-04-29        │
│                                                               │
│  [View Detailed Logs] [Export CSV] [Manual Eval]            │
└─────────────────────────────────────────────────────────────┘
```

### 6.2 주요 위젯

1. **Sharpe Gauge**: 현재/target, violation 카운터
2. **MDD Progress Bar**: % to threshold
3. **OOS vs IS Divergence Chart**: 시계열 (월 단위)
4. **Kill Probability Heatmap**: 5메트릭 × 시간 축 (ML 기반 예측, optional)
5. **Countdown**: "Days to mandatory shutdown" (Phase 2: 180일 from launch)

---

## 7. Override 정책 (E10 권고)

### 7.1 기본 원칙

**우회 금지.** Sprint 4 FAIL = 즉시 shutdown. Phase 2+ KILL = 즉시 shutdown.

### 7.2 예외: Deferred Shutdown (1회만)

조건:
- Kill decision 수령 후 2시간 이내
- 사용자가 `/kill_criteria defer --reason "문제 설명"` 입력
- `kill_reason.md` 자동 생성, audit log 보존
- 7일 deferral, 그 후 재평가
- **2회 이상 defer 시도 → 거부**

```markdown
## Kill Criteria Deferral Log
Date: 2026-04-28T16:45:00Z
Decision: SHUTDOWN (Sharpe < 0.8, MDD > 15%)
Reason: "Investigating broker connectivity issue, cost assumptions need revalidation"
Days Deferred: 7
Next Eval: 2026-05-05T16:45:00Z
```

---

## 8. Phase 별 Differentiated Thresholds

### 8.1 Phase 1 (Sprint 4, Validation)

| 메트릭 | Threshold |
|--------|-----------|
| Hindcast Sharpe | ≥ 0.8 |
| OOS degradation | ≤ 30% |
| Hindcast AUC | ≥ 0.60 |
| Cost-passed | 30-80% |
| Decision | PASS/FAIL (no continue) |

### 8.2 Phase 2 (Live, 6개월)

| 메트릭 | Threshold |
|--------|-----------|
| Rolling Sharpe | ≥ 0.8 |
| MDD | ≤ 15% |
| OOS degradation | ≤ 30% (월 재검증) |
| Verdict reproducibility | ≥ 95% |
| Decision | CONTINUE/SUSPEND_7D/SHUTDOWN |

### 8.3 Phase 3 (Cascade A/B Test)

| 메트릭 | Threshold |
|--------|-----------|
| Control Sharpe | ≥ 0.8 |
| Cascade Sharpe lift | ≥ 0.2 (control 대비) |
| Cost-passed ratio | ≥ 30% |
| Decision | CONTINUE/SHUTDOWN (cascade only research if < 0.2 lift) |

---

## 9. 종료 시 Archive 정책

### 9.1 Kill 후 보존 대상

```
/Applications/GLOSTAT/archive/glostat-v0.4-kill-2026-04-28/
├── snapshot_db.parquet          # 모든 Bigdata MCP 호출
├── verdict_chain.ndjson         # 모든 verdict (30일)
├── metrics_timeline.csv          # 모든 kill criteria 메트릭
├── kill_reason.md                # 종료 이유 + 증거
├── config_snapshot.yaml          # 실행 당시 설정
├── post_mortem_template.md       # 재기동 가이드
└── audit_log.ndjson             # 모든 decision + override
```

### 9.2 Post-Mortem Template

```markdown
# GLOSTAT v0.4 Post-Mortem (Killed 2026-04-28)

## Kill Reason
Sustained Sharpe < 0.8 for 5 consecutive trading days (Phase 2, day 47)

## Root Cause Analysis
- [ ] E_FUNDAMENTAL signal decayed (calibration age > 90d?)
- [ ] E_FUND_FLOW signals lagged regime shift
- [ ] E_TIME horizon misalignment (30d assumption)
- [ ] Cost assumptions too aggressive
- [ ] OOS degradation = 35% (calibration overfitting)

## Evidence
- Last 30d verdicts: 12 BUY, 8 HOLD, 5 SELL
- Average edge: 32 bps
- Average cost: 28 bps
- Success rate: 52% (random walk: 50%)

## Lessons Learned
1. E_NARRATIVE (60d lookback) would have flagged ...
2. Portfolio risk constraints needed ...
3. Regime transition detection could improve ...

## v0.5 Action Items
- [ ] Extend IS/OOS split to 80/20 (was 70/30)
- [ ] Add E_MACRO regime layer
- [ ] Reduce E_TIME weight from 25% to 15%
- [ ] Hard-code MDD ≤ 12% constraint (vs 15%)

## Restart Conditions (all required)
- [ ] 3-month cool-off period (until 2026-07-28)
- [ ] v0.5 plan approved
- [ ] 90d hindcast on new hypothesis (Sharpe ≥ 0.8)
- [ ] Explicit user approval
```

---

## 10. 재기동 정책

### 10.1 조건 (모두 충족 필수)

1. **3개월 cool-off**: 종료 후 90일 경과
2. **v0.5 계획 작성**: 새 assumption, 신호 변경 명시
3. **신규 hindcast 검증**: 2025년 이후 data, Sharpe ≥ 0.8 증명
4. **명시적 사용자 승인**: `/start_glostat_v0.5` command

### 10.2 재기동 후 새 규칙

- MDD threshold를 이전 0.15 → 0.12로 강화
- Phase 2 진입 전 추가 30d validation 필수
- Deferred shutdown 불가능 (이전 defer 이력 시 즉시 shutdown)

---

## 결론

E10의 "Cascade는 연구, 사용자 가치 아님" 통찰은 Kill Criteria의 중요성을 부각한다. 명시적 메트릭 (Sharpe 0.8, MDD 15%), 자동 판정 로직, 그리고 **우회 불가능한** shutdown 메커니즘이 Zombie project를 방지하는 유일한 방법이다. 본 설계는 Phase별로 threshold를 차등화하되, 핵심 원칙 (validation-first, scope-discipline)을 모든 단계에서 유지한다.
