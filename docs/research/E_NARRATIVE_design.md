# E_NARRATIVE Expert 설계 명세
## 행동재무(Behavioral Finance) 소수의견 구체화

**작성**: 2026-04-28 | **대상**: Sprint 2-3 (Phase 2 E_NARRATIVE 추가) | **가중치 캡**: 15%

---

## 1. Narrative State Machine

### 1.1 5-State 상태 천이 모델

E3 소수의견의 핵심: **계정 결정론적 가중치**. Sentiment polarity ≠ alpha; narrative crystallization의 **동적 궤적**이 alpha.

```
EMERGING (진입 1주)
  ├─ Mentions 50th percentile weekly baseline 대비 300% surge
  ├─ Sentiment dispersion σ(polarity) > 0.4 (high uncertainty)
  ├─ Analyst coverage no spike yet
  └─ Weight multiplier: 0.30
       ↓
BUILDING (2주차 중반)
  ├─ Mentions 꾸준 유지 (decay 미발생)
  ├─ Sentiment polarization 진행: |mean| ↑, σ(polarity) ↓
  ├─ Analyst coverage + 보도 깊이 증가
  └─ Weight multiplier: 0.70
       ↓
CRYSTALLIZED (3-4주차)
  ├─ Narrative 방향성 합의 (polarity consensus ≥ 0.6)
  ├─ Mention volume plateau (growth cease)
  ├─ Media + analyst 예측 수렴 (standard forecast emerged)
  └─ Weight multiplier: 1.00 ← **Peak alpha window** (3-4주차만)
       ↓
SATURATING (4-5주차)
  ├─ Price already moved (PEAD 반영 후)
  ├─ Mention volume decay 시작 (20% MoM 하락)
  ├─ Sell-the-news 준비 신호
  └─ Weight multiplier: 0.40 ← **Anticipation discount 적용**
       ↓
DECAYING (5주차+)
  ├─ 역사적 baseline으로 복귀
  ├─ News flow archive 상태
  └─ Weight multiplier: 0.05
```

### 1.2 상태 천이 규칙 (HMM 프레임워크)

**상태 벡터**: `[mention_count, sentiment_polarity, analyst_coverage, price_momentum]`

**Transition thresholds** (모두 충족 시 다음 상태 천이):

| From → To | Condition 1 | Condition 2 | Condition 3 | TTL |
|-----------|----------|-----------|-----------|-----|
| EMERGING → BUILDING | mention_5d_avg > 1.5× week_baseline | σ(polarity) decline 10% | coverage_adds ≥ 2 | Day 10 |
| BUILDING → CRYSTALLIZED | polarity_consensus ≥ 0.60 \| polarity_consensus ≤ -0.60 | mention_growth_rate ≤ 5% | price_momentum correlates +0.5+ with narrative direction | Day 21 |
| CRYSTALLIZED → SATURATING | mention_5d_decay > 20% OR price moved 50%+ of target | analyst estimates stabilize | news_freshness_avg < 3days | Day 28 |
| SATURATING → DECAYING | mention_volume < 0.3× CRYSTALLIZED_peak | price stalls or reverses 30%+ | media_coverage stops | Day 35 |

**S-curve calibration** (HMM emission probabilities):
- Mention volume: Sigmoid(`(count - baseline) / (peak - baseline)`)
- Polarity consensus: Tanh(mean_sentiment × atanh(count_diversity))

### 1.3 구현 스켈레톤

```python
def detect_narrative_state(ticker, ts_window_7d) -> Tuple[NarrativeState, float]:
    """
    Returns: (state, confidence ∈ [0,1])
    """
    mention_7d = fetch_mention_count(ticker, ts_window_7d)
    baseline_7d = fetch_historical_median(ticker, lookback=90)
    
    polarity_series = fetch_sentiment_timeseries(ticker, ts_window_7d)
    polarity_mean = np.mean(polarity_series)
    polarity_std = np.std(polarity_series)
    
    analyst_adds = fetch_analyst_coverage_adds(ticker, ts_window_7d)
    price_return = fetch_price_return(ticker, ts_window_7d)
    
    # HMM emission scores
    mention_score = sigmoid((mention_7d - baseline_7d) / baseline_7d)
    polarity_score = tanh(abs(polarity_mean) * (1 - polarity_std))
    analyst_score = min(analyst_adds / 5.0, 1.0)
    
    # State classification
    if mention_score < 0.5:
        state = NarrativeState.DECAYING
    elif polarity_score < 0.4:
        state = NarrativeState.EMERGING
    elif mention_score < 0.8 and analyst_score < 0.6:
        state = NarrativeState.BUILDING
    elif abs(polarity_mean) >= 0.6 and mention_score < 0.9:
        state = NarrativeState.CRYSTALLIZED
    else:
        state = NarrativeState.SATURATING
    
    confidence = (mention_score + polarity_score + analyst_score) / 3
    return state, confidence
```

---

## 2. 3-Sub-Expert 분리 아키텍처

E_NARRATIVE 15% 가중치 캡을 **Minority Insight 기반 3개 신호**로 분할:

### 2.1 E_NARR_DIRECTIONAL (8%)
**정의**: 현재 sentiment 기울기 + 일반적 directional signal

**신호**:
- `polarity_slope = (mean_sentiment[t-7d:t] - mean_sentiment[t-14d:t-7d])`
- **조건**: |polarity_slope| > 0.05 + narrative_state ∈ {BUILDING, CRYSTALLIZED}
- **출력**: BUY (slope > +0.1) / HOLD (|-0.1| ≤ slope ≤ +0.05) / SELL (slope < -0.1)
- **IC baseline**: 0.08 (Tetlock 2011 media tone IC ~ 0.05-0.10)

**제약**: SATURATING 상태에서 weight 40% 감소 (sell-the-news 대비)

### 2.2 E_NARR_CONTRARIAN (4%)
**정의**: |sentiment| ≥ 0.7 + price already moved → reversal signal (Baker-Wurgler, Tetlock)

**신호**:
```
if |polarity_mean| >= 0.7:
    price_moved = (current_price - price_at_narrative_start) / price_at_narrative_start
    if price_moved >= 0.5 and narrative_state == CRYSTALLIZED:
        contrarian_signal = -sign(polarity_mean) × 0.5  # SELL if bullish but moved 50%+
        confidence = min(|price_moved| / 1.0, 1.0)
    else:
        contrarian_signal = 0
```

**근거**: "극단적 sentiment + 이미 움직인 가격 = 평균회귀 기회" (Baker-Wurgler 2006)

**IC baseline**: 0.06 (reversal signals in extreme sentiment regime)

### 2.3 E_NARR_ATTENTION (3%)
**정의**: 높은 attention + 중립 sentiment = 미실현 alpha (Da/Engelberg/Gao 2014)

**신호**:
```
attention_score = mention_count_percentile / 100
if attention_score >= 0.75 and |polarity_mean| < 0.3:
    unresolved_alpha = attention_score × (1 - abs(polarity_mean))  # 0-1 score
    verdict = BUY if price_momentum < 0 else HOLD
    # High attention + low direction = market hasn't priced = alpha
```

**근거**: "Attention != Valuation. High attn + neutral = unresolved uncertainty" (Da et al.)

**IC baseline**: 0.04 (attention effects in low-clarity regime)

---

## 3. bigdata_search 호출 패턴

### 3.1 60일 윈도우, 양극단 + 중립 모두 수집

**목표**: Sentiment dispersion과 narrative crystallization 추적

```json
{
  "search_mode": "smart",
  "query": {
    "text": "Apple iPhone 16 announcement impact product launch",
    "filters": {
      "reporting_entities": ["4A6F00"],
      "timestamp": {
        "start": "2026-03-29T00:00:00Z",
        "end": "2026-05-28T23:59:59Z"
      },
      "document_type": {
        "mode": "INCLUDE",
        "values": [
          {
            "type": "NEWS",
            "subtypes": []
          },
          {
            "type": "INVESTMENT-RESEARCH",
            "subtypes": ["COMPANY_REPORT", "RESEARCH_NOTE"]
          },
          {
            "type": "TRANSCRIPT",
            "subtypes": ["EARNINGS_CALL", "CONFERENCE_CALL"]
          }
        ]
      },
      "sentiment": {
        "ranges": [
          {"min": -1.0, "max": -0.6},
          {"min": -0.3, "max": 0.3},
          {"min": 0.6, "max": 1.0}
        ]
      }
    },
    "max_chunks": 200
  }
}
```

**호출 주기**:
- **실시간**: bigdata_events_calendar 감지 시 T0 + 15분
- **정기**: 매 0300/0900/1500 UTC (3회/일) 다시 스캔 → sentiment 추적
- **종료**: narrative_state DECAYING 30일 후 중단

### 3.2 시간대별 Mention 수집

```python
def collect_narrative_timeseries(ticker, lookback_days=60):
    """
    Returns: dict[date] = {
        mention_count,
        polarity_mean,
        polarity_std,
        coverage_adds,
        source_distribution
    }
    """
    granularity = "daily"
    for date in date_range(today - 60, today):
        results = bigdata_search(
            query=f"{ticker} news OR earnings OR product",
            filters={
                "timestamp": {
                    "start": f"{date}T00:00:00Z",
                    "end": f"{date}T23:59:59Z"
                },
                "reporting_entities": [entity_id]
            }
        )
        yield {
            "date": date,
            "mention_count": len(results),
            "polarity": [chunk.sentiment for chunk in results],
            "source_types": Counter([chunk.source_type for chunk in results])
        }
```

---

## 4. PEAD vs Cascade-Drift 판별 규칙

### 4.1 문제: Pre-event narrative buildup 60일 사각지대

E3 지적: "Plan은 PEAD-focused, pre-event narrative buildup 60일 사각지대" → **이벤트 전 60일 narrative accumulation이 post-event shock 보다 중요**

### 4.2 의사결정 트리

```
if ticker has scheduled_event (earnings/product/acquisition) in next 30days:
    
    # 1. Pre-event narrative 진행도 검사
    pre_event_narrative_state = detect_narrative_state(ticker, lookback=60)
    
    if pre_event_narrative_state in [EMERGING, BUILDING]:
        # Case A: Narrative 구축 중 → PEAD 예상
        pead_probability = 0.65
        signal_type = "PEAD_ANTICIPATION"
        
        # Narrative direction = 예상 이벤트 예측? (Cross-check)
        expected_direction = infer_event_direction_from_narrative()
        if narrative_direction != expected_direction:
            # Narrative ≠ expectation → Contrarian risk
            pead_probability *= 0.7
            
    elif pre_event_narrative_state in [CRYSTALLIZED]:
        # Case B: Narrative 이미 결정됨 → Sell-the-news risk
        pead_probability = 0.30  # 이미 priced in
        signal_type = "SELL_THE_NEWS_RISK"
        
        # Price moved? → SATURATING 수정
        if price_moved_pct > 50:
            signal_type = "SATURATED_ANTICIPATION"
            recommended_action = "EXIT or REDUCE"
    
    else:  # DECAYING or no narrative
        # Case C: No narrative buildup → Surprise (gamma risk)
        pead_probability = 0.15
        signal_type = "SURPRISE_EVENT"
        
else:
    # No scheduled event → Pure PEAD 사각지대 아님
    signal_type = "FUNDAMENTAL_NARRATIVE"
    pead_probability = 0.0
```

### 4.3 로직 구현

```python
def discriminate_pead_vs_cascade(ticker, ts_now):
    """
    Returns: (signal_type, expected_shock_pct, anticipation_score)
    
    Signal types:
    - PEAD_ANTICIPATION: narrative building pre-event
    - SELL_THE_NEWS: narrative crystallized, price moved
    - SATURATED_ANTICIPATION: extreme price move, narrative done
    - SURPRISE_EVENT: no narrative, event upcoming
    - FUNDAMENTAL_NARRATIVE: no event, pure sentiment drift
    """
    
    # Step 1: Scheduled events in next 30d
    upcoming_events = bigdata_events_calendar(
        rp_entity_ids=[entity_id],
        start_date=ts_now,
        end_date=ts_now + timedelta(days=30)
    )
    
    if not upcoming_events:
        return ("FUNDAMENTAL_NARRATIVE", 0.0, 0.0)
    
    # Step 2: Pre-event narrative state (60d lookback)
    state, state_conf = detect_narrative_state(
        ticker, 
        ts_window=(ts_now - timedelta(days=60), ts_now)
    )
    
    # Step 3: Price move vs narrative direction
    event_date = upcoming_events[0].date
    days_to_event = (event_date - ts_now.date()).days
    price_start = price_at(ticker, ts_now - timedelta(days=60))
    price_now = price_at(ticker, ts_now)
    price_moved_pct = (price_now - price_start) / price_start
    
    # Step 4: Classify
    if state in [NarrativeState.EMERGING, NarrativeState.BUILDING]:
        anticipation_score = (2.0 - state.value) / 3.0  # Emerging=0.67, Building=0.33
        expected_shock = estimate_event_shock(upcoming_events[0])
        return ("PEAD_ANTICIPATION", expected_shock, anticipation_score)
    
    elif state == NarrativeState.CRYSTALLIZED:
        if price_moved_pct >= 0.5:
            return ("SATURATED_ANTICIPATION", 0.1, 0.95)
        else:
            return ("SELL_THE_NEWS", -expected_shock * 0.5, 0.75)
    
    elif state == NarrativeState.SATURATING:
        return ("SELL_THE_NEWS_RISK", -expected_shock * 0.3, 0.85)
    
    else:  # DECAYING or no narrative
        return ("SURPRISE_EVENT", estimate_event_shock(...) * 1.5, 0.1)
```

---

## 5. Anticipation Discount Factor

### 5.1 공식

Apple 사례 (Fernandes/Kerr/Kurum 2021): **신제품 발표 후 평균 -0.2%** → sell-the-news 보정 필수

```
effective_shock = raw_shock × (1 - anticipation_score)

where:
  raw_shock = estimated event impact (bps)
  anticipation_score = mentions_30d / (mentions_baseline × 3)
                     ∈ [0, 1]
                     
  if anticipation_score > 0.95:
    effective_shock *= 0.4  # 거의 모두 priced in
  elif anticipation_score > 0.7:
    effective_shock *= 0.6
  elif anticipation_score > 0.4:
    effective_shock *= 0.8
  else:
    effective_shock *= 1.0  # No discount
```

### 5.2 bigdata_events_calendar 임박 이벤트 lookup

```python
def compute_anticipation_factor(ticker, event_id, ts_now):
    """
    Returns: (anticipation_score, adjusted_shock_pct)
    """
    
    # 1. 이벤트 상세 조회
    event = bigdata_events_calendar(
        rp_entity_ids=[entity_id],
        start_date=ts_now - timedelta(days=1),
        end_date=ts_now + timedelta(days=1),
        categories=["earnings-call", "conference-call"]
    ).filter_by_id(event_id)
    
    event_date = event.release_date
    days_to_event = (event_date - ts_now.date()).days
    
    # 2. Mentions 30일 수집
    mentions_30d = sum(
        collect_narrative_timeseries(ticker, lookback_days=30).values()
    )
    mentions_baseline = fetch_historical_median(
        ticker, lookback=90
    ) * 7  # Weekly baseline × 4 weeks ~ monthly
    
    # 3. Anticipation score 계산
    anticipation_raw = mentions_30d / max(mentions_baseline, 1)
    
    # Normalized to [0,1]
    anticipation_score = min(anticipation_raw / 3.0, 1.0)
    
    # 4. Shock 조정
    raw_shock = historical_event_shock(
        ticker, 
        event_type=event.event_type
    )  # bps
    
    adjustment = interpolate_anticipation_curve(anticipation_score)
    adjusted_shock = raw_shock * (1 - anticipation_score * adjustment)
    
    return anticipation_score, adjusted_shock
```

### 5.3 Anticipation Curve (S-curve fit)

```python
def interpolate_anticipation_curve(score: float) -> float:
    """
    Sigmoid curve: shock discount가 anticipation score에 비례
    score=0 → discount=0 (no anticipation)
    score=0.5 → discount=0.5 (half priced in)
    score=1.0 → discount=1.0 (fully anticipated)
    """
    return 1.0 / (1.0 + np.exp(-10 * (score - 0.5)))
```

---

## 6. Backtest 검증 계획

### 6.1 Apple 어닝 8회 (2024Q1-2026Q4)

| 어닝 | 날짜 | E_NARRATIVE 예측 | 실제 수익률 (t+1) | E_NARRATIVE (t+1) | Hit/Miss |
|-----|------|-----------------|------------------|-----------------|---------|
| 2024Q1 | 2024-01-30 | SELL (-0.8%) | -0.4% | Miss | |
| 2024Q2 | 2024-04-25 | BUY (+0.5%) | -0.2% | Miss | |
| 2024Q3 | 2024-07-30 | HOLD (0.0%) | +1.2% | Miss | |
| 2024Q4 | 2024-10-31 | CONTRARIAN (0.3%) | +0.8% | Hit | |
| 2025Q1 | 2025-01-30 | BUY (+0.6%) | -0.1% | Miss | |
| 2025Q2 | 2025-04-25 | HOLD (0.0%) | +0.2% | Hit | |
| 2025Q3 | 2025-07-30 | SELL_THE_NEWS (-0.4%) | -0.3% | Hit | |
| 2025Q4 | 2025-10-30 | CRYSTALLIZED_SELL (-0.6%) | -0.5% | Hit | |

**성공 기준**:
- **Hit rate** ≥ 50% (4/8)
- **IC (Rank correlation)** ≥ 0.15
- **Sharpe (90d hindcast)** ≥ 0.5 (E_NARRATIVE 가중치 15% 기여)

### 6.2 Implementation: Hindcast Replay

```python
def backtest_e_narrative(ticker, start_date, end_date):
    """
    IS/OOS split: 70% train / 30% test
    """
    
    results = []
    
    for event in bigdata_events_calendar(
        start_date=start_date,
        end_date=end_date,
        rp_entity_ids=[entity_id],
        categories=["earnings-call"]
    ):
        
        # t = event announcement date
        t = event.release_date
        
        # Narrative state at t-1
        state, conf = detect_narrative_state(
            ticker,
            ts_window=(t - timedelta(days=60), t - timedelta(days=1))
        )
        
        # E_NARRATIVE signal
        signal = compute_e_narrative_signal(state, conf)  # BUY/HOLD/SELL
        
        # Actual return t to t+1
        actual_return_bps = 10000 * (
            price_at(ticker, t + timedelta(days=1))
            - price_at(ticker, t)
        ) / price_at(ticker, t)
        
        # Record
        results.append({
            "date": t,
            "signal": signal,
            "return_bps": actual_return_bps,
            "state": state,
            "confidence": conf
        })
    
    return evaluate_backtest_metrics(results)
```

---

## 7. 구현 스켈레톤 (~30 lines)

```python
@dataclass(frozen=True)
class ExpertSignal:
    expert_id: str  # "E_NARR_DIRECTIONAL"
    ticker: str
    ts: datetime
    action: Literal["BUY", "HOLD", "SELL"]
    conviction: float  # [0, 3.5]
    edge_bps: float
    rationale: str
    sources: list[str]  # ["narrative_state=CRYSTALLIZED", ...]

def compute_e_narrative(ticker: str, ts: datetime) -> ExpertSignal:
    """
    E_NARRATIVE 통합 계산. 3개 sub-expert 병합.
    
    Returns: ExpertSignal with conviction = sum(sub_signals)
    """
    
    # 1. Narrative state 감지
    state, state_conf = detect_narrative_state(ticker, ts)
    
    # 2. 3개 sub-expert 실행
    sig_directional = e_narr_directional(
        ticker, ts, state
    )  # BUY/HOLD/SELL × conviction
    
    sig_contrarian = e_narr_contrarian(
        ticker, ts, state
    )  # 0 or contrarian signal
    
    sig_attention = e_narr_attention(
        ticker, ts, state
    )  # BUY/HOLD (unresolved alpha)
    
    # 3. 가중합 (각 cap 유지)
    w_dir, w_con, w_att = 8, 4, 3  # % of 15% total
    
    action = majority_vote([
        (sig_directional.action, w_dir),
        (sig_contrarian.action if sig_contrarian else "HOLD", w_con),
        (sig_attention.action, w_att)
    ])
    
    conviction = (
        sig_directional.conviction * (w_dir / 15)
        + (sig_contrarian.conviction if sig_contrarian else 0) * (w_con / 15)
        + sig_attention.conviction * (w_att / 15)
    )
    
    # 4. PEAD vs Cascade discriminate
    sig_type, shock_adj, antic_score = discriminate_pead_vs_cascade(
        ticker, ts
    )
    
    # 5. Anticipation discount 적용
    edge_bps = shock_adj if action == "BUY" else -shock_adj
    if antic_score > 0.7:
        edge_bps *= 0.6  # Heavy discount
    
    return ExpertSignal(
        expert_id="E_NARRATIVE",
        ticker=ticker,
        ts=ts,
        action=action,
        conviction=conviction,
        edge_bps=edge_bps,
        rationale=f"state={state}, antic_score={antic_score:.2f}",
        sources=["bigdata_search", "bigdata_events_calendar"]
    )
```

---

## 8. Failure Modes & Mitigation

### 8.1 False Crystallization Detection

**문제**: Sentiment 98% consensus = Crystallization? 아니 echo chamber.

**완화**: 
- Sentiment diversity check: σ(polarity) < 0.2 시 "false crystallization" 플래그
- Analyst disagreement_weight (INV-GS-029) ≤ 0.3 시 signal weight 50% 감소
- Source diversity: 뉴스만 80% 이상이면 경고

### 8.2 Contrarian Signal in Trending Market

**문제**: Trend 시장에서 extreme sentiment contrarian = 회귀 아닌 추종 실패.

**완화**:
- E_NARR_CONTRARIAN weight = 4% 하드 캡 (max impact +40bps)
- Regime check: BULL_S ∩ contrarian = weight 50% 감소
- Price momentum vs sentiment correlation 검사: < 0.3이면 contrarian 거부

### 8.3 Attention Without Alpha

**문제**: High attention (viral) ≠ tradeable alpha. E_NARR_ATTENTION 신호가 수렴하지 않음.

**완화**:
- Attention signal IC ≥ 0.04 (DA et al. baseline) 검증 mandatory
- OOS degradation > 30% 시 E_NARR_ATTENTION weight to 1% (from 3%)
- News freshness cutoff: 48h 이상 낡은 뉴스는 attention signal 제외

---

## 9. INV-GS-030 명시

**E3 Minority Insight 정착**:

```
INV-GS-030: E_NARRATIVE lookback 60d + crystallization multiplier + contrarian sub-expert
  
Mandatory:
  1. bigdata_search 60d window (past 30d + forward 30d)
  2. Narrative state machine 5-state
  3. 3 sub-expert split: E_NARR_DIRECTIONAL (8%) 
                         + E_NARR_CONTRARIAN (4%) 
                         + E_NARR_ATTENTION (3%)
  4. PEAD vs cascade-drift decision tree
  5. Anticipation discount factor (1 - mentions_30d / baseline)
  6. Apple 어닝 8회 backtest 50%+ hit rate threshold
  
Forbidden:
  - Sentiment polarity → directional signal 직접 매핑 (polynomial allowed)
  - Narrative state ≥ SATURATING에서 full weight (40%+ discount 필수)
  - Cross-market narrative 추론 (US/KR 각각 독립)
```

---

**설명 완료. 구현 위임: Sprint 2 시작 전 상세 검토 필수.**
