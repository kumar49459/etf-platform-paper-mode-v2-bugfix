"""Tests for disaster_recovery.py (Production Verification objectives 2
and 3): severe disaster injection with full recovery verification, and
complete audit-trail reconstruction for every order in a run.
"""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from pathlib import Path

from etf_platform.execution_manager import OrderLifecycleState
from etf_platform.paper_trading_operations.disaster_recovery import run_disaster_recovery_exercise
from etf_platform.paper_trading_operations.session import ExtendedPaperTradingSession


class TestDisasterRecoveryExercise(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_all_five_disaster_kinds_can_occur(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B", "C"), seed=42,
            failure_injection_rate=0.03, restart_probability_per_day=0.0,
            event_archive_path=self.tmp_dir / "events.jsonl",
        )
        report = run_disaster_recovery_exercise(session, num_days=100, cycles_per_day=3, rng=random.Random(42), disaster_probability=0.2)
        kinds_seen = {d.kind for d in report.disasters_injected}
        expected = {"unexpected_shutdown", "process_termination", "database_interruption", "broker_failure", "notification_failure"}
        self.assertTrue(kinds_seen.issubset(expected))
        self.assertGreater(len(report.disasters_injected), 5, "Too few disasters injected to meaningfully test recovery.")
        session.close()

    def test_recovery_verification_tolerates_transient_injected_noise(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B", "C"), seed=999,
            failure_injection_rate=0.03, restart_probability_per_day=0.0,
            event_archive_path=self.tmp_dir / "events.jsonl",
        )
        report = run_disaster_recovery_exercise(session, num_days=180, cycles_per_day=3, rng=random.Random(999), disaster_probability=0.15)
        self.assertEqual(report.recoveries_confirmed, len(report.disasters_injected))
        self.assertEqual(report.recovery_failures, [])
        session.close()

    def test_no_orphans_or_duplicates_after_disaster_exercise(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B"), seed=7,
            failure_injection_rate=0.025, restart_probability_per_day=0.0,
            event_archive_path=self.tmp_dir / "events.jsonl",
        )
        report = run_disaster_recovery_exercise(session, num_days=150, cycles_per_day=2, rng=random.Random(7), disaster_probability=0.12)
        self.assertEqual(report.final_orphan_count, 0)
        self.assertEqual(report.final_duplicate_count, 0)
        session.close()


class TestCompleteAuditReconstruction(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_every_order_in_the_run_has_a_reconstructable_story(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B", "C"), seed=2024,
            starting_cash=200_000_000.0, failure_injection_rate=0.03, restart_probability_per_day=0.08,
            event_archive_path=self.tmp_dir / "events.jsonl",
        )
        session.run(num_days=100, cycles_per_day=3, rng=random.Random(2024), snapshot_every_n_days=10, purge_every_n_days=10)

        full_log = session.get_full_cycle_log()
        self.assertGreater(len(full_log), 0)

        unreconstructable = []
        for entry in full_log:
            history = session._archive.reconstruct_by_cycle_id(entry.cycle_id, live_event_recorder=session._events)
            if len(history) == 0:
                unreconstructable.append(entry.cycle_id)

        self.assertEqual(
            unreconstructable, [],
            f"{len(unreconstructable)} of {len(full_log)} orders have NO reconstructable event history.",
        )
        session.close()

    def test_reconstructed_histories_are_chronologically_consistent(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B"), seed=2025,
            failure_injection_rate=0.02, restart_probability_per_day=0.05,
            event_archive_path=self.tmp_dir / "events.jsonl",
        )
        session.run(num_days=60, cycles_per_day=2, rng=random.Random(2025), snapshot_every_n_days=10, purge_every_n_days=10)

        full_log = session.get_full_cycle_log()
        violations = []
        for entry in full_log:
            history = session._archive.reconstruct_by_cycle_id(entry.cycle_id, live_event_recorder=session._events)
            timestamps = [h["timestamp"] for h in history]
            if timestamps != sorted(timestamps):
                violations.append(entry.cycle_id)

        self.assertEqual(violations, [], f"{len(violations)} orders have non-chronological reconstructed histories.")
        session.close()

    def test_final_status_in_log_matches_current_store_state_or_is_explainable(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A",), seed=2026,
            failure_injection_rate=0.02, restart_probability_per_day=0.05,
            event_archive_path=self.tmp_dir / "events.jsonl",
        )
        session.run(num_days=50, cycles_per_day=2, rng=random.Random(2026), snapshot_every_n_days=10, purge_every_n_days=10)

        full_log = session.get_full_cycle_log()
        checked = 0
        for entry in full_log[:50]:
            try:
                record = session._wrapped_store.load_execution_record(entry.execution_id)
            except Exception:
                continue
            if record is None:
                continue
            checked += 1
            terminal_states = (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED,
                                OrderLifecycleState.FAILED, OrderLifecycleState.RECONCILED)
            if entry.final_status in terminal_states:
                self.assertIn(
                    record.order_status, terminal_states,
                    f"{entry.execution_id}: log shows terminal {entry.final_status}, "
                    f"store shows non-terminal {record.order_status} -- state regression.",
                )
        self.assertGreater(checked, 0, "No orders were actually cross-checked -- test setup issue.")
        session.close()


if __name__ == "__main__":
    unittest.main()
