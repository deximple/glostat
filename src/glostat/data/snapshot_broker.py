from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pyarrow as pa
import pyarrow.parquet as pq
import structlog

# E5 Snapshot Broker (INV-GS-022): all Bigdata MCP responses persisted; replay reads
# snapshot first. Local-only Sprint 0 backend (SQLite index + parquet shards). The S3
# layer is optional and lands in Phase 2 — production swap is a path-prefix change.

log: Final = structlog.get_logger(__name__)

_DDL: Final = """
CREATE TABLE IF NOT EXISTS snapshots (
    leaf_hash      TEXT PRIMARY KEY,
    uaid           TEXT NOT NULL,
    edge_type      TEXT NOT NULL,
    ts_utc         TEXT NOT NULL,
    tool           TEXT NOT NULL,
    params_canon   TEXT NOT NULL,
    parquet_path   TEXT NOT NULL,
    payload_bytes  INTEGER NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snap_uaid_ts  ON snapshots(uaid, ts_utc);
CREATE INDEX IF NOT EXISTS idx_snap_edge     ON snapshots(edge_type);
CREATE INDEX IF NOT EXISTS idx_snap_tool     ON snapshots(tool);

CREATE TABLE IF NOT EXISTS verdicts (
    verdict_hash   TEXT PRIMARY KEY,
    ticker         TEXT NOT NULL,
    issued_at      TEXT NOT NULL,
    parent_hash    TEXT,
    leaves         TEXT NOT NULL,
    git_commit     TEXT NOT NULL,
    parquet_path   TEXT NOT NULL,
    created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_verdict_ticker ON verdicts(ticker, issued_at);
"""


@dataclass(frozen=True, slots=True)
class SnapshotKey:
    uaid: str
    edge_type: str       # logical signal type, e.g. "tearsheet", "search", "events"
    ts_utc: datetime     # canonical event time (not wall clock)
    tool: str
    params_canon: str    # canonical JSON of MCP params

    def to_leaf_input(self) -> bytes:
        payload = {
            "uaid": self.uaid,
            "edge_type": self.edge_type,
            "ts_utc": self.ts_utc.isoformat(),
            "tool": self.tool,
            "params_canon": self.params_canon,
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True, slots=True)
class MerkleLeaf:
    leaf_hash: str
    key: SnapshotKey
    payload_sha: str

    @classmethod
    def compute(cls, key: SnapshotKey, payload_bytes: bytes) -> MerkleLeaf:
        payload_sha = hashlib.sha256(payload_bytes).hexdigest()
        h = hashlib.sha256()
        h.update(key.to_leaf_input())
        h.update(b"\x1e")
        h.update(payload_sha.encode())
        return cls(leaf_hash=h.hexdigest(), key=key, payload_sha=payload_sha)


@dataclass(frozen=True, slots=True)
class SnapshotRecord:
    leaf: MerkleLeaf
    parquet_path: Path
    payload_bytes: int
    created_at: datetime


@dataclass(frozen=True, slots=True)
class VerdictRecord:
    verdict_hash: str
    ticker: str
    issued_at: datetime
    parent_hash: str | None
    leaves: tuple[str, ...]
    git_commit: str
    parquet_path: Path
    created_at: datetime


@dataclass(slots=True)
class SnapshotBroker:
    root: Path
    _db: sqlite3.Connection = field(init=False)

    def __post_init__(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "shards").mkdir(exist_ok=True)
        (self.root / "verdicts").mkdir(exist_ok=True)
        self._db = sqlite3.connect(self.root / "index.sqlite", isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode = WAL")
        self._db.execute("PRAGMA synchronous = NORMAL")
        self._db.executescript(_DDL)

    # ── snapshot lifecycle ─────────────────────────────────────────────────

    def save_snapshot(self, key: SnapshotKey, payload: dict[str, Any]) -> SnapshotRecord:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               default=str).encode("utf-8")
        leaf = MerkleLeaf.compute(key, canonical)
        existing = self._fetch_snapshot(leaf.leaf_hash)
        if existing is not None:
            log.debug("snapshot.idempotent_hit", leaf=leaf.leaf_hash, uaid=key.uaid)
            return existing
        shard = self._shard_path(leaf.leaf_hash)
        shard.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pydict(
            {
                "leaf_hash":     [leaf.leaf_hash],
                "payload_canon": [canonical],
                "payload_sha":   [leaf.payload_sha],
                "uaid":          [key.uaid],
                "edge_type":     [key.edge_type],
                "ts_utc":        [key.ts_utc],
                "tool":          [key.tool],
                "params_canon":  [key.params_canon],
            }
        )
        pq.write_table(table, shard, compression="zstd")
        created = _utcnow()
        self._db.execute(
            "INSERT INTO snapshots "
            "(leaf_hash, uaid, edge_type, ts_utc, tool, params_canon, "
            " parquet_path, payload_bytes, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                leaf.leaf_hash,
                key.uaid,
                key.edge_type,
                key.ts_utc.isoformat(),
                key.tool,
                key.params_canon,
                str(shard.relative_to(self.root)),
                len(canonical),
                created.isoformat(),
            ),
        )
        log.info(
            "snapshot.saved",
            leaf=leaf.leaf_hash[:12],
            uaid=key.uaid,
            tool=key.tool,
            bytes=len(canonical),
        )
        return SnapshotRecord(leaf=leaf, parquet_path=shard,
                              payload_bytes=len(canonical), created_at=created)

    def read_snapshot(self, leaf_hash: str) -> dict[str, Any]:
        record = self._fetch_snapshot(leaf_hash)
        if record is None:
            raise KeyError(f"snapshot not found: {leaf_hash}")
        table = pq.read_table(self.root / record.parquet_path.relative_to(self.root))
        rows = table.to_pylist()
        if not rows:
            raise RuntimeError(f"snapshot shard empty: {leaf_hash}")
        canon = bytes(rows[0]["payload_canon"])
        actual = hashlib.sha256(canon).hexdigest()
        if actual != record.leaf.payload_sha:
            raise IntegrityError(
                f"snapshot integrity broken: leaf={leaf_hash} "
                f"expected={record.leaf.payload_sha} got={actual}"
            )
        return json.loads(canon.decode("utf-8"))

    def list_snapshots(
        self, *, uaid: str | None = None, edge_type: str | None = None
    ) -> Iterable[SnapshotRecord]:
        clauses, params = [], []
        if uaid:
            clauses.append("uaid = ?")
            params.append(uaid)
        if edge_type:
            clauses.append("edge_type = ?")
            params.append(edge_type)
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        rows = self._db.execute(
            f"SELECT * FROM snapshots {where} ORDER BY ts_utc",
            params,
        ).fetchall()
        for row in rows:
            yield self._row_to_snapshot(row)

    # ── verdict replay ─────────────────────────────────────────────────────

    def record_verdict(
        self,
        *,
        verdict_hash: str,
        ticker: str,
        issued_at: datetime,
        leaves: Sequence[str],
        git_commit: str,
        payload: dict[str, Any],
        parent_hash: str | None = None,
    ) -> VerdictRecord:
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                               default=str).encode("utf-8")
        shard = self.root / "verdicts" / f"{verdict_hash[:2]}" / f"{verdict_hash}.parquet"
        shard.parent.mkdir(parents=True, exist_ok=True)
        table = pa.Table.from_pydict({
            "verdict_hash":   [verdict_hash],
            "payload_canon":  [canonical],
            "leaves":         [list(leaves)],
            "ticker":         [ticker],
            "issued_at":      [issued_at],
            "parent_hash":    [parent_hash],
            "git_commit":     [git_commit],
        })
        pq.write_table(table, shard, compression="zstd")
        created = _utcnow()
        self._db.execute(
            "INSERT OR REPLACE INTO verdicts "
            "(verdict_hash, ticker, issued_at, parent_hash, leaves, "
            " git_commit, parquet_path, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                verdict_hash, ticker, issued_at.isoformat(), parent_hash,
                json.dumps(list(leaves)), git_commit,
                str(shard.relative_to(self.root)), created.isoformat(),
            ),
        )
        return VerdictRecord(
            verdict_hash=verdict_hash, ticker=ticker, issued_at=issued_at,
            parent_hash=parent_hash, leaves=tuple(leaves),
            git_commit=git_commit, parquet_path=shard, created_at=created,
        )

    def replay_verdict(self, verdict_hash: str) -> dict[str, Any]:
        row = self._db.execute(
            "SELECT * FROM verdicts WHERE verdict_hash = ?", (verdict_hash,)
        ).fetchone()
        if row is None:
            raise KeyError(f"verdict not found: {verdict_hash}")
        table = pq.read_table(self.root / row["parquet_path"])
        canon = bytes(table.to_pylist()[0]["payload_canon"])
        return json.loads(canon.decode("utf-8"))

    # ── audit (Merkle root over leaves; cheap, deterministic) ──────────────

    def audit_root(self, leaves: Sequence[str] | None = None) -> str:
        if leaves is None:
            rows = self._db.execute(
                "SELECT leaf_hash FROM snapshots ORDER BY leaf_hash"
            ).fetchall()
            leaves = [row["leaf_hash"] for row in rows]
        else:
            leaves = sorted(leaves)
        if not leaves:
            return hashlib.sha256(b"").hexdigest()
        layer = [bytes.fromhex(h) for h in leaves]
        while len(layer) > 1:
            nxt = []
            for i in range(0, len(layer), 2):
                left = layer[i]
                right = layer[i + 1] if i + 1 < len(layer) else left
                nxt.append(hashlib.sha256(left + right).digest())
            layer = nxt
        return layer[0].hex()

    # ── lifecycle ─────────────────────────────────────────────────────────

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def transaction(self):
        try:
            self._db.execute("BEGIN")
            yield self
            self._db.execute("COMMIT")
        except Exception:
            self._db.execute("ROLLBACK")
            raise

    # ── internals ─────────────────────────────────────────────────────────

    def _shard_path(self, leaf_hash: str) -> Path:
        return self.root / "shards" / leaf_hash[:2] / f"{leaf_hash}.parquet"

    def _fetch_snapshot(self, leaf_hash: str) -> SnapshotRecord | None:
        row = self._db.execute(
            "SELECT * FROM snapshots WHERE leaf_hash = ?", (leaf_hash,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_snapshot(row)

    def _row_to_snapshot(self, row: sqlite3.Row) -> SnapshotRecord:
        key = SnapshotKey(
            uaid=row["uaid"],
            edge_type=row["edge_type"],
            ts_utc=datetime.fromisoformat(row["ts_utc"]),
            tool=row["tool"],
            params_canon=row["params_canon"],
        )
        leaf = MerkleLeaf(leaf_hash=row["leaf_hash"], key=key, payload_sha="")
        shard = self.root / row["parquet_path"]
        return SnapshotRecord(
            leaf=_with_payload_sha(leaf, shard),
            parquet_path=shard,
            payload_bytes=int(row["payload_bytes"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )


class IntegrityError(RuntimeError):
    """Raised when a snapshot shard's payload sha disagrees with the index."""


def _with_payload_sha(leaf: MerkleLeaf, shard: Path) -> MerkleLeaf:
    table = pq.read_table(shard, columns=["payload_sha"])
    rows = table.to_pylist()
    sha = str(rows[0]["payload_sha"]) if rows else ""
    return MerkleLeaf(leaf_hash=leaf.leaf_hash, key=leaf.key, payload_sha=sha)


def _utcnow() -> datetime:
    return datetime.now(tz=UTC)
