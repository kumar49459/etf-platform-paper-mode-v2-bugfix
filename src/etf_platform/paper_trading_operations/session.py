"""ExtendedPaperTradingSession (Milestone 5B, requirement 1). Deliberately
NOT new architecture -- a thin continuous-operation wrapper around Module
28's existing, already-stress-tested machinery: SubmissionOrchestrator,
ReconciliationService, PaperBrokerPort, PaperQuoteProvider,
ExecutionStateStore, and the failure_injection framework from Milestone 4.

The one genuinely new idea here is OPERATIONAL FRAMING rather than a
stress-test framing: cycles are distributed across many simulated days
(not all bunched together), reconciliation runs on a realistic daily
schedule (not only opportunistically after a restart), and every
processed record is logged in a form the operational report generator
(reports.py) can consume directly -- without querying the frozen
ExecutionStateStore in any way it wasn't already designed for (it has no
date-range query method, and this module does not add one to it).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date as _date_type
from datetime import timedelta

from etf_platform.execution_manager import (
    ExecutionEvent,
    ExecutionEventType,
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
from etf_platform.execution_manager.failure_injection import (
    FailureInjectingComplianceChecker,
    FailureInjectingNotifier,
    FailureInjectingQuoteProvider,
    FailureInjectingStore,
    FailureInjector,
)
from etf_platform.paper_trading_operations.event_archive import CycleLogArchive, EventArchive
from etf_platform.strategy_engine.ports import NotificationPort


MAX_RESOURCE_SNAPSHOTS_RETAINED = 500
"""Provisional, disclosed bound: enough for analyze_all_trends()'s first-
half/second-half comparison to remain meaningful (hundreds of points) far
beyond any realistic snapshot_every_n_days cadence, while keeping the
list itself bounded regardless of total session duration -- found
necessary via this milestone's own 365-day validation run, where this
structure was the last remaining unbounded-growth source after fixing
cycle_log."""


class NoOpNotifier(NotificationPort):
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)

    def poll_commands(self):
        return []


@dataclass(frozen=True)
class CycleLogEntry:
    execution_id: str
    cycle_id: str
    symbol: str
    quantity_proposed: int
    final_status: OrderLifecycleState
    broker_order_id: object
    executed_quantity: int
    executed_price: object
    limit_price: float
    as_of_date: object
    rejection_notes: tuple = ()


@dataclass
class ResourceSnapshot:
    day: int
    timestamp: object
    memory_kb: float
    db_size_bytes: int
    cumulative_events: int
    open_orders_count: int


@dataclass
class SessionState:
    cycle_log: list = field(default_factory=list)
    resource_snapshots: list = field(default_factory=list)
    """Found via this milestone's own 365-day validation run to be the
    LAST remaining unbounded structure after fixing cycle_log: same
    append-only pattern, small in absolute terms (~4KB/year) but
    structurally identical to the bug just fixed. Unlike cycle_log
    (which needs full-history retention for report generation via
    CycleLogArchive), resource_snapshots only needs recent history for
    analyze_all_trends()'s first-half/second-half comparison -- a
    bounded rolling window is the simpler, correct fix here, not a
    third archive class for data nothing needs to retain in full."""
    restarts_performed: int = 0
    reconciliation_runs: int = 0
    reconciliation_mismatches: int = 0
    invariant_violations: list = field(default_factory=list)
    outstanding_execution_ids: set = field(default_factory=set)
    """Found via this milestone's own long-duration run (Milestone 5B):
    a record that doesn't reach a terminal state within its OWN cycle's
    poll budget (e.g. because reconciliation only adopts a broker_order_id
    for it LATER, after the cycle's loop already exhausted its attempts)
    was previously never revisited again -- permanently orphaned, growing
    open_orders_count without bound over a long run. Tracking every
    non-terminal execution_id here so sweep_outstanding() (below) can
    give them further chances to resolve, is the fix."""


class ExtendedPaperTradingSession:
    def __init__(
        self, db_path, symbols, seed, starting_cash=10_000_000.0, failure_injection_rate=0.0,
        restart_probability_per_day=0.05, reconcile_every_n_days=1, event_archive_path=None,
    ):
        self._db_path = db_path
        self._symbols = symbols
        self._seed = seed
        self._restart_probability_per_day = restart_probability_per_day
        self._reconcile_every_n_days = reconcile_every_n_days
        self._archive = EventArchive(event_archive_path) if event_archive_path else None
        self._cycle_log_archive = CycleLogArchive(f"{event_archive_path}.cyclelog") if event_archive_path else None
        """Found via this milestone's own 365-day validation run: the
        resource-trend report classified memory_kb as GROWING, traced to
        SessionState.cycle_log being append-only with no pruning -- unlike
        events and the broker's own order dict, which were already
        periodically managed. Same fix pattern as EventArchive, applied
        symmetrically rather than leaving one structure unbounded while
        fixing the other."""
        """Found via this milestone's own long-duration testing: the
        original version called self._events.clear() directly with no
        durable archive first -- a run whose purge cadence happened to
        land near the last simulated day left the live event recorder
        completely empty, violating requirement 5 ("the complete
        execution history must be reconstructable"). event_archive_path
        is optional (defaulting to None, preserving prior test behavior
        exactly for callers that don't need durability) but any real
        operational deployment must supply one."""

        # Found while investigating an intermittent test failure in
        # Production Verification's audit-reconstruction test: SimulatedClock()
        # with no explicit start defaults to real wall-clock time (utc_now()),
        # meaning two runs with the IDENTICAL seed were never actually fully
        # deterministic -- only the random DECISIONS were reproducible, not
        # the timestamps events received, which could interact with
        # time-sensitive logic (purge cadence, archive boundaries) in ways
        # that varied run to run. Fixed by anchoring the clock to a fixed
        # epoch, making "same seed = fully identical run" genuinely true.
        from datetime import datetime, timezone

        fixed_epoch = datetime(2024, 1, 1, tzinfo=timezone.utc)
        self._clock = SimulatedClock(start=fixed_epoch)
        self._events = InMemoryEventRecorder()
        self._scenario_provider = SeededRandomScenarioProvider(seed=seed)
        self._broker = PaperBrokerPort(self._clock, self._events, self._scenario_provider, starting_cash=starting_cash)
        quotes = PaperQuoteProvider(self._clock, self._events, self._scenario_provider,
                                     base_prices={s: 100.0 + i * 13.7 for i, s in enumerate(symbols)})
        compliance = MinimalInlineComplianceChecker()
        notifier = NoOpNotifier()

        self._injector = None
        if failure_injection_rate > 0:
            self._injector = FailureInjector(seed=seed + 777777, failure_rate=failure_injection_rate)
            quotes = FailureInjectingQuoteProvider(quotes, self._injector)
            compliance = FailureInjectingComplianceChecker(compliance, self._injector)
            notifier = FailureInjectingNotifier(notifier, self._injector)
        self._quotes = quotes
        self._compliance = compliance
        self._notifier = notifier

        self._store = ExecutionStateStore(db_path)
        self._wrapped_store = FailureInjectingStore(self._store, self._injector) if self._injector else self._store
        self._orchestrator = SubmissionOrchestrator(
            self._wrapped_store, self._broker, self._quotes, self._compliance, self._notifier, self._clock, self._events,
        )
        self.state = SessionState()
        self._cursor_symbol_index = 0

    def _restart(self):
        self._store.close()
        self._store = ExecutionStateStore(self._db_path)
        self._wrapped_store = FailureInjectingStore(self._store, self._injector) if self._injector else self._store
        self._orchestrator = SubmissionOrchestrator(
            self._wrapped_store, self._broker, self._quotes, self._compliance, self._notifier, self._clock, self._events,
        )
        reconciler = ReconciliationService(self._wrapped_store, self._broker, self._clock, self._events)
        try:
            outcomes = reconciler.reconcile(correlation_id=f"restart-{self.state.restarts_performed}")
            self.state.reconciliation_runs += 1
            self.state.reconciliation_mismatches += sum(
                1 for o in outcomes if o.discrepancy_type.value not in ("no_discrepancy", "still_open_at_broker")
            )
        except Exception:
            pass
        self.state.restarts_performed += 1

    def _process_one_cycle(self, day_index, cycles_per_day_index):
        symbol = self._symbols[self._cursor_symbol_index % len(self._symbols)]
        self._cursor_symbol_index += 1
        cycle_id = f"session-{self._seed}-day{day_index}-c{cycles_per_day_index}"

        record = ExecutionRecord(
            execution_id=new_execution_id(), queue_id=None, cycle_id=cycle_id, symbol=symbol,
            quantity_proposed=10, quantity_final=None, limit_price=100.0, order_status=OrderLifecycleState.PROPOSAL,
            broker_order_id=None, executed_price=None, executed_quantity=0, is_paper_trade=True,
            created_at=self._clock.now(), last_status_check=None, priority_rank=1,
        )
        try:
            self._wrapped_store.save_execution_record(record)
        except Exception:
            return
        # Found while investigating a genuine, intermittent audit-trail
        # gap: if the injected failure hits inside the VERY FIRST
        # process_order() call below (before any state transition
        # succeeds), NO event is ever emitted for this cycle_id -- events
        # only fire after a successful transition, and this record never
        # gets one. The record still ends up with a cycle_log entry
        # (appended unconditionally further down), making it genuinely
        # unreconstructable from the event stream despite "existing."
        # Emitting one event immediately after the one guaranteed-
        # successful step (the initial save above) closes this gap --
        # every cycle_id that ever gets a cycle_log entry is now
        # guaranteed at least one corresponding event, regardless of what
        # happens afterward.
        self._events.record(ExecutionEvent(
            event_type=ExecutionEventType.ORDER_PENDING, timestamp=self._clock.now(),
            broker_order_id=None, symbol=symbol, details={"stage": "proposal_created"},
            correlation_id=f"corr-{cycle_id}", cycle_id=cycle_id, component="ExtendedPaperTradingSession",
            result="proposal_created",
        ))

        for _ in range(8):
            try:
                record = self._orchestrator.process_order(record, correlation_id=f"corr-{cycle_id}")
            except Exception:
                break
            if record.order_status in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED, OrderLifecycleState.FAILED):
                break

        self.state.cycle_log.append(CycleLogEntry(
            execution_id=record.execution_id, cycle_id=cycle_id, symbol=symbol,
            quantity_proposed=record.quantity_proposed, final_status=record.order_status,
            broker_order_id=record.broker_order_id, executed_quantity=record.executed_quantity or 0,
            executed_price=record.executed_price, limit_price=record.limit_price,
            as_of_date=self._clock.now().date(), rejection_notes=tuple(record.notes or ()),
        ))
        if record.order_status in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED,
                                    OrderLifecycleState.FAILED, OrderLifecycleState.RECONCILED):
            self.state.outstanding_execution_ids.discard(record.execution_id)
        else:
            self.state.outstanding_execution_ids.add(record.execution_id)

    def sweep_outstanding(self, max_per_sweep=200):
        """Found and fixed via this milestone's own long-duration run:
        without this, any record not resolved within its own cycle's poll
        budget was permanently orphaned -- never revisited, growing
        open_orders_count without bound over a long operational run. Gives
        every currently-outstanding record one more process_order() pass;
        called periodically from run(), not just once at the end, so a
        record has many chances across many days, not a single retry."""
        resolved = []
        # Found while investigating a genuinely intermittent test failure:
        # iterating a set of execution_id strings without sorting first
        # meant processing order depended on Python's per-process hash
        # randomization (PYTHONHASHSEED, randomized by default since
        # Python 3.3) -- meaning two runs with the IDENTICAL seed were
        # NOT actually fully deterministic, since WHICH orders got swept
        # first (and therefore which random.Random(seed) draws they
        # consumed) varied run to run. Sorting first makes "same seed =
        # identical run" genuinely true, not just true for the top-level
        # random choices.
        for execution_id in sorted(self.state.outstanding_execution_ids)[:max_per_sweep]:
            try:
                record = self._wrapped_store.load_execution_record(execution_id)
            except Exception:
                continue
            if record is None:
                self.state.outstanding_execution_ids.discard(execution_id)
                continue
            try:
                record = self._orchestrator.process_order(record, correlation_id=f"sweep-{execution_id}")
            except Exception:
                continue
            if record.order_status in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED,
                                        OrderLifecycleState.FAILED, OrderLifecycleState.RECONCILED):
                resolved.append(execution_id)
        for execution_id in resolved:
            self.state.outstanding_execution_ids.discard(execution_id)
        return len(resolved)

    def run(self, num_days, cycles_per_day, rng, snapshot_every_n_days=5, purge_every_n_days=10):
        import tracemalloc
        from pathlib import Path

        tracemalloc.start()
        cumulative_events = 0

        for day in range(num_days):
            if rng.random() < self._restart_probability_per_day:
                self._restart()

            for c in range(cycles_per_day):
                self._process_one_cycle(day, c)

            self.sweep_outstanding()

            if day % self._reconcile_every_n_days == self._reconcile_every_n_days - 1:
                reconciler = ReconciliationService(self._wrapped_store, self._broker, self._clock, self._events)
                try:
                    outcomes = reconciler.reconcile(correlation_id=f"scheduled-{day}")
                    self.state.reconciliation_runs += 1
                    self.state.reconciliation_mismatches += sum(
                        1 for o in outcomes if o.discrepancy_type.value not in ("no_discrepancy", "still_open_at_broker")
                    )
                except Exception:
                    pass

            if day % snapshot_every_n_days == snapshot_every_n_days - 1:
                current, peak = tracemalloc.get_traced_memory()
                db_size = Path(self._db_path).stat().st_size if Path(self._db_path).exists() else 0
                cumulative_events += len(self._events.events())
                self.state.resource_snapshots.append(ResourceSnapshot(
                    day=day, timestamp=self._clock.now(), memory_kb=current / 1024, db_size_bytes=db_size,
                    cumulative_events=cumulative_events, open_orders_count=len(self._broker.get_open_orders()),
                ))
                if len(self.state.resource_snapshots) > MAX_RESOURCE_SNAPSHOTS_RETAINED:
                    # Keep only the most recent window -- analyze_all_trends()'s
                    # first-half/second-half comparison only needs recent
                    # history, and unlike cycle_log, nothing else in this
                    # module needs the full multi-year snapshot history
                    # retained, so a bounded rolling window (not a third
                    # archive class) is the correct, simplest fix.
                    self.state.resource_snapshots = self.state.resource_snapshots[-MAX_RESOURCE_SNAPSHOTS_RETAINED:]

            if day % purge_every_n_days == purge_every_n_days - 1:
                reconciler = ReconciliationService(self._wrapped_store, self._broker, self._clock, self._events)
                try:
                    reconciler.reconcile(correlation_id=f"pre-purge-{day}")
                    self.state.reconciliation_runs += 1
                    self._broker.purge_terminal_orders()
                except Exception:
                    pass
                if self._archive is not None:
                    self._archive.archive_and_clear(self._events)
                else:
                    self._events.clear()
                if self._cycle_log_archive is not None:
                    self._cycle_log_archive.archive_and_trim(self.state.cycle_log)

            self._clock.advance(timedelta(hours=24))

        tracemalloc.stop()

    def get_full_cycle_log(self):
        """Merges live (not-yet-archived) cycle_log entries with archived
        ones -- necessary because reports.py's generate_report() filters
        by date range, and a report requested for an OLDER period (one
        that's already been archived+trimmed) would silently see nothing
        if only self.state.cycle_log were consulted. Archived rows are
        reconstructed into CycleLogEntry instances (not left as raw
        dicts) so reports.py's attribute access works identically
        regardless of source."""
        entries = list(self.state.cycle_log)
        if self._cycle_log_archive is not None:
            for row in self._cycle_log_archive.read_all():
                entries.append(CycleLogEntry(
                    execution_id=row["execution_id"], cycle_id=row["cycle_id"], symbol=row["symbol"],
                    quantity_proposed=row["quantity_proposed"], final_status=OrderLifecycleState(row["final_status"]),
                    broker_order_id=row["broker_order_id"], executed_quantity=row["executed_quantity"],
                    executed_price=row["executed_price"], limit_price=row["limit_price"],
                    as_of_date=_date_type.fromisoformat(row["as_of_date"]), rejection_notes=tuple(row["rejection_notes"]),
                ))
        entries.sort(key=lambda e: e.as_of_date)
        return entries

    def close(self):
        self._store.close()
