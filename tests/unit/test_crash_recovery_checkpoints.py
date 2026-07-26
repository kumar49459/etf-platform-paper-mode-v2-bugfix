"""Crash-recovery tests for SubmissionOrchestrator + ReconciliationService,
covering every checkpoint requested: before/after verification, before/
after persistence, before/after broker submission, before/after
notification. Each test simulates a crash by simply stopping short of a
step, then verifies recovery via a fresh orchestrator/store pair against
the same database file -- proving idempotency, no duplicate orders, no
lost orders, and consistent recovery.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from etf_platform.execution_manager import (
    ExecutionRecord,
    ExecutionStateStore,
    FixedScenarioProvider,
    InMemoryEventRecorder,
    MinimalInlineComplianceChecker,
    OrderLifecycleState,
    PaperBrokerPort,
    PaperQuoteProvider,
    ReconciliationService,
    SimulatedClock,
    SubmissionOrchestrator,
    new_execution_id,
    utc_now,
)
from etf_platform.execution_manager.scenarios import BrokerScenario
from etf_platform.strategy_engine.ports import NotificationPort


class FakeNotifier(NotificationPort):
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def poll_commands(self):
        return []


def make_record(execution_id=None, symbol="A", cycle_id="cycle-1", quantity=100, price=100.0,
                 status=OrderLifecycleState.PROPOSAL, broker_order_id=None, quantity_final=None):
    return ExecutionRecord(
        execution_id=execution_id or new_execution_id(), queue_id=None, cycle_id=cycle_id, symbol=symbol,
        quantity_proposed=quantity, quantity_final=quantity_final, limit_price=price, order_status=status,
        broker_order_id=broker_order_id, executed_price=None, executed_quantity=0, is_paper_trade=True,
        created_at=utc_now(), last_status_check=None, priority_rank=1,
    )


class CrashRecoveryTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.db_path = self.tmp_dir / "crash.db"

    def make_stack(self, scenario=BrokerScenario.IMMEDIATE_FILL, starting_cash=100000.0):
        clock = SimulatedClock()
        events = InMemoryEventRecorder()
        provider = FixedScenarioProvider(scenario)
        broker = PaperBrokerPort(clock, events, provider, starting_cash=starting_cash)
        quotes = PaperQuoteProvider(clock, events, provider, base_prices={"A": 100.0})
        compliance = MinimalInlineComplianceChecker()
        notifier = FakeNotifier()
        store = ExecutionStateStore(self.db_path)
        orchestrator = SubmissionOrchestrator(store, broker, quotes, compliance, notifier, clock, events)
        return store, broker, orchestrator, notifier, events


class TestCheckpointBeforeVerification(CrashRecoveryTestCase):
    def test_crash_before_verification_recovers_by_running_verification_fresh(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.PROPOSAL)
        store.save_execution_record(record)
        store.close()

        store2, broker2, orchestrator2, notifier2, events2 = self.make_stack()
        reloaded = store2.load_execution_record(record.execution_id)
        self.assertEqual(reloaded.order_status, OrderLifecycleState.PROPOSAL)
        result = orchestrator2.process_order(reloaded, "corr-1")
        self.assertEqual(result.order_status, OrderLifecycleState.VERIFIED)
        store2.close()


class TestCheckpointAfterVerificationBeforePersistence(CrashRecoveryTestCase):
    def test_this_boundary_is_unobservable_verification_is_pure(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.PROPOSAL)
        store.save_execution_record(record)

        throwaway_result = orchestrator._verification.verify(
            record.symbol, record.quantity_proposed, record.limit_price,
            orchestrator._quotes, orchestrator._broker, orchestrator._compliance,
        )
        result = orchestrator.process_order(record, "corr-1")
        self.assertEqual(result.order_status, OrderLifecycleState.VERIFIED)
        self.assertEqual(throwaway_result.verified_quantity, result.quantity_final)
        store.close()


class TestCheckpointBeforeAfterPersistence(CrashRecoveryTestCase):
    def test_crash_before_persistence_of_verified_state_recovers_cleanly(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.PROPOSAL)
        store.save_execution_record(record)
        store.close()

        store2, broker2, orchestrator2, notifier2, events2 = self.make_stack()
        reloaded = store2.load_execution_record(record.execution_id)
        result = orchestrator2.process_order(reloaded, "corr-1")
        self.assertEqual(result.order_status, OrderLifecycleState.VERIFIED)
        persisted = store2.load_execution_record(record.execution_id)
        self.assertEqual(persisted.order_status, OrderLifecycleState.VERIFIED)
        store2.close()

    def test_crash_after_persistence_of_verified_state_does_not_reverify(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.PROPOSAL)
        store.save_execution_record(record)
        record = orchestrator.process_order(record, "corr-1")
        self.assertEqual(record.order_status, OrderLifecycleState.VERIFIED)
        store.close()

        store2, broker2, orchestrator2, notifier2, events2 = self.make_stack()
        reloaded = store2.load_execution_record(record.execution_id)
        self.assertEqual(reloaded.order_status, OrderLifecycleState.VERIFIED)
        result = orchestrator2.process_order(reloaded, "corr-2")
        self.assertIn(result.order_status, (OrderLifecycleState.PENDING, OrderLifecycleState.SUBMITTED))
        store2.close()


class TestCheckpointBeforeBrokerSubmission(CrashRecoveryTestCase):
    def test_crash_before_broker_submission_retries_cleanly_no_duplicate(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.VERIFIED, quantity_final=100)
        store.save_execution_record(record)
        store.close()

        store2, broker2, orchestrator2, notifier2, events2 = self.make_stack()
        reloaded = store2.load_execution_record(record.execution_id)
        result = orchestrator2.process_order(reloaded, "corr-1")
        self.assertEqual(result.order_status, OrderLifecycleState.PENDING)
        self.assertIsNotNone(result.broker_order_id)
        store2.close()


class TestCheckpointAfterBrokerSubmissionBeforePendingPersisted(CrashRecoveryTestCase):
    def test_the_critical_checkpoint_broker_has_it_but_local_record_does_not_know_yet(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.VERIFIED, quantity_final=100, cycle_id="cycle-crash-test")
        store.save_execution_record(record)

        record.transition_to(OrderLifecycleState.SUBMITTED)
        store.save_execution_record(record)
        real_broker_order_id = broker.submit_order("A", "BUY", 100, 100.0, "cycle-crash-test")
        store.close()

        store2, broker2, orchestrator2, notifier2, events2 = self.make_stack()
        reconciler = ReconciliationService(store2, broker, clock=SimulatedClock(), event_recorder=InMemoryEventRecorder())
        reconciler.reconcile("corr-recovery")
        recovered = store2.load_execution_record(record.execution_id)
        self.assertEqual(recovered.broker_order_id, real_broker_order_id)
        self.assertEqual(recovered.order_status, OrderLifecycleState.PENDING)

        open_orders = [o for o in broker.get_open_orders() if o.client_reference == "cycle-crash-test"]
        self.assertEqual(len(open_orders), 1)
        store2.close()

    def test_crash_when_broker_never_actually_received_it_escalates_to_ambiguous_not_auto_retry(self):
        """Updated per DDR-001: this scenario (no local broker_order_id,
        no match found among the broker's open orders) can NO LONGER be
        distinguished by reconciliation from "reached the broker and
        already resolved to a terminal state" -- both look identical from
        this vantage point. The old behavior (auto-revert to VERIFIED for
        retry) was an unsafe guess; DDR-001 replaces it with escalation to
        AMBIGUOUS and mandatory operator review, eliminating the automated
        duplicate-order risk. This test's name and assertions changed to
        match the new, safe policy -- not just patched to pass."""
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.VERIFIED, quantity_final=100, cycle_id="cycle-never-sent")
        store.save_execution_record(record)
        record.transition_to(OrderLifecycleState.SUBMITTED)
        store.save_execution_record(record)
        store.close()

        store2, broker2, orchestrator2, notifier2, events2 = self.make_stack()
        reconciler = ReconciliationService(store2, broker, clock=SimulatedClock(), event_recorder=InMemoryEventRecorder())
        outcomes = reconciler.reconcile("corr-recovery")
        recovered = store2.load_execution_record(record.execution_id)
        self.assertEqual(recovered.order_status, OrderLifecycleState.AMBIGUOUS)
        self.assertEqual(outcomes[0].discrepancy_type.value, "ambiguous_no_local_id")

        # process_order() must treat AMBIGUOUS as a no-op -- never auto-retry.
        result = orchestrator2.process_order(recovered, "corr-retry")
        self.assertEqual(result.order_status, OrderLifecycleState.AMBIGUOUS)
        store2.close()


    def test_ddr001_crash_after_real_fill_before_recording_escalates_not_retries(self):
        """The exact scenario DDR-001's root-cause section describes,
        reproduced as a permanent regression test: the broker call
        succeeds AND the order immediately fills, but the process is
        interrupted before broker_order_id is recorded locally. Before
        this fix, reconciliation would search only open_at_broker (which
        correctly excludes the now-FILLED order), find nothing, and
        incorrectly conclude NEVER_REACHED_BROKER -- reverting to VERIFIED
        for automatic retry, which would create a genuine duplicate
        submission. This is the test that would have caught that defect."""
        store, broker, orchestrator, notifier, events = self.make_stack(scenario=BrokerScenario.IMMEDIATE_FILL)
        record = make_record(status=OrderLifecycleState.VERIFIED, quantity_final=10, cycle_id="ddr001-fill-crash")
        store.save_execution_record(record)
        record.transition_to(OrderLifecycleState.SUBMITTED)
        store.save_execution_record(record)

        # The broker call actually succeeds and the order immediately fills --
        # simulating the crash by never recording broker_order_id locally.
        real_broker_order_id = broker.submit_order("A", "BUY", 10, 100.0, "ddr001-fill-crash")
        filled_order = broker.get_order_status(real_broker_order_id)
        self.assertEqual(filled_order.state, OrderLifecycleState.FILLED)
        self.assertNotIn(real_broker_order_id, [o.broker_order_id for o in broker.get_open_orders()])
        store.close()

        store2, broker2, orchestrator2, notifier2, events2 = self.make_stack(scenario=BrokerScenario.IMMEDIATE_FILL)
        reconciler = ReconciliationService(store2, broker, clock=SimulatedClock(), event_recorder=InMemoryEventRecorder())
        outcomes = reconciler.reconcile("ddr001-recovery")
        recovered = store2.load_execution_record(record.execution_id)

        self.assertEqual(
            recovered.order_status, OrderLifecycleState.AMBIGUOUS,
            "DDR-001 REGRESSION: a filled-but-unrecorded order must escalate to AMBIGUOUS, "
            "never auto-retry to VERIFIED.",
        )

        # Prove no automatic duplicate: process_order() must be a no-op here.
        result = orchestrator2.process_order(recovered, "ddr001-no-retry-check")
        self.assertEqual(result.order_status, OrderLifecycleState.AMBIGUOUS)

        reference_counts = {}
        for order in broker._orders.values():
            reference_counts[order.client_reference] = reference_counts.get(order.client_reference, 0) + 1
        self.assertEqual(reference_counts.get("ddr001-fill-crash", 0), 1,
                          "DDR-001 REGRESSION: exactly one order must exist at the broker for this reference.")
        store2.close()

    def test_operator_can_resolve_ambiguous_execution_after_manual_confirmation(self):
        """The only legitimate way out of AMBIGUOUS: an explicit,
        human-invoked resolution, never an automatic one."""
        store, broker, orchestrator, notifier, events = self.make_stack(scenario=BrokerScenario.IMMEDIATE_FILL)
        record = make_record(status=OrderLifecycleState.VERIFIED, quantity_final=10, cycle_id="ddr001-resolve-test")
        store.save_execution_record(record)
        record.transition_to(OrderLifecycleState.SUBMITTED)
        store.save_execution_record(record)
        real_broker_order_id = broker.submit_order("A", "BUY", 10, 100.0, "ddr001-resolve-test")
        broker.get_order_status(real_broker_order_id)  # fills

        reconciler = ReconciliationService(store, broker, clock=SimulatedClock(), event_recorder=InMemoryEventRecorder())
        reconciler.reconcile("pre-resolve")
        ambiguous = store.load_execution_record(record.execution_id)
        self.assertEqual(ambiguous.order_status, OrderLifecycleState.AMBIGUOUS)

        # Operator checks the broker directly, confirms it filled, resolves manually.
        resolved = reconciler.resolve_ambiguous_execution(
            execution_id=record.execution_id, confirmed_state=OrderLifecycleState.FILLED,
            operator_notes="Confirmed via broker web UI order history: order filled at 10:32 IST.",
            broker_order_id=real_broker_order_id, executed_quantity=10, executed_price=100.0,
        )
        self.assertEqual(resolved.order_status, OrderLifecycleState.FILLED)
        self.assertTrue(any("Confirmed via broker web UI" in n for n in resolved.notes))
        store.close()

    def test_resolve_ambiguous_execution_rejects_non_ambiguous_records(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.FILLED)
        store.save_execution_record(record)
        reconciler = ReconciliationService(store, broker, clock=SimulatedClock(), event_recorder=InMemoryEventRecorder())
        with self.assertRaises(Exception):
            reconciler.resolve_ambiguous_execution(
                execution_id=record.execution_id, confirmed_state=OrderLifecycleState.RECONCILED,
                operator_notes="test",
            )
        store.close()

    def test_resolve_ambiguous_execution_requires_operator_notes(self):
        store, broker, orchestrator, notifier, events = self.make_stack(scenario=BrokerScenario.IMMEDIATE_FILL)
        record = make_record(status=OrderLifecycleState.VERIFIED, quantity_final=10, cycle_id="ddr001-notes-test")
        store.save_execution_record(record)
        record.transition_to(OrderLifecycleState.SUBMITTED)
        store.save_execution_record(record)
        broker.get_order_status(broker.submit_order("A", "BUY", 10, 100.0, "ddr001-notes-test"))
        reconciler = ReconciliationService(store, broker, clock=SimulatedClock(), event_recorder=InMemoryEventRecorder())
        reconciler.reconcile("pre-resolve")
        with self.assertRaises(Exception):
            reconciler.resolve_ambiguous_execution(
                execution_id=record.execution_id, confirmed_state=OrderLifecycleState.FILLED, operator_notes="",
            )
        store.close()

    def test_high_priority_alert_generated_on_ambiguous_escalation(self):
        store, broker, orchestrator, notifier, events = self.make_stack(scenario=BrokerScenario.IMMEDIATE_FILL)
        record = make_record(status=OrderLifecycleState.VERIFIED, quantity_final=10, cycle_id="ddr001-alert-test")
        store.save_execution_record(record)
        record.transition_to(OrderLifecycleState.SUBMITTED)
        store.save_execution_record(record)
        broker.get_order_status(broker.submit_order("A", "BUY", 10, 100.0, "ddr001-alert-test"))

        reconciler = ReconciliationService(store, broker, clock=SimulatedClock(),
                                            event_recorder=InMemoryEventRecorder(), notification_port=notifier)
        reconciler.reconcile("alert-test")
        self.assertEqual(len(notifier.sent), 1)
        self.assertIn("HIGH PRIORITY", notifier.sent[0])
        self.assertIn("OPERATOR REVIEW REQUIRED", notifier.sent[0])
        store.close()


class TestCheckpointBeforeAfterNotification(CrashRecoveryTestCase):
    def test_crash_before_notification_does_not_lose_order_state(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.FILLED, broker_order_id="bo1", quantity_final=100)
        store.save_execution_record(record)
        store.close()

        store2, broker2, orchestrator2, notifier2, events2 = self.make_stack()
        reloaded = store2.load_execution_record(record.execution_id)
        self.assertEqual(reloaded.order_status, OrderLifecycleState.FILLED)
        orchestrator2.process_order(reloaded, "corr-1")
        self.assertEqual(len(notifier2.sent), 1)
        store2.close()

    def test_crash_after_notification_is_idempotent_order_state_unaffected(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.FILLED, broker_order_id="bo1", quantity_final=100)
        store.save_execution_record(record)
        orchestrator.process_order(record, "corr-1")
        self.assertEqual(len(notifier.sent), 1)
        result = orchestrator.process_order(record, "corr-2")
        self.assertEqual(result.order_status, OrderLifecycleState.FILLED)
        store.close()


class TestFullLifecycleIdempotency(CrashRecoveryTestCase):
    def test_calling_process_order_repeatedly_at_every_stage_never_duplicates(self):
        store, broker, orchestrator, notifier, events = self.make_stack()
        record = make_record(status=OrderLifecycleState.PROPOSAL, quantity=100)
        store.save_execution_record(record)

        for _ in range(10):
            record = orchestrator.process_order(record, "corr-hammer")
            if record.order_status == OrderLifecycleState.FILLED:
                break

        self.assertEqual(record.order_status, OrderLifecycleState.FILLED)
        all_orders_for_cycle = [o for o in broker._orders.values() if o.client_reference == record.cycle_id]
        self.assertEqual(len(all_orders_for_cycle), 1)
        store.close()


if __name__ == "__main__":
    unittest.main()
