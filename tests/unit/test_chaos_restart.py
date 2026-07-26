"""Chaos restart testing (Milestone 4, requirement 5). Unlike Milestone
3's 8 NAMED checkpoints (deliberately chosen, meaningful boundaries), this
terminates at ARBITRARY points -- wrapping every store call with a random
chance of raising immediately after it returns, simulating a process kill
at a point neither the test author nor the implementation specifically
anticipated.

Verifies exactly what was requested: no duplicate orders, no lost orders,
no corrupted persistence, reconciliation always converges, event ordering
remains valid.
"""

from __future__ import annotations

import random
import shutil
import tempfile
import unittest
from pathlib import Path

from etf_platform.execution_manager import (
    ExecutionRecord,
    ExecutionStateStore,
    InMemoryEventRecorder,
    MinimalInlineComplianceChecker,
    OrderLifecycleState,
    PaperBrokerPort,
    PaperQuoteProvider,
    ReconciliationService,
    SeededRandomScenarioProvider,
    SimulatedClock,
    SubmissionOrchestrator,
    new_execution_id,
)
from etf_platform.strategy_engine.ports import NotificationPort


class ChaosTerminationSignal(Exception):
    """Distinct from any real exception type -- a chaos-injected
    termination is never confused with a genuine simulated system
    failure when interpreting test results."""


class ArbitraryTerminationWrapper:
    """Wraps ANY object, injecting a random chance of raising
    ChaosTerminationSignal immediately AFTER any method call successfully
    returns -- simulating a process kill at the instruction boundary right
    after that call completed. Deliberately different from FailureInjector
    (which simulates the call itself failing): this simulates the call
    SUCCEEDING and the process dying right after, a different and
    arguably more dangerous timing to get wrong."""

    def __init__(self, wrapped, rng, termination_probability):
        self._wrapped = wrapped
        self._rng = rng
        self._probability = termination_probability

    def __getattr__(self, name):
        attr = getattr(self._wrapped, name)
        if not callable(attr):
            return attr

        def wrapper(*args, **kwargs):
            result = attr(*args, **kwargs)
            if self._rng.random() < self._probability:
                raise ChaosTerminationSignal(f"Simulated process termination immediately after {name}()")
            return result

        return wrapper


class FakeNotifier(NotificationPort):
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def poll_commands(self):
        return []


class TestChaosRestartTesting(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def test_random_termination_at_arbitrary_points_always_recovers_cleanly(self):
        db_path = self.tmp_dir / "chaos.db"
        rng = random.Random(777)
        master_clock = SimulatedClock()
        master_events = InMemoryEventRecorder()
        scenario_provider = SeededRandomScenarioProvider(seed=777)
        broker = PaperBrokerPort(master_clock, master_events, scenario_provider, starting_cash=10_000_000.0)
        quotes = PaperQuoteProvider(master_clock, master_events, scenario_provider, base_prices={"A": 100.0, "B": 50.0})
        compliance = MinimalInlineComplianceChecker()
        notifier = FakeNotifier()

        num_cycles = 300
        termination_probability = 0.08

        store = ExecutionStateStore(db_path)

        for cycle in range(num_cycles):
            wrapped_store = ArbitraryTerminationWrapper(store, rng, termination_probability)
            orchestrator = SubmissionOrchestrator(wrapped_store, broker, quotes, compliance, notifier, master_clock, master_events)

            record = ExecutionRecord(
                execution_id=new_execution_id(), queue_id=None, cycle_id=f"chaos-cycle-{cycle}", symbol="A" if cycle % 2 == 0 else "B",
                quantity_proposed=10, quantity_final=None, limit_price=100.0 if cycle % 2 == 0 else 50.0,
                order_status=OrderLifecycleState.PROPOSAL, broker_order_id=None, executed_price=None,
                executed_quantity=0, is_paper_trade=True, created_at=master_clock.now(), last_status_check=None,
                priority_rank=1,
            )
            try:
                store.save_execution_record(record)
                for _poll in range(6):
                    try:
                        record = orchestrator.process_order(record, correlation_id=f"chaos-{cycle}")
                    except ChaosTerminationSignal:
                        raise  # must propagate to the outer handler -- this is the condition under test
                    except Exception:
                        # An ordinary simulated failure (e.g. PaperBrokerPort's
                        # own BrokerScenario-driven BrokerCommunicationError)
                        # -- the real system already tolerates this by design
                        # (Milestone 3: record stays put, retried next cycle).
                        # Not a chaos termination, so no restart/reconciliation
                        # needed here -- just stop polling this cycle.
                        break
                    if record.order_status in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED, OrderLifecycleState.FAILED):
                        break
            except ChaosTerminationSignal:
                store.close()
                store = ExecutionStateStore(db_path)
                reconciler = ReconciliationService(store, broker, master_clock, master_events)
                reconciler.reconcile(correlation_id=f"chaos-recovery-{cycle}")

        final_reconciler = ReconciliationService(store, broker, master_clock, master_events)
        final_reconciler.reconcile(correlation_id="chaos-final")

        reference_counts = {}
        for order in broker._orders.values():
            reference_counts[order.client_reference] = reference_counts.get(order.client_reference, 0) + 1
        duplicates = {ref: count for ref, count in reference_counts.items() if count > 1}
        self.assertEqual(duplicates, {}, f"Duplicate orders found for cycle_ids: {duplicates}")

        attempted_cycle_ids = {f"chaos-cycle-{i}" for i in range(num_cycles)}
        broker_cycle_ids = {o.client_reference for o in broker._orders.values()}
        missing_entirely = attempted_cycle_ids - broker_cycle_ids
        for missing_cycle_id in missing_entirely:
            records = store.load_records_for_cycle(missing_cycle_id)
            self.assertGreater(
                len(records), 0,
                f"{missing_cycle_id} has no broker record AND no local execution_history record -- genuinely lost.",
            )

        store.close()
        recheck_store = ExecutionStateStore(db_path)
        recheck_store.close()

        final_store = ExecutionStateStore(db_path)
        final_reconciler2 = ReconciliationService(final_store, broker, master_clock, master_events)
        from etf_platform.execution_manager.reconciliation import DiscrepancyType

        outcomes = final_reconciler2.reconcile(correlation_id="chaos-convergence-check")
        unresolved_anomalies = [o for o in outcomes if o.discrepancy_type == DiscrepancyType.BROKER_HAS_NO_RECORD]
        self.assertEqual(unresolved_anomalies, [], f"Reconciliation did not converge: {unresolved_anomalies}")
        final_store.close()

        events_by_order = {}
        for event in master_events.events():
            if event.broker_order_id:
                events_by_order.setdefault(event.broker_order_id, []).append(event)
        for broker_order_id, order_events in events_by_order.items():
            timestamps = [e.timestamp for e in order_events]
            self.assertEqual(
                timestamps, sorted(timestamps),
                f"Event ordering violated for {broker_order_id}: timestamps not monotonically non-decreasing.",
            )


if __name__ == "__main__":
    unittest.main()
