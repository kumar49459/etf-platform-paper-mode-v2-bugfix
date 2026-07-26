"""Property tests (requirement 5) as permanent, CI-scale regression tests.
The full 50,000-cycle mandatory stress test (requirement 8) is run
separately and reported in the milestone summary / CHANGELOG -- kept here
at a smaller scale (2,000 cycles) so it still runs quickly as part of the
normal suite while exercising the exact same harness and invariants.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from etf_platform.execution_manager.stress_harness import run_stress_test


class TestPropertyInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_dir = Path(tempfile.mkdtemp())
        cls.report = run_stress_test(
            num_cycles=2000, seed=2026, db_path=cls.tmp_dir / "property.db", restart_probability=0.04,
        )

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp_dir, ignore_errors=True)

    def test_no_duplicate_submission(self):
        self.assertEqual(self.report.duplicate_submissions_detected, 0)

    def test_no_impossible_lifecycle_transitions(self):
        self.assertEqual(self.report.invalid_transitions_detected, 0)

    def test_no_negative_cash(self):
        self.assertEqual(self.report.negative_cash_events, 0)

    def test_reconciliation_ran_on_every_restart(self):
        self.assertGreaterEqual(self.report.reconciliation_runs, self.report.restarts_performed)

    def test_no_orphan_orders_after_final_reconciliation(self):
        self.assertEqual(self.report.orphan_orders_at_end, 0)

    def test_no_invariant_violations_overall(self):
        self.assertEqual(self.report.invariant_violations, [])

    def test_restarts_actually_occurred_meaning_recovery_was_genuinely_exercised(self):
        self.assertGreater(self.report.restarts_performed, 10)

    def test_restart_success_rate_is_100_percent(self):
        self.assertEqual(self.report.restart_success_rate, 1.0)


if __name__ == "__main__":
    unittest.main()
