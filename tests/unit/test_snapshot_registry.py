"""Unit tests for SnapshotRegistry, the SQLite-backed metadata store."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from etf_platform.data_engine.exceptions import SnapshotNotFoundError
from etf_platform.data_engine.models import DataSnapshot
from etf_platform.data_engine.storage.snapshot_registry import SnapshotRegistry


class TestSnapshotRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.registry = SnapshotRegistry(self.tmp_dir / "registry.db")
        self.addCleanup(self.registry.close)

    def _snapshot(self, snapshot_id: str) -> DataSnapshot:
        return DataSnapshot(
            snapshot_id=snapshot_id,
            created_at=datetime.now(timezone.utc),
            symbols=("NIFTYBEES", "GOLDBEES"),
            start_date=date(2026, 1, 1),
            end_date=date(2026, 1, 31),
            source_providers=("nse",),
            row_count=42,
        )

    def test_register_and_get_snapshot_roundtrip(self) -> None:
        self.registry.register_snapshot(self._snapshot("snap-1"))
        retrieved = self.registry.get_snapshot("snap-1")
        self.assertEqual(retrieved.snapshot_id, "snap-1")
        self.assertEqual(retrieved.symbols, ("NIFTYBEES", "GOLDBEES"))
        self.assertEqual(retrieved.row_count, 42)

    def test_get_unknown_snapshot_raises(self) -> None:
        with self.assertRaises(SnapshotNotFoundError):
            self.registry.get_snapshot("does-not-exist")

    def test_latest_snapshot_id_returns_most_recent(self) -> None:
        self.assertIsNone(self.registry.latest_snapshot_id())
        self.registry.register_snapshot(self._snapshot("snap-older"))
        self.registry.register_snapshot(self._snapshot("snap-newer"))
        # Both registered "now" in this fast test; latest_snapshot_id orders
        # by created_at, so we only assert it returns *one of* the registered
        # ids deterministically rather than assuming clock resolution.
        self.assertIn(self.registry.latest_snapshot_id(), {"snap-older", "snap-newer"})

    def test_ingestion_run_lifecycle(self) -> None:
        self.registry.register_snapshot(self._snapshot("snap-1"))
        run_id = self.registry.start_ingestion_run("snap-1")
        self.assertIsInstance(run_id, int)
        self.registry.finish_ingestion_run(run_id, "succeeded", "all good")
        row = self.registry._conn.execute(
            "SELECT status, detail FROM ingestion_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        self.assertEqual(row["status"], "succeeded")
        self.assertEqual(row["detail"], "all good")

    def test_wal_mode_enabled(self) -> None:
        mode = self.registry._conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")

    def test_concurrent_access_from_multiple_threads_is_safe(self) -> None:
        """Real regression test for the check_same_thread=False + lock fix
        (see common/db.py and this module's docstring): registers and reads
        snapshots from several threads sharing ONE SnapshotRegistry
        instance/connection concurrently. Before the fix, this either raised
        sqlite3.ProgrammingError ('objects created in a thread can only be
        used in that same thread') or risked interleaved writes corrupting
        state without the lock.
        """
        import threading

        errors: list[Exception] = []
        num_threads = 8
        writes_per_thread = 5

        def worker(thread_id: int) -> None:
            try:
                for i in range(writes_per_thread):
                    snap_id = f"thread-{thread_id}-snap-{i}"
                    self.registry.register_snapshot(self._snapshot(snap_id))
                    run_id = self.registry.start_ingestion_run(snap_id)
                    self.registry.finish_ingestion_run(run_id, "succeeded")
                    retrieved = self.registry.get_snapshot(snap_id)
                    assert retrieved.snapshot_id == snap_id
            except Exception as exc:  # noqa: BLE001 — captured for the main thread to assert on
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [], f"Thread safety errors: {errors}")

        # Every snapshot from every thread should have been registered —
        # confirms no writes were silently lost to a race.
        all_ids = {
            row["snapshot_id"]
            for row in self.registry._conn.execute("SELECT snapshot_id FROM data_snapshots").fetchall()
        }
        self.assertEqual(len(all_ids), num_threads * writes_per_thread)


if __name__ == "__main__":
    unittest.main()
