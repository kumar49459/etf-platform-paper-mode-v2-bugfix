"""SubmissionOrchestrator (Milestone 3, requirement 2). Orchestration
ONLY: coordinates VerificationService and BrokerPort, persists checkpoints,
manages retries, coordinates notifications. No trading logic, no
allocation logic, no decision making -- every actual decision (quantity,
price, symbol) already happened in Strategy Engine or VerificationService;
this class only sequences calls and persists state around them.

DESIGN: process_order() is a single-step, idempotent, resumable function --
inspect the record's CURRENT persisted state, perform exactly the next
step, persist, return. This is what makes every one of the 8 requested
crash checkpoints testable: "crashing at checkpoint N" is simulated by
simply not calling process_order() again past that point, and "recovery"
is calling it again on a fresh orchestrator/store pair pointed at the same
database file. Same pattern Strategy Engine's crash-safety used (Phase 6).

Checkpoint mapping (the 8 requested points):
  before verification    -> record at PROPOSAL, process_order() not yet called
  after verification /
  before persistence      -> UNOBSERVABLE as a distinct state: VerificationService
                             is pure (no side effect), so a crash here is
                             indistinguishable from "before verification" --
                             both simply re-run verification from scratch on
                             retry, which is safe since it's idempotent.
  after persistence /
  before broker submission -> record at VERIFIED, persisted, submission not yet attempted
  after broker submission /
  before persisting PENDING -> THE critical real checkpoint: broker may have
                             accepted the order even if the process crashes
                             before recording that fact locally -- exactly
                             what mandatory reconciliation resolves.
  before notification /
  after notification       -> best-effort, not order-state-critical
"""

from __future__ import annotations

import uuid

from etf_platform.execution_manager.events import ExecutionEvent, ExecutionEventType
from etf_platform.execution_manager.models import OrderLifecycleState
from etf_platform.execution_manager.verification import VerificationService


class SubmissionOrchestrator:
    def __init__(
        self, store, broker_port, live_quote_provider, compliance_port, notification_port,
        clock, event_recorder, verification_service=None, component_name="SubmissionOrchestrator",
    ):
        self._store = store
        self._broker = broker_port
        self._quotes = live_quote_provider
        self._compliance = compliance_port
        self._notifications = notification_port
        self._clock = clock
        self._events = event_recorder
        self._verification = verification_service or VerificationService()
        self._component = component_name

    def process_order(self, execution_record, correlation_id=None):
        correlation_id = correlation_id or f"corr-{uuid.uuid4().hex[:12]}"
        state = execution_record.order_status

        if state == OrderLifecycleState.PROPOSAL:
            return self._do_verify(execution_record, correlation_id)
        if state == OrderLifecycleState.VERIFIED:
            return self._do_submit(execution_record, correlation_id)
        if state == OrderLifecycleState.SUBMITTED:
            # Found during Milestone 3's own crash-recovery testing: SUBMITTED
            # means "we persisted intent to submit" -- broker_order_id may
            # still be None if the process crashed before the broker's
            # response was recorded. process_order() must NOT attempt to poll
            # here (get_order_status(None) would be meaningless); only
            # ReconciliationService knows how to safely resolve this state
            # (by matching client_reference against the broker's open orders,
            # or confirming it's safe to retry) -- see reconciliation.py.
            # process_order() is deliberately a no-op for this state.
            return execution_record
        if state == OrderLifecycleState.AMBIGUOUS:
            # DDR-001: terminal for automated processing. process_order()
            # must never attempt anything with an AMBIGUOUS record -- not
            # poll, not retry, not resolve it. The only way out is an
            # explicit, human-invoked call to
            # ReconciliationService.resolve_ambiguous_execution().
            return execution_record
        if state in (OrderLifecycleState.PENDING, OrderLifecycleState.PARTIALLY_FILLED):
            return self._do_poll(execution_record, correlation_id)
        if state in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED, OrderLifecycleState.FAILED):
            return self._do_notify(execution_record, correlation_id)
        return execution_record

    def _do_verify(self, record, correlation_id):
        result = self._verification.verify(
            record.symbol, record.quantity_proposed, record.limit_price,
            self._quotes, self._broker, self._compliance,
        )
        if not result.approved:
            record.transition_to(OrderLifecycleState.FAILED)
            record.notes = record.notes + result.notes
            self._store.save_execution_record(record)
            self._emit(ExecutionEventType.ORDER_REJECTED, record, correlation_id, "rejected",
                       {"reason": result.rejection_reason.value if result.rejection_reason else None})
            return record

        record.transition_to(OrderLifecycleState.VERIFIED)
        record.quantity_final = result.verified_quantity
        record.notes = record.notes + result.notes
        self._store.save_execution_record(record)
        self._emit(ExecutionEventType.ORDER_PENDING, record, correlation_id, "verified",
                   {"verified_quantity": result.verified_quantity})
        return record

    def _do_submit(self, record, correlation_id):
        quantity = record.quantity_final or record.quantity_proposed
        client_reference = record.cycle_id

        # Checkpoint: "before broker submission" -- persist SUBMITTED
        # (intent-to-submit) BEFORE the broker call, not after. This was a
        # real bug caught during this milestone's own smoke testing: the
        # first version of this method jumped straight from VERIFIED to
        # PENDING, skipping SUBMITTED entirely -- which meant the "persist
        # before external side effect" discipline documented in this
        # module's docstring wasn't actually implemented, only described.
        record.transition_to(OrderLifecycleState.SUBMITTED)
        self._store.save_execution_record(record)

        try:
            broker_order_id = self._broker.submit_order(
                record.symbol, "BUY", quantity, record.limit_price, client_reference,
            )
        except Exception as exc:
            # A failed/uncertain submission is NOT assumed to mean nothing
            # happened -- record stays at SUBMITTED (not FAILED), so
            # mandatory reconciliation (Milestone 1) is what resolves
            # whether the broker actually received it despite this
            # exception, rather than this method guessing.
            self._emit(ExecutionEventType.API_ERROR, record, correlation_id, "submission_failed", {"error": str(exc)})
            raise

        # Checkpoint: "after broker submission / before persisting PENDING"
        # -- the critical real checkpoint (see module docstring).
        record.broker_order_id = broker_order_id
        record.transition_to(OrderLifecycleState.PENDING)
        self._store.save_execution_record(record)
        self._emit(ExecutionEventType.ORDER_SUBMITTED, record, correlation_id, "submitted",
                   {"broker_order_id": broker_order_id, "quantity": quantity})
        return record

    def _do_poll(self, record, correlation_id):
        broker_order = self._broker.get_order_status(record.broker_order_id)
        if broker_order.state != record.order_status:
            record.transition_to(broker_order.state)
            record.executed_quantity = broker_order.executed_quantity
            record.executed_price = broker_order.executed_price
            record.last_status_check = self._clock.now()
            self._store.save_execution_record(record)
            self._emit(
                {
                    OrderLifecycleState.PARTIALLY_FILLED: ExecutionEventType.PARTIAL_FILL,
                    OrderLifecycleState.FILLED: ExecutionEventType.ORDER_FILLED,
                    OrderLifecycleState.CANCELLED: ExecutionEventType.ORDER_CANCELLED,
                }.get(broker_order.state, ExecutionEventType.ORDER_PENDING),
                record, correlation_id, broker_order.state.value,
                {"executed_quantity": broker_order.executed_quantity},
            )
        return record

    def _do_notify(self, record, correlation_id):
        if self._notifications is not None:
            self._notifications.send(
                f"Order {record.symbol} reached terminal state {record.order_status.value} "
                f"(execution_id={record.execution_id})."
            )
        return record

    def _emit(self, event_type, record, correlation_id, result, details):
        self._events.record(ExecutionEvent(
            event_type=event_type, timestamp=self._clock.now(), broker_order_id=record.broker_order_id,
            symbol=record.symbol, details=details, correlation_id=correlation_id, cycle_id=record.cycle_id,
            component=self._component, result=result,
        ))
