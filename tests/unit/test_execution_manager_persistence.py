"""Tests for execution_manager.persistence -- unit, failure-path, and
restart-recovery tests, per implementation rule 4."""

from __future__ import annotations

import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from etf_platform.execution_manager import (
    ConcurrentInvocationError,
    DatabaseCorruptionError,
    ExecutionRecord,
    ExecutionStateStore,
    OrderLifecycleState,
    new_execution_id,
    utc_now,
)


def make_record(execution_id=None, cycle_id="c1", symbol="A", status=OrderLifecycleState.PROPOSAL, priority_rank=1):
    return ExecutionRecord(
        execution_id=execution_id or new_execution_id(), queue_id=None, cycle_id=cycle_id, symbol=symbol,
        quantity_proposed=10, quantity_final=None, limit_price=100.0, order_status=status,
        broker_order_id=None, executed_price=None, executed_quantity=0, is_paper_trade=True,
        created_at=utc_now(), last_status_check=None, priority_rank=priority_rank,
    )


class ExecutionManagerPersistenceTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)


class TestExecutionRecordCrud(ExecutionManagerPersistenceTestCase):
    def setUp(self):
        super().setUp()
        self.store = ExecutionStateStore(self.tmp_dir / "test.db")
        self.addCleanup(self.store.close)

    def test_save_and_load_roundtrip(self):
        record = make_record(symbol="NIFTYBEES")
        self.store.save_execution_record(record)
        loaded = self.store.load_execution_record(record.execution_id)
        self.assertEqual(loaded.symbol, "NIFTYBEES")
        self.assertEqual(loaded.order_status, OrderLifecycleState.PROPOSAL)
        self.assertEqual(loaded.created_at.tzinfo is not None, True)

    def test_load_nonexistent_returns_none(self):
        self.assertIsNone(self.store.load_execution_record("does-not-exist"))

    def test_update_via_upsert(self):
        record = make_record()
        self.store.save_execution_record(record)
        record.transition_to(OrderLifecycleState.VERIFIED)
        record.quantity_final = 8
        self.store.save_execution_record(record)
        loaded = self.store.load_execution_record(record.execution_id)
        self.assertEqual(loaded.order_status, OrderLifecycleState.VERIFIED)
        self.assertEqual(loaded.quantity_final, 8)

    def test_load_records_for_cycle_ordered_by_priority(self):
        r1 = make_record(cycle_id="cycle-x", symbol="LOW_PRIORITY", priority_rank=3)
        r2 = make_record(cycle_id="cycle-x", symbol="HIGH_PRIORITY", priority_rank=1)
        r3 = make_record(cycle_id="cycle-x", symbol="MID_PRIORITY", priority_rank=2)
        for r in (r1, r2, r3):
            self.store.save_execution_record(r)
        loaded = self.store.load_records_for_cycle("cycle-x")
        self.assertEqual([r.symbol for r in loaded], ["HIGH_PRIORITY", "MID_PRIORITY", "LOW_PRIORITY"])

    def test_load_unresolved_excludes_reconciled(self):
        pending = make_record(status=OrderLifecycleState.PENDING)
        reconciled = make_record(status=OrderLifecycleState.RECONCILED)
        self.store.save_execution_record(pending)
        self.store.save_execution_record(reconciled)
        unresolved = self.store.load_unresolved_records()
        ids = {r.execution_id for r in unresolved}
        self.assertIn(pending.execution_id, ids)
        self.assertNotIn(reconciled.execution_id, ids)

    def test_notes_roundtrip(self):
        record = make_record()
        record.notes = ("first note", "second note")
        self.store.save_execution_record(record)
        loaded = self.store.load_execution_record(record.execution_id)
        self.assertEqual(loaded.notes, ("first note", "second note"))


class TestConcurrentInvocationLocking(ExecutionManagerPersistenceTestCase):
    def setUp(self):
        super().setUp()
        self.store = ExecutionStateStore(self.tmp_dir / "test.db")
        self.addCleanup(self.store.close)

    def test_second_claim_on_same_cycle_raises(self):
        self.store.claim_cycle("cycle-1", claimed_by="invocation-A")
        with self.assertRaises(ConcurrentInvocationError):
            self.store.claim_cycle("cycle-1", claimed_by="invocation-B")

    def test_claims_on_different_cycles_do_not_conflict(self):
        self.store.claim_cycle("cycle-1", claimed_by="invocation-A")
        self.store.claim_cycle("cycle-2", claimed_by="invocation-B")

    def test_release_allows_reclaim(self):
        self.store.claim_cycle("cycle-1", claimed_by="invocation-A")
        self.store.release_cycle("cycle-1")
        self.store.claim_cycle("cycle-1", claimed_by="invocation-B")

    def test_stale_claim_can_be_reclaimed(self):
        self.store.claim_cycle("cycle-1", claimed_by="crashed-invocation")
        self.store.claim_cycle("cycle-1", claimed_by="new-invocation", max_claim_age_seconds=0)
        self.assertTrue(self.store.is_claimed("cycle-1"))

    def test_is_claimed_false_for_unclaimed_cycle(self):
        self.assertFalse(self.store.is_claimed("never-claimed"))

    def test_real_concurrent_threads_only_one_succeeds(self):
        results = {"success": 0, "blocked": 0}
        lock = threading.Lock()

        def attempt(name):
            try:
                self.store.claim_cycle("race-cycle", claimed_by=name)
                with lock:
                    results["success"] += 1
            except ConcurrentInvocationError:
                with lock:
                    results["blocked"] += 1

        threads = [threading.Thread(target=attempt, args=(f"invocation-{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(results["success"], 1, "Exactly one invocation must win the claim.")
        self.assertEqual(results["blocked"], 9, "All others must be blocked.")


class TestDatabaseCorruption(ExecutionManagerPersistenceTestCase):
    def test_severely_corrupted_file_raises_domain_exception(self):
        corrupt_path = self.tmp_dir / "corrupt.db"
        corrupt_path.write_bytes(b"not a sqlite file at all, deliberately corrupted")
        with self.assertRaises(DatabaseCorruptionError):
            ExecutionStateStore(corrupt_path)

    def test_valid_empty_file_treated_as_fresh_database(self):
        empty_path = self.tmp_dir / "empty.db"
        empty_path.touch()
        store = ExecutionStateStore(empty_path)
        store.close()

    def test_valid_database_passes_integrity_check(self):
        store = ExecutionStateStore(self.tmp_dir / "valid.db")
        store.save_execution_record(make_record())
        store.close()
        store2 = ExecutionStateStore(self.tmp_dir / "valid.db")
        store2.close()


class TestRestartRecovery(ExecutionManagerPersistenceTestCase):
    def test_execution_records_survive_restart(self):
        db_path = self.tmp_dir / "restart.db"
        store1 = ExecutionStateStore(db_path)
        record = make_record(symbol="RESTART_TEST")
        store1.save_execution_record(record)
        store1.close()

        store2 = ExecutionStateStore(db_path)
        loaded = store2.load_execution_record(record.execution_id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.symbol, "RESTART_TEST")
        store2.close()

    def test_cycle_claims_survive_restart(self):
        db_path = self.tmp_dir / "restart_claim.db"
        store1 = ExecutionStateStore(db_path)
        store1.claim_cycle("crashed-cycle", claimed_by="pre-crash-invocation")
        store1.close()

        store2 = ExecutionStateStore(db_path)
        self.assertTrue(store2.is_claimed("crashed-cycle"))
        with self.assertRaises(ConcurrentInvocationError):
            store2.claim_cycle("crashed-cycle", claimed_by="post-crash-invocation")
        store2.close()

    def test_unresolved_records_are_findable_after_restart(self):
        db_path = self.tmp_dir / "restart_unresolved.db"
        store1 = ExecutionStateStore(db_path)
        pending = make_record(status=OrderLifecycleState.PENDING)
        store1.save_execution_record(pending)
        store1.close()

        store2 = ExecutionStateStore(db_path)
        unresolved = store2.load_unresolved_records()
        self.assertEqual(len(unresolved), 1)
        self.assertEqual(unresolved[0].execution_id, pending.execution_id)
        store2.close()


if __name__ == "__main__":
    unittest.main()
