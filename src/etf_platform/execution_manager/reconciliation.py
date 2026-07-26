"""ReconciliationService (Milestone 3, requirement 3). The broker is
authoritative after submission (Decision 1, established since Milestone
1's mandatory-reconciliation design). Every mismatch between local state,
broker state, and persisted state is explicitly detected and classified
- corrections are applied (the broker's view always wins), but every
correction emits a classified event first. Nothing is ever silently
repaired.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from etf_platform.execution_manager.events import ExecutionEvent, ExecutionEventType
from etf_platform.execution_manager.exceptions import ExecutionManagerError
from etf_platform.execution_manager.models import OrderLifecycleState


class DiscrepancyType(Enum):
    NO_DISCREPANCY = "no_discrepancy"
    NEVER_REACHED_BROKER = "never_reached_broker"
    STILL_OPEN_AT_BROKER = "still_open_at_broker"
    STATE_MISMATCH = "state_mismatch"
    BROKER_HAS_NO_RECORD = "broker_has_no_record"
    AMBIGUOUS_NO_LOCAL_ID = "ambiguous_no_local_id"
    """DDR-001: a valid, expected operational outcome -- not an error.
    Reconciliation could not confirm either that the order reached the
    broker (no match found) or that it's safe to assume it didn't
    (get_open_orders() structurally cannot rule out "reached the broker
    and already resolved to a terminal state before this check ran").
    Escalated to AMBIGUOUS, never auto-retried."""


@dataclass(frozen=True)
class ReconciliationOutcome:
    execution_id: str
    broker_order_id: object
    discrepancy_type: DiscrepancyType
    local_state: OrderLifecycleState
    broker_state: object
    action_taken: str
    details: str = ""


class ReconciliationService:
    def __init__(self, store, broker_port, clock, event_recorder, notification_port=None, component_name="ReconciliationService"):
        self._store = store
        self._broker = broker_port
        self._clock = clock
        self._events = event_recorder
        self._notifications = notification_port
        """Optional, defaults to None (backward compatible with every
        existing caller in this codebase that constructs
        ReconciliationService without one -- Milestone 3/4/5B/Production
        Verification's callers all pass 4 positional args). When
        provided, used to generate the high-priority operational alert
        DDR-001's approved design requires for every AMBIGUOUS
        escalation. If not provided, the escalation still happens
        correctly (state transition, event emission) -- only the
        notification itself is skipped, which is a real, disclosed
        degradation for any caller that doesn't wire one up, not a
        silent one."""
        self._component = component_name

    def reconcile(self, correlation_id=None):
        unresolved = self._store.load_unresolved_records()
        # Found by actually running the stress harness (Milestone 3,
        # requirement 8): records still at PROPOSAL/VERIFIED haven't
        # attempted any broker interaction THIS cycle yet -- that's normal
        # mid-flight state, not a crash, and treating every one of them as
        # "never reached broker" (technically true, but not a discrepancy)
        # produced enormous noise and wasted reconciliation work that grew
        # with total history rather than with actual problems. Only
        # records that have progressed to or past a broker interaction
        # attempt are reconciliation's concern.
        #
        # AMBIGUOUS records (DDR-001) are also excluded from active
        # processing here -- they are terminal for automated processing by
        # design; re-running reconciliation logic against them would either
        # be a no-op or, worse, risk re-deriving the same unsafe guess this
        # policy exists to prevent. They still count toward unresolved_count
        # so an operator watching reconciliation's own summary can see how
        # many are outstanding, without reconciliation itself acting on them.
        relevant = [
            r for r in unresolved
            if r.order_status not in (
                OrderLifecycleState.PROPOSAL, OrderLifecycleState.VERIFIED, OrderLifecycleState.AMBIGUOUS,
            )
        ]
        ambiguous_count = sum(1 for r in unresolved if r.order_status == OrderLifecycleState.AMBIGUOUS)
        open_at_broker = {o.broker_order_id: o for o in self._broker.get_open_orders()}
        outcomes = []

        self._emit(ExecutionEventType.RECONCILIATION_CHECK, None, None, correlation_id, None,
                    {"unresolved_count": len(unresolved), "relevant_count": len(relevant),
                     "ambiguous_awaiting_operator": ambiguous_count}, result="started")

        for record in relevant:
            outcome = self._reconcile_one(record, open_at_broker, correlation_id)
            outcomes.append(outcome)

        self._emit(ExecutionEventType.RECONCILIATION_CHECK, None, None, correlation_id, None,
                    {"outcomes": len(outcomes), "ambiguous_awaiting_operator": ambiguous_count}, result="completed")
        return outcomes

    def _reconcile_one(self, record, open_at_broker, correlation_id):
        _TERMINAL = (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED, OrderLifecycleState.FAILED)

        if record.order_status == OrderLifecycleState.FAILED and record.broker_order_id is None:
            # Rejected at verification -- never attempted broker
            # interaction at all, nothing to check. Confirm directly
            # rather than searching for a match that could never exist.
            record.order_status = OrderLifecycleState.RECONCILED
            self._store.save_execution_record(record)
            return ReconciliationOutcome(
                execution_id=record.execution_id, broker_order_id=None,
                discrepancy_type=DiscrepancyType.NO_DISCREPANCY, local_state=record.order_status,
                broker_state=None, action_taken="confirmed_no_broker_interaction_was_ever_needed",
            )
        if record.broker_order_id is None:
            # Found during Milestone 3's own crash-recovery testing: a
            # record can reach SUBMITTED (persisted) with no
            # broker_order_id (crashed before the broker's response was
            # recorded) -- there's no ID to look up directly, so match by
            # client_reference (the cycle_id we passed to submit_order())
            # against the broker's own open-orders list instead.
            matched = next(
                (o for o in open_at_broker.values()
                 if getattr(o, "client_reference", None) == record.cycle_id and o.symbol == record.symbol),
                None,
            )
            if matched is not None:
                self._emit_mismatch(record, DiscrepancyType.STATE_MISMATCH, correlation_id,
                                     f"Found a matching open order at the broker by client_reference "
                                     f"({record.cycle_id}) despite no local broker_order_id -- adopting it.")
                record.broker_order_id = matched.broker_order_id
                record.order_status = matched.state
                record.executed_quantity = matched.executed_quantity
                record.executed_price = matched.executed_price
                record.last_status_check = self._clock.now()
                self._store.save_execution_record(record)
                return ReconciliationOutcome(
                    execution_id=record.execution_id, broker_order_id=matched.broker_order_id,
                    discrepancy_type=DiscrepancyType.STATE_MISMATCH, local_state=record.order_status,
                    broker_state=matched.state, action_taken="adopted_broker_order_id_via_client_reference_match",
                )
            # Genuinely not found in the broker's OPEN orders. Per DDR-001:
            # this does NOT mean "confirmed never reached the broker" --
            # get_open_orders() structurally excludes orders that reached
            # the broker and already resolved to a terminal state before
            # this check ran (proven by direct test against PaperBrokerPort,
            # see DDR-001's root-cause section). The old behavior here
            # (revert to VERIFIED for automatic retry) was an unsafe guess,
            # not a confirmed-safe conclusion, and risked a genuine
            # duplicate submission. Escalate to AMBIGUOUS instead -- a
            # valid, expected operational outcome, not an error -- and
            # require explicit operator review before any further action.
            self._emit_mismatch(record, DiscrepancyType.AMBIGUOUS_NO_LOCAL_ID, correlation_id,
                                 "No broker_order_id locally and no matching open order found by "
                                 "client_reference -- CANNOT confirm this order never reached the broker "
                                 "(it may have already filled/been rejected/been cancelled before this "
                                 "check ran). Escalating to AMBIGUOUS. Requires explicit operator review.")
            if record.order_status == OrderLifecycleState.SUBMITTED:
                record.order_status = OrderLifecycleState.AMBIGUOUS
                self._store.save_execution_record(record)
            self._alert_ambiguous(record, correlation_id)
            return ReconciliationOutcome(
                execution_id=record.execution_id, broker_order_id=None,
                discrepancy_type=DiscrepancyType.AMBIGUOUS_NO_LOCAL_ID, local_state=record.order_status,
                broker_state=None, action_taken="escalated_to_ambiguous_awaiting_operator_review",
            )

        if record.broker_order_id in open_at_broker:
            return ReconciliationOutcome(
                execution_id=record.execution_id, broker_order_id=record.broker_order_id,
                discrepancy_type=DiscrepancyType.STILL_OPEN_AT_BROKER, local_state=record.order_status,
                broker_state=open_at_broker[record.broker_order_id].state, action_taken="none_still_in_flight",
            )

        try:
            broker_order = self._broker.get_order_status(record.broker_order_id)
        except ExecutionManagerError:
            self._emit_mismatch(record, DiscrepancyType.BROKER_HAS_NO_RECORD, correlation_id,
                                 "Broker has no record of this broker_order_id at all -- flagged for manual review.")
            return ReconciliationOutcome(
                execution_id=record.execution_id, broker_order_id=record.broker_order_id,
                discrepancy_type=DiscrepancyType.BROKER_HAS_NO_RECORD, local_state=record.order_status,
                broker_state=None, action_taken="flagged_for_manual_review",
                details="Broker has no record of a broker_order_id we hold locally -- do not guess.",
            )

        if broker_order.state == record.order_status:
            if broker_order.state in _TERMINAL:
                # Confirmed terminal AND broker agrees -- this is exactly
                # what RECONCILED means (PHASE7_Objectives.md section 4):
                # advance it so it never needs checking again. Without
                # this, every historical order would be re-checked on
                # every single future restart forever -- found by actually
                # running the stress harness at scale, not by reasoning
                # about the design in the abstract.
                record.order_status = OrderLifecycleState.RECONCILED
                self._store.save_execution_record(record)
                return ReconciliationOutcome(
                    execution_id=record.execution_id, broker_order_id=record.broker_order_id,
                    discrepancy_type=DiscrepancyType.NO_DISCREPANCY, local_state=record.order_status,
                    broker_state=broker_order.state, action_taken="confirmed_and_reconciled",
                )
            return ReconciliationOutcome(
                execution_id=record.execution_id, broker_order_id=record.broker_order_id,
                discrepancy_type=DiscrepancyType.NO_DISCREPANCY, local_state=record.order_status,
                broker_state=broker_order.state, action_taken="none",
            )

        self._emit_mismatch(record, DiscrepancyType.STATE_MISMATCH, correlation_id,
                             f"Local={record.order_status.value}, broker={broker_order.state.value} -- adopting broker's state.")
        record.quantity_final = broker_order.executed_quantity or record.quantity_final
        record.executed_price = broker_order.executed_price
        record.executed_quantity = broker_order.executed_quantity
        adopted_state = broker_order.state
        record.order_status = OrderLifecycleState.RECONCILED if adopted_state in _TERMINAL else adopted_state
        record.last_status_check = self._clock.now()
        self._store.save_execution_record(record)
        return ReconciliationOutcome(
            execution_id=record.execution_id, broker_order_id=record.broker_order_id,
            discrepancy_type=DiscrepancyType.STATE_MISMATCH, local_state=record.order_status,
            broker_state=broker_order.state, action_taken="adopted_broker_state",
        )

    def _alert_ambiguous(self, record, correlation_id):
        """DDR-001 requirement: generate a high-priority operational alert
        for every AMBIGUOUS escalation. Uses the existing NotificationPort
        (no new interface), prefixed distinctly so it's unmistakable in
        any notification stream, and never silently skipped -- if no
        notification_port was provided to this service, that gap is
        disclosed in the constructor's own docstring, not hidden here."""
        if self._notifications is None:
            return
        self._notifications.send(
            f"[HIGH PRIORITY - OPERATOR REVIEW REQUIRED] Execution {record.execution_id} "
            f"(cycle_id={record.cycle_id}, symbol={record.symbol}) is AMBIGUOUS: reconciliation could not "
            f"confirm whether this order reached the broker. Automatic retry has been suppressed. "
            f"See the ambiguous-execution report for full detail. This requires explicit operator review "
            f"before any further action -- do not resubmit without confirming the broker's actual state."
        )

    def resolve_ambiguous_execution(self, execution_id, confirmed_state, operator_notes, broker_order_id=None,
                                     executed_quantity=None, executed_price=None):
        """The ONLY way an AMBIGUOUS record ever leaves that state -- an
        explicit, human-invoked action, never called by reconcile() or any
        automatic/scheduled path. `confirmed_state` must be one of the
        states ORDER_LIFECYCLE_TRANSITIONS[AMBIGUOUS] allows (validated by
        the underlying transition_to() call, which raises
        InvalidLifecycleTransitionError for anything else) -- whatever the
        operator actually confirmed by checking the broker directly
        (dashboard, contract notes, support contact), not a guess.
        `operator_notes` is mandatory and is persisted into the record's
        notes for permanent audit trail -- an ambiguous execution's
        resolution must always be explainable after the fact, not just
        the resolution itself."""
        record = self._store.load_execution_record(execution_id)
        if record is None:
            raise ExecutionManagerError(f"No execution record found for execution_id={execution_id!r}")
        if record.order_status != OrderLifecycleState.AMBIGUOUS:
            raise ExecutionManagerError(
                f"resolve_ambiguous_execution() called on execution_id={execution_id!r}, but its current "
                f"state is {record.order_status.value!r}, not AMBIGUOUS -- refusing to act on a record "
                f"that isn't actually awaiting this specific resolution."
            )
        if not operator_notes or not operator_notes.strip():
            raise ExecutionManagerError("operator_notes is mandatory for resolving an AMBIGUOUS execution -- "
                                         "the resolution must be explainable in the permanent audit trail.")

        record.transition_to(confirmed_state)  # raises InvalidLifecycleTransitionError for a disallowed target
        if broker_order_id is not None:
            record.broker_order_id = broker_order_id
        if executed_quantity is not None:
            record.executed_quantity = executed_quantity
        if executed_price is not None:
            record.executed_price = executed_price
        record.notes = record.notes + (f"[OPERATOR RESOLUTION] {operator_notes}",)
        record.last_status_check = self._clock.now()
        self._store.save_execution_record(record)
        self._emit(
            ExecutionEventType.RECONCILIATION_CHECK, record.broker_order_id, record.symbol, None,
            record.cycle_id, {"resolved_to": confirmed_state.value, "operator_notes": operator_notes},
            result="operator_resolved_ambiguous",
        )
        return record

    def _emit_mismatch(self, record, discrepancy_type, correlation_id, details):
        self._emit(
            ExecutionEventType.RECONCILIATION_CHECK, record.broker_order_id, record.symbol, correlation_id,
            record.cycle_id, {"discrepancy_type": discrepancy_type.value, "details": details}, result="mismatch",
        )

    def _emit(self, event_type, broker_order_id, symbol, correlation_id, cycle_id, details, result):
        self._events.record(ExecutionEvent(
            event_type=event_type, timestamp=self._clock.now(), broker_order_id=broker_order_id, symbol=symbol,
            details=details, correlation_id=correlation_id, cycle_id=cycle_id, component=self._component,
            result=result,
        ))
