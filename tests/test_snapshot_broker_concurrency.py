from __future__ import annotations

import multiprocessing as mp
from datetime import UTC, datetime
from pathlib import Path

import pytest

from glostat.data.snapshot_broker import SnapshotBroker, SnapshotKey

# v1.6.3 — SnapshotBroker concurrency / parallel-safety regression tests.
#
# Discovered via cross-stock acid test (2026-05-02): 5 parallel `glostat
# predict` runs from the same cwd hit "sqlite3.OperationalError: database
# is locked" because WAL was set but busy_timeout wasn't. After the fix
# (timeout=30 + PRAGMA busy_timeout = 30000), parallel writes serialize
# correctly without raising.


def _writer_proc(root: str, idx: int) -> int:
    """Independent process: open broker, write one snapshot, exit."""
    from glostat.data.snapshot_broker import SnapshotBroker as B  # noqa: PLC0415
    from glostat.data.snapshot_broker import SnapshotKey as K  # noqa: PLC0415
    broker = B(root=Path(root))
    try:
        key = K(
            uaid=f"TEST.PROC{idx}",
            edge_type="test_concurrency",
            ts_utc=datetime(2026, 5, 2, 15, idx, tzinfo=UTC),
            tool="test_concurrency_proc",
            params_canon=f'{{"idx":{idx}}}',
        )
        broker.save_snapshot(key, {"idx": idx, "payload": "x" * 100})
        return 0
    except Exception:
        return 1
    finally:
        broker.close()


class TestSnapshotBrokerParallelWriters:
    def test_three_processes_write_concurrently(self, tmp_path: Path) -> None:
        # WHY: pre-fix this would raise "database is locked" within ~100ms.
        # Post-fix the busy_timeout=30s lets writers serialize cleanly.
        root = str(tmp_path / "broker_root")
        with mp.get_context("spawn").Pool(processes=3) as pool:
            results = pool.starmap(_writer_proc, [(root, i) for i in range(3)])
        assert all(r == 0 for r in results), f"Some writers failed: {results}"

    def test_five_processes_write_concurrently(self, tmp_path: Path) -> None:
        # Higher contention — was the empirical failure case in cross-stock test.
        root = str(tmp_path / "broker_root_5")
        with mp.get_context("spawn").Pool(processes=5) as pool:
            results = pool.starmap(_writer_proc, [(root, i) for i in range(5)])
        assert all(r == 0 for r in results), f"Some writers failed: {results}"


class TestBrokerPragmas:
    def test_busy_timeout_set(self, tmp_path: Path) -> None:
        broker = SnapshotBroker(root=tmp_path / "broker_pragma")
        try:
            cursor = broker._db.execute("PRAGMA busy_timeout")
            row = cursor.fetchone()
            # Either tuple form or sqlite3.Row.
            value = row[0] if hasattr(row, "__getitem__") else row
            # Should be 30000 (30s in ms) per the fix.
            assert value == 30000, f"busy_timeout pragma not set: got {value}"
        finally:
            broker.close()

    def test_journal_mode_wal(self, tmp_path: Path) -> None:
        broker = SnapshotBroker(root=tmp_path / "broker_wal")
        try:
            cursor = broker._db.execute("PRAGMA journal_mode")
            row = cursor.fetchone()
            value = row[0] if hasattr(row, "__getitem__") else row
            assert str(value).lower() == "wal", f"WAL not active: got {value}"
        finally:
            broker.close()


@pytest.mark.parametrize("ticker_idx", range(3))
def test_repeated_save_idempotent(tmp_path: Path, ticker_idx: int) -> None:
    """Same payload saved twice = single shard (idempotency preserved post-fix)."""
    broker = SnapshotBroker(root=tmp_path / "broker_idem")
    try:
        key = SnapshotKey(
            uaid=f"TEST.IDEM{ticker_idx}",
            edge_type="test_idem",
            ts_utc=datetime(2026, 5, 2, tzinfo=UTC),
            tool="test_idem_tool",
            params_canon=f'{{"i":{ticker_idx}}}',
        )
        rec1 = broker.save_snapshot(key, {"x": 1})
        rec2 = broker.save_snapshot(key, {"x": 1})
        assert rec1.leaf.leaf_hash == rec2.leaf.leaf_hash
    finally:
        broker.close()
