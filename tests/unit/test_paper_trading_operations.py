"""Tests for paper_trading_operations: session continuity, report
accuracy (including the stale-status fix), and resource trend analysis.
"""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from pathlib import Path

from etf_platform.paper_trading_operations.reports import generate_report
from etf_platform.paper_trading_operations.resource_trends import analyze_all_trends, analyze_trend
from etf_platform.paper_trading_operations.session import ExtendedPaperTradingSession, ResourceSnapshot


class TestExtendedPaperTradingSession(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_runs_without_failure_injection(self):
        session = ExtendedPaperTradingSession(db_path=self.tmp_dir / "s.db", symbols=("A", "B"), seed=1)
        session.run(num_days=10, cycles_per_day=2, rng=random.Random(1))
        self.assertEqual(len(session.state.cycle_log), 20)
        session.close()

    def test_runs_with_failure_injection_and_restarts(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B", "C"), seed=2,
            failure_injection_rate=0.03, restart_probability_per_day=0.15,
        )
        session.run(num_days=20, cycles_per_day=3, rng=random.Random(2), snapshot_every_n_days=4)
        # A small number of cycles can legitimately be skipped if the
        # initial save itself is hit by injected failure -- not exactly
        # num_days*cycles_per_day, but close, and never fewer than 90%.
        expected = 20 * 3
        self.assertGreaterEqual(len(session.state.cycle_log), int(expected * 0.9))
        self.assertLessEqual(len(session.state.cycle_log), expected)
        self.assertGreater(session.state.reconciliation_runs, 0)
        session.close()

    def test_resource_snapshots_collected_at_expected_cadence(self):
        session = ExtendedPaperTradingSession(db_path=self.tmp_dir / "s.db", symbols=("A",), seed=3)
        session.run(num_days=10, cycles_per_day=1, rng=random.Random(3), snapshot_every_n_days=2)
        self.assertEqual(len(session.state.resource_snapshots), 5)
        session.close()

    def test_no_duplicate_client_references_at_broker(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B"), seed=4,
            failure_injection_rate=0.04, restart_probability_per_day=0.2,
        )
        session.run(num_days=15, cycles_per_day=2, rng=random.Random(4))
        reference_counts = {}
        for order in session._broker._orders.values():
            reference_counts[order.client_reference] = reference_counts.get(order.client_reference, 0) + 1
        duplicates = {ref: count for ref, count in reference_counts.items() if count > 1}
        self.assertEqual(duplicates, {})
        session.close()

    def test_no_permanently_orphaned_pending_orders_over_a_long_run(self):
        """Regression for the real bug found via this milestone's own
        100-day diagnostic run: records reconciliation adopted a
        broker_order_id for were never revisited afterward, permanently
        stuck at poll_count=0 regardless of how many more days passed.
        sweep_outstanding() (wired into run()) is the fix.

        The right invariant to check is NOT "zero orders are ever
        momentarily unpolled at the exact instant of inspection" -- a few
        recently-adopted orders with poll_count=0 at any single snapshot
        is normal for any continuously-operating system (found while
        making this test robust: the exact count at one instant is
        sensitive to PYTHONHASHSEED-influenced set iteration order in
        sweep_outstanding's truncation, harmless stochastic variance, not
        a defect). The invariant that actually matters is that this count
        does NOT GROW across an extended period -- checked here by
        comparing snapshots 100 days apart."""
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B", "C"), seed=99,
            starting_cash=1_000_000_000.0, failure_injection_rate=0.015, restart_probability_per_day=0.04,
        )
        rng = random.Random(99)
        session.run(num_days=100, cycles_per_day=3, rng=rng, snapshot_every_n_days=10, purge_every_n_days=14)
        stale_at_100 = len([o for o in session._broker.get_open_orders() if o.poll_count == 0])

        session.run(num_days=100, cycles_per_day=3, rng=rng, snapshot_every_n_days=10, purge_every_n_days=14)
        stale_at_200 = len([o for o in session._broker.get_open_orders() if o.poll_count == 0])

        self.assertLessEqual(
            stale_at_200, stale_at_100 + 5,
            f"Stale orphaned orders grew from {stale_at_100} (day 100) to {stale_at_200} (day 200) -- "
            "this is the unbounded-growth pattern the sweep fix exists to prevent.",
        )
        session.close()


class TestOperationalReports(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_every_logged_cycle_accounted_for_in_report(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B"), seed=5, failure_injection_rate=0.02,
        )
        session.run(num_days=10, cycles_per_day=2, rng=random.Random(5))
        start = session.state.cycle_log[0].as_of_date
        end = session.state.cycle_log[-1].as_of_date
        report = generate_report("Test", session.state.cycle_log, session._wrapped_store, start, end)
        total = report.executed_orders + report.rejected_orders + report.cancelled_orders + report.still_pending_orders
        self.assertEqual(total, len(session.state.cycle_log))
        session.close()

    def test_transient_read_failure_does_not_produce_false_anomaly(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A",), seed=6, failure_injection_rate=0.05,
        )
        session.run(num_days=15, cycles_per_day=2, rng=random.Random(6))
        start = session.state.cycle_log[0].as_of_date
        end = session.state.cycle_log[-1].as_of_date
        report = generate_report("Test", session.state.cycle_log, session._wrapped_store, start, end)
        confirmed_missing = [a for a in report.anomalies if a.startswith("CONFIRMED MISSING")]
        self.assertEqual(confirmed_missing, [], f"Found confirmed-missing anomalies: {confirmed_missing}")
        session.close()

    def test_report_date_filtering_excludes_out_of_range_entries(self):
        session = ExtendedPaperTradingSession(db_path=self.tmp_dir / "s.db", symbols=("A",), seed=7)
        session.run(num_days=10, cycles_per_day=1, rng=random.Random(7))
        all_dates = sorted({e.as_of_date for e in session.state.cycle_log})
        first_day_only = generate_report("Daily", session.state.cycle_log, session._wrapped_store, all_dates[0], all_dates[0])
        total_first_day = (first_day_only.executed_orders + first_day_only.rejected_orders
                            + first_day_only.cancelled_orders + first_day_only.still_pending_orders)
        entries_that_day = [e for e in session.state.cycle_log if e.as_of_date == all_dates[0]]
        self.assertEqual(total_first_day, len(entries_that_day))
        session.close()


class TestEventArchiveReconstruction(unittest.TestCase):
    """Regression for a real defect found via this milestone's own
    testing: without a durable archive, periodic events.clear() calls
    (for memory-boundedness) could leave the live event recorder
    completely empty at the end of a run -- violating requirement 5's
    'complete execution history must be reconstructable.' event_archive_path
    is what fixes this; these tests prove the fix, not just that the
    feature exists."""

    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_without_archive_live_recorder_can_end_up_empty(self):
        """Documents the ORIGINAL defect explicitly, as a permanent
        regression marker -- if this test ever starts failing, it means
        the underlying purge-timing behavior changed, which is worth
        knowing about even though the fix (below) makes it a non-issue
        in practice."""
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B"), seed=42,
            failure_injection_rate=0.08, restart_probability_per_day=0.15,
        )
        session.run(num_days=30, cycles_per_day=2, rng=random.Random(42))
        self.assertEqual(len(session._events.events()), 0)
        self.assertGreater(len(session.state.cycle_log), 0, "Real activity occurred despite the empty event recorder.")
        session.close()

    def test_with_archive_full_history_survives_clearing(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B"), seed=42,
            failure_injection_rate=0.08, restart_probability_per_day=0.15,
            event_archive_path=self.tmp_dir / "events.jsonl",
        )
        session.run(num_days=30, cycles_per_day=2, rng=random.Random(42))
        archived = session._archive.read_all()
        self.assertGreater(len(archived), 0, "Archive must contain events even though the live recorder was cleared.")
        session.close()

    def test_specific_order_lifecycle_fully_reconstructable(self):
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B"), seed=42,
            failure_injection_rate=0.08, restart_probability_per_day=0.15,
            event_archive_path=self.tmp_dir / "events.jsonl",
        )
        session.run(num_days=30, cycles_per_day=2, rng=random.Random(42))
        full_log = session.get_full_cycle_log()  # robust to cycle_log having been periodically trimmed
        target_cycle_id = full_log[5].cycle_id
        history = session._archive.reconstruct_by_cycle_id(target_cycle_id, live_event_recorder=session._events)
        self.assertGreater(len(history), 0)
        timestamps = [h["timestamp"] for h in history]
        self.assertEqual(timestamps, sorted(timestamps), "Reconstructed history must be chronologically ordered.")
        session.close()

    def test_archive_survives_a_simulated_restart(self):
        """The archive file itself must be durable across a session
        restart (a fresh ExecutionStateStore, fresh SubmissionOrchestrator)
        -- proven by reading it back from a SEPARATE EventArchive instance
        pointed at the same path, not just the one the session already
        holds a reference to."""
        from etf_platform.paper_trading_operations.event_archive import EventArchive

        archive_path = self.tmp_dir / "events.jsonl"
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B"), seed=42,
            failure_injection_rate=0.05, restart_probability_per_day=0.2,
            event_archive_path=archive_path,
        )
        session.run(num_days=20, cycles_per_day=2, rng=random.Random(42))
        session.close()

        independent_archive = EventArchive(archive_path)
        rows = independent_archive.read_all()
        self.assertGreater(len(rows), 0, "Archive must be readable independently of the session that wrote it.")

    def test_cycle_log_and_resource_snapshots_stay_bounded_over_a_long_run(self):
        """Regression for two real defects found via this milestone's own
        365-day validation run: cycle_log and resource_snapshots were both
        append-only with no pruning, unlike events and the broker's own
        order dict (which were already periodically managed). Found via
        the resource-trend report itself classifying memory_kb as
        GROWING, then traced to these specific structures."""
        session = ExtendedPaperTradingSession(
            db_path=self.tmp_dir / "s.db", symbols=("A", "B", "C"), seed=100,
            starting_cash=500_000_000.0, failure_injection_rate=0.02, restart_probability_per_day=0.05,
            event_archive_path=self.tmp_dir / "events.jsonl",
        )
        session.run(num_days=200, cycles_per_day=3, rng=random.Random(100), snapshot_every_n_days=5, purge_every_n_days=15)

        self.assertLess(
            len(session.state.cycle_log), 100,
            f"cycle_log has {len(session.state.cycle_log)} entries after 200 days -- should stay small via periodic archiving.",
        )
        from etf_platform.paper_trading_operations.session import MAX_RESOURCE_SNAPSHOTS_RETAINED

        self.assertLessEqual(len(session.state.resource_snapshots), MAX_RESOURCE_SNAPSHOTS_RETAINED)

        full_log = session.get_full_cycle_log()
        self.assertGreater(len(full_log), 100, "Full merged log (live + archived) should reflect real cumulative activity.")
        session.close()


class TestResourceTrends(unittest.TestCase):
    def test_stable_metric_classified_correctly(self):
        snapshots = [ResourceSnapshot(day=i, timestamp=None, memory_kb=100.0, db_size_bytes=1000,
                                       cumulative_events=50, open_orders_count=0) for i in range(10)]
        trend = analyze_trend(snapshots, "memory_kb")
        self.assertEqual(trend.verdict, "stable")

    def test_growing_metric_classified_correctly(self):
        snapshots = [ResourceSnapshot(day=i, timestamp=None, memory_kb=100.0 * (1 + i * 0.5), db_size_bytes=1000,
                                       cumulative_events=50, open_orders_count=0) for i in range(10)]
        trend = analyze_trend(snapshots, "memory_kb")
        self.assertEqual(trend.verdict, "growing")

    def test_insufficient_data_handled_gracefully(self):
        snapshots = [ResourceSnapshot(day=0, timestamp=None, memory_kb=100.0, db_size_bytes=1000,
                                       cumulative_events=50, open_orders_count=0)]
        trend = analyze_trend(snapshots, "memory_kb")
        self.assertEqual(trend.verdict, "insufficient_data")

    def test_analyze_all_trends_covers_every_leak_relevant_metric(self):
        snapshots = [ResourceSnapshot(day=i, timestamp=None, memory_kb=100.0, db_size_bytes=1000 + i,
                                       cumulative_events=50 * i, open_orders_count=0) for i in range(10)]
        trends = analyze_all_trends(snapshots)
        # cumulative_events deliberately excluded -- it's an intentionally
        # monotonic counter, not a leak signal (see resource_trends.py).
        self.assertEqual(set(trends.keys()), {"memory_kb", "db_size_bytes", "open_orders_count"})


if __name__ == "__main__":
    unittest.main()
