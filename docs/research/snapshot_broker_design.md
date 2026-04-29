# Snapshot Broker + Merkle Tree 아키텍처 명세

**작성**: 2026-04-28 | **기반**: PLAN_v0.4 Section 2 Sprint 0, INV-GS-022, E5 MLOps minority insight

---

## 1. 저장 레이아웃

### 로컬 캐시 구조 (MVP)

```
local cache/
├── snapshots/
│   ├── 2026/
│   │   ├── 04/
│   │   │   ├── 28/
│   │   │   │   ├── 10/
│   │   │   │   │   └── snap_1234567890abcdef.parquet
│   │   │   │   │   └── snap_1234567890abcdef.parquet
│   │   │   │   └── 11/
│   │   │   │       └── snap_...parquet
│   │   │   └── 29/
│   ├── snapshots_root.txt          # Daily Merkle root 저장
│   └── index.db                    # SQLite 인덱스 (MVP)
```

### SQLite 인덱스 스키마 (MVP)

```sql
CREATE TABLE snapshots (
    snapshot_id TEXT PRIMARY KEY,    -- snap_YYYYMMDDHHmmssUUID (52B)
    request_hash TEXT NOT NULL,      -- SHA256(canonical_json(request)) (64B)
    response_hash TEXT NOT NULL,     -- SHA256(canonical_json(response)) (64B)
    ts_utc INTEGER NOT NULL,         -- Unix timestamp (ms)
    source_tool TEXT NOT NULL,       -- "bigdata_company_tearsheet", "bigdata_events_calendar", ...
    ttl_ms INTEGER DEFAULT 86400000, -- 1day default (adjusted per edge_type)
    sources TEXT NOT NULL            -- JSON array [{"tool": "...", "edge_type": "SUPPLIES_TO"}]
);

CREATE INDEX idx_request_hash ON snapshots(request_hash);
CREATE INDEX idx_ts_utc ON snapshots(ts_utc);
CREATE INDEX idx_ts_utc_tool ON snapshots(ts_utc, source_tool);
```

### Phase 2 옵션: S3 + DynamoDB

```
s3://glostat-snapshots/
├── YYYY/MM/DD/HH/
│   └── snap_{snapshot_id}.parquet

DynamoDB: snapshots-index (global secondary index on request_hash + ts_utc)
```

---

## 2. Write Path: `save_snapshot(request, response) → snapshot_id`

### 알고리즘

```python
def save_snapshot(request: dict, response: dict) -> str:
    # 1. Canonical JSON 직렬화 (정렬된 키)
    req_json = canonical_json(request)
    resp_json = canonical_json(response)
    
    # 2. 해시 계산
    request_hash = hashlib.sha256(req_json.encode()).hexdigest()
    response_hash = hashlib.sha256(resp_json.encode()).hexdigest()
    
    # 3. Snapshot ID 생성
    ts_utc_ms = int(time.time() * 1000)
    uuid = str(uuid4())[:8]
    snapshot_id = f"snap_{ts_utc_ms}{uuid}"
    
    # 4. Parquet 저장
    shard_path = f"snapshots/{ts_utc_ms//86400000}/{(ts_utc_ms%86400000)//3600000}/"
    os.makedirs(shard_path, exist_ok=True)
    
    df = pd.DataFrame([{
        "snapshot_id": snapshot_id,
        "request_hash": request_hash,
        "response_hash": response_hash,
        "ts_utc_ms": ts_utc_ms,
        "source_tool": request.get("tool_name", "unknown"),
        "ttl_ms": EDGE_TYPE_TTL.get(request.get("edge_type"), 86400000),
        "response_preview": resp_json[:1024]
    }])
    
    df.to_parquet(f"{shard_path}{snapshot_id}.parquet", index=False)
    
    # 5. SQLite 인덱스 업데이트
    insert_to_index(snapshot_id, request_hash, response_hash, ts_utc_ms, ...)
    
    # 6. 일일 Merkle 트리에 leaf 추가
    append_merkle_leaf(response_hash)
    
    return snapshot_id
```

---

## 3. Merkle Tree 구조

### 일일 이진 트리 (Binary Merkle Tree)

**목표**: 1000개 verdict/min 시 전체 일일 응답의 결정론적 검증

```
         Daily Root (저장)
            /        \
          H(0-1)     H(2-3)
          /    \     /    \
        H(0)  H(1) H(2)  H(3) ...
        |      |     |      |
      leaf0 leaf1 leaf2 leaf3  ... leaf_N
       
leaf_i = SHA256(response_hash_i)
```

### 알고리즘

```python
def build_daily_merkle_tree(leaves: list[str]) -> str:
    """
    leaves: [response_hash_0, response_hash_1, ...]
    returns: root_hash (64B hex string)
    """
    if not leaves:
        return sha256(b"").hexdigest()
    
    # Leaf 생성: SHA256(response_hash)
    current_level = [sha256(leaf.encode()).hexdigest() for leaf in leaves]
    
    # 홀수 개 노드 처리: 마지막 노드 복제
    while len(current_level) > 1:
        if len(current_level) % 2 == 1:
            current_level.append(current_level[-1])
        
        next_level = []
        for i in range(0, len(current_level), 2):
            pair_hash = sha256(
                (current_level[i] + current_level[i+1]).encode()
            ).hexdigest()
            next_level.append(pair_hash)
        
        current_level = next_level
    
    return current_level[0]

def save_daily_root(date: str, root: str):
    """snapshots_root.txt: YYYY-MM-DD,root_hash"""
    with open("snapshots_root.txt", "a") as f:
        f.write(f"{date},{root}\n")
```

---

## 4. Read Path / Replay: `replay(verdict_id) → bool`

### 결정론적 재생 (Deterministic Replay)

```python
def replay(verdict_id: str, original_snapshot_ids: list[str]) -> bool:
    """
    원본 verdict가 저장된 snapshot_ids로부터
    응답을 재구성하고 동일 response_hash 검증
    """
    try:
        # 1. 원본 snapshot 로드
        snapshots = []
        for snap_id in original_snapshot_ids:
            path = get_snapshot_path(snap_id)
            df = pd.read_parquet(path)
            snapshots.append(df)
        
        combined = pd.concat(snapshots, ignore_index=True)
        
        # 2. 원본 response_hash 검증
        for _, row in combined.iterrows():
            stored_hash = row['response_hash']
            # 실제 재실행은 source_tool 호출 (MCP cache hit 기대)
            # 또는 stored response preview에서 복원
            recomputed = recompute_response(row)
            recomputed_hash = sha256(canonical_json(recomputed).encode()).hexdigest()
            
            if recomputed_hash != stored_hash:
                log_mismatch(verdict_id, snap_id, stored_hash, recomputed_hash)
                return False
        
        return True
        
    except FileNotFoundError:
        log_snapshot_missing(verdict_id, original_snapshot_ids)
        return False
```

---

## 5. Cache Stampede 완화

### Single-Flight AsyncIO Lock (요청 중복 제거)

```python
class SnapshotCache:
    def __init__(self):
        self._locks = {}  # request_hash → asyncio.Lock
    
    async def get_or_compute(self, request: dict):
        request_hash = compute_hash(request)
        
        if request_hash not in self._locks:
            self._locks[request_hash] = asyncio.Lock()
        
        async with self._locks[request_hash]:
            # 첫 진입 스레드가 캐시 체크 및 계산
            cached = await self._check_cache(request_hash)
            if cached:
                return cached
            
            response = await call_mcp_tool(request)
            snapshot_id = save_snapshot(request, response)
            
            return response
```

### β-XFetch 알고리즘 (핫키 조기 갱신)

```python
def should_early_refresh(request_hash: str, ttl_ms: int) -> bool:
    """
    확률적 조기 갱신: 만료 전 β*TTL 지점부터
    β=0.1 → 90% 만료 시점에서 10% 확률로 갱신
    """
    age_ms = current_timestamp_ms() - snapshot_created_at(request_hash)
    
    if age_ms < ttl_ms * 0.9:
        return False
    
    # 베타 함수로 확률 증가
    β = 0.1
    probability = β * (age_ms - ttl_ms * 0.9) / (ttl_ms * 0.1)
    
    return random.random() < min(probability, 1.0)
```

---

## 6. Edge Type별 TTL

**출처**: PLAN_v0.3 §3.2 + 확장

| Edge Type | TTL | 근거 | MVP |
|-----------|-----|------|-----|
| SUPPLIES_TO | 91d | 공급망 변화 느림 | × (Phase 2) |
| COMPETES_WITH | 30d | 시장 경쟁 중기 변화 | × |
| GEO_EXPOSES | 91d | 지리적 노출 장기 | × |
| THEMATICALLY_LINKED | 14d | 주제 유연성 | × |
| MACRO_LAGS | 7d | 거시 지표 주간 재갱신 | 계획 |
| country_tearsheet | 6h | 정책/통계 일일 갱신 | ✓ |
| company_tearsheet | 1h | 실적 공시/리밸런싱 시간당 | ✓ |
| market_tearsheet | 15m | 시장 데이터 실시간성 | ✓ |
| events_calendar | 24h | 어닝/컨퍼런스 일일 갱신 | ✓ |

---

## 7. 볼륨 스파이크 시나리오 (VIX 50, 1000 verdict/min)

### Parquet 분할 전략

```
snapshots/2026/04/28/10/     # 10:xx 시간
├── snap_...00-09_idx=0.parquet   # 분 단위 샤드
├── snap_...10-19_idx=1.parquet
├── snap_...20-29_idx=2.parquet
├── snap_...30-39_idx=3.parquet
├── snap_...40-49_idx=4.parquet
├── snap_...50-59_idx=5.parquet
```

**병렬 fsync**: 분당 1개 샤드 저장, 6개 병렬 I/O

**배치 Merkle 업데이트**: 매 분 말 60개 leaf 한 번에 추가

```python
def flush_per_minute_shard(minute_snapshots: list[str]):
    # 1. Parquet 저장 (병렬, 시간당 6개 동시)
    shard_path = f"snapshots/{YYYY}/{MM}/{DD}/{HH}/shard_{MM}.parquet"
    df.to_parquet(shard_path, index=False, compression='snappy')
    
    # 2. 분당 Merkle leaf 60개 배치 추가
    response_hashes = [snap['response_hash'] for snap in minute_snapshots]
    append_merkle_batch(response_hashes)
    
    # 3. 시간 말 일일 root 계산
    if MM == 59:
        daily_root = finalize_daily_merkle()
        save_daily_root(f"{YYYY}-{MM}-{DD}", daily_root)
```

---

## 8. 실패 모드 및 처리

| 실패 모드 | 신호 | 복구 |
|---------|------|------|
| **Bigdata response_hash 불일치** | replay() → False | 데이터 드리프트 로그, 원본 snapshot 보존, 재실행 단계적 재개 |
| **Snapshot 누락** | FileNotFoundError | 사용자 알림, 부분 재생 불가, verdict 무효화 |
| **S3 outage (Phase 2)** | 타임아웃 | MVP: local fallback, Phase 2: DynamoDB 인덱스만 조회 |
| **DynamoDB throttling** | 429 응답 | exponential backoff, local SQLite cache hit 우선 |
| **Merkle 트리 정합성 오류** | hash chain 끊김 | 일일 root 재계산, audit log 출력 |

---

## 9. 감사 쿼리 API

```python
def audit(date: str) -> str:
    """날짜별 Merkle root 조회"""
    root = query_snapshots_root(date)
    return root  # "2026-04-28,a1b2c3d4..."

def verify_chain(start_date: str, end_date: str) -> bool:
    """연속된 일별 root chain 검증"""
    roots = []
    for date in date_range(start_date, end_date):
        roots.append(audit(date))
    
    # 각 root가 전일 root로부터 유도 가능한지 확인
    for i in range(1, len(roots)):
        if not validate_chain_link(roots[i-1], roots[i]):
            return False
    return True

def diff(verdict_id1: str, verdict_id2: str) -> dict:
    """두 verdict 간 snapshot 차이 분석"""
    snap1 = load_snapshots(verdict_id1)
    snap2 = load_snapshots(verdict_id2)
    
    return {
        "common_requests": count_identical_requests(snap1, snap2),
        "diverged_responses": [
            {
                "request_hash": h,
                "hash1": snap1[h],
                "hash2": snap2[h]
            } for h in diverged_keys(snap1, snap2)
        ]
    }
```

---

## 10. 구현 스켈레톤 (Python, ~40줄)

```python
import hashlib
import json
import sqlite3
import pandas as pd
from datetime import datetime
from uuid import uuid4
import os

class SnapshotBroker:
    def __init__(self, cache_dir: str = "cache"):
        self.cache_dir = cache_dir
        self.db = sqlite3.connect(f"{cache_dir}/index.db")
        self._init_db()
    
    def _init_db(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY,
                request_hash TEXT NOT NULL,
                response_hash TEXT NOT NULL,
                ts_utc_ms INTEGER NOT NULL,
                source_tool TEXT NOT NULL,
                ttl_ms INTEGER DEFAULT 86400000,
                sources TEXT NOT NULL
            )
        """)
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_req_hash ON snapshots(request_hash)"
        )
        self.db.commit()
    
    def save_snapshot(self, request: dict, response: dict) -> str:
        req_json = json.dumps(request, sort_keys=True)
        resp_json = json.dumps(response, sort_keys=True)
        
        request_hash = hashlib.sha256(req_json.encode()).hexdigest()
        response_hash = hashlib.sha256(resp_json.encode()).hexdigest()
        
        ts_ms = int(datetime.utcnow().timestamp() * 1000)
        uuid_str = str(uuid4())[:8]
        snapshot_id = f"snap_{ts_ms}{uuid_str}"
        
        # Parquet 저장
        shard_dir = f"{self.cache_dir}/snapshots/{ts_ms//86400000}/{(ts_ms%86400000)//3600000}"
        os.makedirs(shard_dir, exist_ok=True)
        
        df = pd.DataFrame([{
            "snapshot_id": snapshot_id,
            "request_hash": request_hash,
            "response_hash": response_hash,
            "ts_utc_ms": ts_ms,
            "source_tool": request.get("tool_name", "unknown"),
            "response_preview": resp_json[:1024]
        }])
        df.to_parquet(f"{shard_dir}/{snapshot_id}.parquet", index=False)
        
        # Index 저장
        self.db.execute("""
            INSERT INTO snapshots VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (snapshot_id, request_hash, response_hash, ts_ms,
              request.get("tool_name"), 86400000, "[]"))
        self.db.commit()
        
        return snapshot_id
    
    def replay(self, snapshot_ids: list[str]) -> bool:
        for snap_id in snapshot_ids:
            cursor = self.db.execute(
                "SELECT response_hash FROM snapshots WHERE snapshot_id = ?",
                (snap_id,)
            )
            row = cursor.fetchone()
            if not row:
                return False
        return True
```

---

**요약**: INV-GS-022 결정론적 재생을 위해 Snapshot Broker는 모든 Bigdata MCP 호출을 Parquet 샤드로 기록하고, 일일 Merkle 트리로 검증 가능하게 유지한다. MVP는 local SQLite, Phase 2에서 S3+DynamoDB로 확장 가능.
