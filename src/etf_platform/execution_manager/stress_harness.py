"""Large-scale execution stress test (Milestone 3, requirement 8,
mandatory) and property tests (requirement 5).

The broker (representing Kite) is a SINGLE long-lived instance for the
entire run -- a real broker doesn't reset when Module 28's own process
restarts. Only the local ExecutionStateStore is closed and reopened to
simulate a Module 28 restart, exactly matching what "restart" means in
this architecture: the client process restarts, the broker's own state
does not.
"""

from __future__ import annotations

import random
import time
import tracemalloc
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from etf_platform.execution_manager import (
    ExecutionRecord,
    ExecutionStateStore,
    InMemoryEventRecorder,
    InvalidLifecycleTransitionError,
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
from etf_platform.execution_manager.failure_injection import (
    FailureInjectingComplianceChecker,
    FailureInjectingNotifier,
    FailureInjectingQuoteProvider,
    FailureInjectingStore,
    FailureInjector,
)
from etf_platform.strategy_engine.ports import NotificationPort


class NoOpNotifier(NotificationPort):
    def __init__(self):
        self.count = 0

    def send(self, message):
        self.count += 1

    def poll_commands(self):
        return []


@dataclass
class StressTestReport:
    num_cycles: int
    restarts_performed: int
    invalid_transitions_detected: int
    duplicate_submissions_detected: int
    lost_orders_detected: int
    negative_cash_events: int
    reconciliation_runs: int
    reconciliation_mismatches_found: int
    orphan_orders_at_end: int
    elapsed_seconds: float
    peak_memory_kb: float
    final_db_size_bytes: int
    total_events_emitted: int
    invariant_violations: list = field(default_factory=list)

    @property
    def throughput_per_second(self):
        return self.num_cycles / self.elapsed_seconds if self.elapsed_seconds > 0 else 0.0

    @property
    def avg_latency_ms(self):
        return (self.elapsed_seconds / self.num_cycles) * 1000 if self.num_cycles else 0.0

    @property
    def restart_success_rate(self):
        expected_clean_recoveries = self.restarts_performed
        actual_clean = expected_clean_recoveries - len(self.invariant_violations)
        return actual_clean / expected_clean_recoveries if expected_clean_recoveries > 0 else 1.0


def run_stress_test(
    num_cycles, seed, db_path, restart_probability=0.03, symbols=("A", "B", "C"),
    starting_cash=50_000_000.0, max_polls_per_cycle=8, purge_interval=1000,
    failure_injection_rate=0.0,
):
    """failure_injection_rate=0.0 (default) preserves Milestone 3's exact
    behavior -- BrokerScenario-driven failures only. A nonzero rate
    additionally wraps the store, notifier, quote provider, and compliance
    checker with FailureInjector (Milestone 4, requirement 2), injecting
    failures independent of BrokerScenario's own order-lifecycle failure
    modes -- e.g. a database failure can now occur even when the broker
    scenario for that cycle is IMMEDIATE_FILL."""
    rng = random.Random(seed)
    master_clock = SimulatedClock()
    master_events = InMemoryEventRecorder()
    scenario_provider = SeededRandomScenarioProvider(seed=seed)
    broker = PaperBrokerPort(master_clock, master_events, scenario_provider, starting_cash=starting_cash)
    quotes = PaperQuoteProvider(master_clock, master_events, scenario_provider,
                                 base_prices={s: 100.0 + i * 17.3 for i, s in enumerate(symbols)})
    compliance = MinimalInlineComplianceChecker()
    notifier = NoOpNotifier()

    injector = None
    if failure_injection_rate > 0:
        injector = FailureInjector(seed=seed + 500000, failure_rate=failure_injection_rate)
        quotes = FailureInjectingQuoteProvider(quotes, injector)
        compliance = FailureInjectingComplianceChecker(compliance, injector)
        notifier = FailureInjectingNotifier(notifier, injector)
        # The store is wrapped fresh on every "restart" below, not just
        # once here -- see the restart block.

    def _wrap_store(real_store):
        return FailureInjectingStore(real_store, injector) if injector else real_store

    store = ExecutionStateStore(db_path)
    wrapped_store = _wrap_store(store)
    orchestrator = SubmissionOrchestrator(wrapped_store, broker, quotes, compliance, notifier, master_clock, master_events)

    report = StressTestReport(
        num_cycles=num_cycles, restarts_performed=0, invalid_transitions_detected=0,
        duplicate_submissions_detected=0, lost_orders_detected=0, negative_cash_events=0,
        reconciliation_runs=0, reconciliation_mismatches_found=0, orphan_orders_at_end=0,
        elapsed_seconds=0.0, peak_memory_kb=0.0, final_db_size_bytes=0, total_events_emitted=0,
    )

    tracemalloc.start()
    start_time = time.perf_counter()
    cumulative_events_emitted = 0
    # Found while reviewing this run's own results, not the system under
    # test: reading len(master_events.events()) only ONCE at the very end
    # is wrong whenever events.clear() is called mid-run (as the periodic
    # purge step below does) -- everything cleared before the final read
    # silently vanishes from the reported total. Tracking a running
    # cumulative counter instead, incremented immediately before each
    # clear (and once more at the very end for whatever's left
    # unclearer), is what makes "total events generated" an honest number
    # rather than an artifact of when the last clear happened to land.

    for cycle in range(num_cycles):
        symbol = rng.choice(symbols)
        quantity = rng.randint(1, 200)
        price = 100.0 + rng.random() * 50

        record = ExecutionRecord(
            execution_id=new_execution_id(), queue_id=None, cycle_id=f"cycle-{seed}-{cycle}", symbol=symbol,
            quantity_proposed=quantity, quantity_final=None, limit_price=price,
            order_status=OrderLifecycleState.PROPOSAL, broker_order_id=None, executed_price=None,
            executed_quantity=0, is_paper_trade=True, created_at=master_clock.now(), last_status_check=None,
            priority_rank=1,
        )
        try:
            wrapped_store.save_execution_record(record)
        except Exception:
            continue  # injected database failure on the very first write for this cycle -- skip, retried as a fresh cycle next time in a real system

        for _poll in range(max_polls_per_cycle):
            if rng.random() < restart_probability:
                store.close()
                store = ExecutionStateStore(db_path)
                wrapped_store = _wrap_store(store)
                orchestrator = SubmissionOrchestrator(
                    wrapped_store, broker, quotes, compliance, notifier, master_clock, master_events,
                )
                reconciler = ReconciliationService(wrapped_store, broker, master_clock, master_events)
                try:
                    outcomes = reconciler.reconcile(correlation_id=f"restart-{report.restarts_performed}")
                except Exception:
                    outcomes = []  # injected database failure during reconciliation itself -- next restart will retry
                report.restarts_performed += 1
                report.reconciliation_runs += 1
                # Found while reviewing this run's own results: counting
                # every non-NO_DISCREPANCY outcome as a "mismatch" silently
                # included STILL_OPEN_AT_BROKER, which is not a discrepancy
                # at all -- just an order legitimately still in flight when
                # a restart happened to catch it. With frequent restarts,
                # this benign classification dominates and makes the metric
                # meaningless (it was measuring "how often did a restart
                # catch something mid-flight," not "how many real problems
                # were found"). Only NEVER_REACHED_BROKER, STATE_MISMATCH,
                # and BROKER_HAS_NO_RECORD are genuine discrepancies.
                report.reconciliation_mismatches_found += sum(
                    1 for o in outcomes
                    if o.discrepancy_type.value not in ("no_discrepancy", "still_open_at_broker")
                )
                try:
                    reloaded = wrapped_store.load_execution_record(record.execution_id)
                    if reloaded is not None:
                        record = reloaded
                except Exception:
                    pass  # injected database failure on reload -- proceed with the in-memory record we already have

            try:
                record = orchestrator.process_order(record, correlation_id=f"cycle-{seed}-{cycle}")
            except InvalidLifecycleTransitionError:
                report.invalid_transitions_detected += 1
                report.invariant_violations.append(f"cycle {cycle}: invalid transition")
                break
            except Exception:
                break

            if record.order_status in (
                OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED, OrderLifecycleState.FAILED,
            ):
                break

        if broker.get_available_cash() < 0:
            report.negative_cash_events += 1
            report.invariant_violations.append(f"cycle {cycle}: negative cash ({broker.get_available_cash()})")

        master_clock.advance(timedelta(minutes=rng.randint(1, 90)))

        if cycle % purge_interval == purge_interval - 1:
            cumulative_events_emitted += len(master_events.events())
            master_events.clear()
            # Found by actually running this harness: purging the broker's
            # memory of terminal orders on an independent schedule, without
            # first confirming those specific orders were already
            # reconciled locally, destroys reconciliation's ability to
            # later confirm them -- producing FALSE "broker has no record"
            # classifications for orders that actually completed
            # correctly. The fix is a real operational rule, not just a
            # test-harness patch: reconcile FIRST (to advance everything
            # possible to RECONCILED), THEN purge only what's confirmed
            # reconciled. Purge-then-reconcile is unsafe; reconcile-then-
            # purge is the only correct ordering.
            interim_reconciler = ReconciliationService(wrapped_store, broker, master_clock, master_events)
            try:
                interim_reconciler.reconcile(correlation_id=f"pre-purge-{cycle}")
            except Exception:
                pass  # injected failure -- purge deferred to next interval rather than risking an unsafe purge
            else:
                report.reconciliation_runs += 1
                broker.purge_terminal_orders()

    final_reconciler = ReconciliationService(wrapped_store, broker, master_clock, master_events)
    final_outcomes = []
    for attempt in range(5):  # a real system retries transient failures rather than giving up after one
        try:
            final_outcomes = final_reconciler.reconcile(correlation_id=f"final-reconciliation-attempt-{attempt}")
            break
        except Exception:
            report.reconciliation_runs += 1
            continue
    else:
        report.invariant_violations.append(
            "final reconciliation did not converge after 5 retries -- a genuine non-convergence, not just bad luck"
        )
    report.reconciliation_runs += 1
    unresolved_after_final_reconciliation = [
        o for o in final_outcomes if o.discrepancy_type.value == "broker_has_no_record"
    ]
    report.orphan_orders_at_end = len(unresolved_after_final_reconciliation)

    reference_counts = {}
    for order in broker._orders.values():
        reference_counts[order.client_reference] = reference_counts.get(order.client_reference, 0) + 1
    duplicates = {ref: count for ref, count in reference_counts.items() if count > 1}
    report.duplicate_submissions_detected = len(duplicates)
    if duplicates:
        report.invariant_violations.append(f"duplicate submissions for cycle_ids: {list(duplicates.keys())[:5]}")

    elapsed = time.perf_counter() - start_time
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    report.elapsed_seconds = elapsed
    report.peak_memory_kb = peak / 1024
    report.final_db_size_bytes = Path(db_path).stat().st_size if Path(db_path).exists() else 0
    report.total_events_emitted = cumulative_events_emitted + len(master_events.events())

    store.close()
    return report
