"""PaperBrokerPort (Milestone 2's core deliverable) -- a deterministic,
scenario-driven simulator satisfying the exact same BrokerPort interface
KiteLiveBrokerPort will later satisfy (PHASE7_Objectives.md section 3).
Every action is driven by an explicit BrokerScenario (scenarios.py), a
controllable Clock (clock.py), and emits structured ExecutionEvents
(events.py) -- nothing here depends on real wall-clock time or
unseeded randomness.

Internal order tracking uses OrderLifecycleState + validate_transition
(models.py) directly, exactly as ExecutionRecord does -- the simulator
itself is structurally incapable of producing an invalid lifecycle
transition, not merely tested to avoid one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from etf_platform.execution_manager.exceptions import BrokerCommunicationError, ExecutionManagerError
from etf_platform.execution_manager.events import ExecutionEvent, ExecutionEventType
from etf_platform.execution_manager.models import OrderLifecycleState, validate_transition
from etf_platform.execution_manager.ports import BrokerPort
from etf_platform.execution_manager.scenarios import BrokerScenario


class OrderRejectedError(ExecutionManagerError):
    """Raised by PaperBrokerPort.submit_order() for the ORDER_REJECTED
    scenario -- mirrors a real broker's synchronous validation rejection
    (no broker_order_id is ever assigned)."""


@dataclass
class PaperOrder:
    broker_order_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: float
    client_reference: str
    scenario: BrokerScenario
    state: OrderLifecycleState
    submitted_at: object
    poll_count: int = 0
    executed_quantity: int = 0
    executed_price: float = None

    def transition_to(self, new_state, clock, event_recorder, event_type, details=None):
        validate_transition(self.state, new_state)
        self.state = new_state
        event_recorder.record(ExecutionEvent(
            event_type=event_type, timestamp=clock.now(), broker_order_id=self.broker_order_id,
            symbol=self.symbol, details=details or {},
        ))


class PaperBrokerPort(BrokerPort):
    """THREAD-SAFETY BOUNDARY, found and disclosed during this milestone's
    adversarial review: this class has no internal locking around
    self._orders / self._order_parameters. Concurrent calls from different
    threads working on DIFFERENT orders are safe under CPython's GIL (each
    dict operation is individually atomic), but self._orders[x].poll_count
    += 1 and similar multi-step updates are NOT atomic across threads if
    two threads ever operate on the SAME order concurrently. This is an
    accepted, disclosed limitation, not an oversight: Module 28's actual
    invocation model is single-threaded, short-lived processes with
    cycle-level mutual exclusion already enforced by ExecutionStateStore's
    claim_cycle() (Milestone 1) -- the scenario this class would need
    internal locking for (two threads racing on one order) shouldn't arise
    in the architecture as designed. If PaperBrokerPort is ever reused in
    a genuinely multi-threaded context, that would need its own review at
    that time, not a speculative lock added now for a case that can't
    currently occur.
    """

    def __init__(self, clock, event_recorder, scenario_provider, starting_cash=1_000_000.0):
        self._clock = clock
        self._events = event_recorder
        self._scenarios = scenario_provider
        self._cash = starting_cash
        self._orders: dict[str, PaperOrder] = {}
        self._order_parameters: dict[str, object] = {}
        # Parameters are resolved ONCE at submission time and stored --
        # re-querying scenario_for() on every poll would break determinism
        # for SequentialScenarioProvider (each call advances its index),
        # so a poll must reuse the parameters already assigned, never
        # re-derive them.

    def submit_order(self, symbol, side, quantity, limit_price, client_reference):
        broker_order_id = f"paper-{uuid.uuid4().hex[:12]}"
        self._events.record(ExecutionEvent(
            event_type=ExecutionEventType.ORDER_SUBMITTED, timestamp=self._clock.now(),
            broker_order_id=broker_order_id, symbol=symbol,
            details={"quantity": quantity, "limit_price": limit_price, "client_reference": client_reference},
        ))

        scenario, parameters = self._scenarios.scenario_for(symbol, client_reference)

        if scenario == BrokerScenario.ORDER_REJECTED:
            self._events.record(ExecutionEvent(
                event_type=ExecutionEventType.ORDER_REJECTED, timestamp=self._clock.now(),
                broker_order_id=broker_order_id, symbol=symbol, details={"reason": parameters.rejection_reason},
            ))
            raise OrderRejectedError(parameters.rejection_reason)

        if scenario == BrokerScenario.NETWORK_TIMEOUT:
            self._events.record(ExecutionEvent(
                event_type=ExecutionEventType.API_TIMEOUT, timestamp=self._clock.now(),
                broker_order_id=None, symbol=symbol, details={"stage": "submit_order"},
            ))
            raise BrokerCommunicationError(f"Simulated network timeout submitting order for {symbol}.")

        if scenario == BrokerScenario.API_ERROR:
            self._events.record(ExecutionEvent(
                event_type=ExecutionEventType.API_ERROR, timestamp=self._clock.now(),
                broker_order_id=None, symbol=symbol, details={"stage": "submit_order"},
            ))
            raise BrokerCommunicationError(f"Simulated API error submitting order for {symbol}.")

        if scenario == BrokerScenario.CONNECTION_LOST:
            self._events.record(ExecutionEvent(
                event_type=ExecutionEventType.NETWORK_ERROR, timestamp=self._clock.now(),
                broker_order_id=None, symbol=symbol, details={"stage": "submit_order"},
            ))
            raise BrokerCommunicationError(f"Simulated connection loss submitting order for {symbol}.")

        if scenario == BrokerScenario.QUOTE_UNAVAILABLE:
            # Found via the long-duration simulation (recommendation 6):
            # this scenario was silently unhandled here, meaning an order
            # assigned it fell through every branch and got tracked as a
            # normal PENDING order that get_order_status() then ALSO never
            # resolved -- permanently stuck, no exception, no progress, a
            # genuine unbounded-accumulation bug, not the memory-shape red
            # herring it first looked like. Semantically: a real order
            # cannot be safely priced/submitted without current quote
            # data, so this is treated the same as the other
            # communication-style failures.
            self._events.record(ExecutionEvent(
                event_type=ExecutionEventType.QUOTE_UNAVAILABLE, timestamp=self._clock.now(),
                broker_order_id=None, symbol=symbol, details={"stage": "submit_order"},
            ))
            raise BrokerCommunicationError(f"Simulated quote unavailability submitting order for {symbol}.")

        order = PaperOrder(
            broker_order_id=broker_order_id, symbol=symbol, side=side, quantity=quantity,
            limit_price=limit_price, client_reference=client_reference, scenario=scenario,
            state=OrderLifecycleState.PENDING, submitted_at=self._clock.now(),
        )
        self._orders[broker_order_id] = order
        self._order_parameters[broker_order_id] = parameters
        self._events.record(ExecutionEvent(
            event_type=ExecutionEventType.ORDER_PENDING, timestamp=self._clock.now(),
            broker_order_id=broker_order_id, symbol=symbol, details={},
        ))
        return broker_order_id

    def get_order_status(self, broker_order_id):
        order = self._orders.get(broker_order_id)
        if order is None:
            raise ExecutionManagerError(f"PaperBrokerPort has no record of broker_order_id={broker_order_id!r}")

        order.poll_count += 1
        scenario = order.scenario
        parameters = self._order_parameters[broker_order_id]

        if order.state in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED):
            return order

        if scenario == BrokerScenario.IMMEDIATE_FILL:
            self._fill(order, order.quantity)

        elif scenario == BrokerScenario.PARTIAL_FILL:
            if order.state == OrderLifecycleState.PENDING:
                partial_qty = max(1, int(order.quantity * parameters.partial_fill_ratio))
                self._partial_fill(order, partial_qty)
            else:
                remaining = order.quantity - order.executed_quantity
                self._fill(order, remaining)

        elif scenario == BrokerScenario.DELAYED_FILL:
            if order.poll_count > parameters.delayed_fill_poll_count:
                self._fill(order, order.quantity)

        elif scenario == BrokerScenario.ORDER_CANCELLED:
            order.transition_to(
                OrderLifecycleState.CANCELLED, self._clock, self._events, ExecutionEventType.ORDER_CANCELLED,
                {"reason": "Simulated broker-side cancellation (e.g. risk system or exchange-initiated)."},
            )

        elif scenario == BrokerScenario.ORDER_EXPIRED:
            order.transition_to(
                OrderLifecycleState.CANCELLED, self._clock, self._events, ExecutionEventType.ORDER_EXPIRED,
                {"reason": "Simulated execution-window expiry, unfilled."},
            )

        elif scenario in (BrokerScenario.NETWORK_TIMEOUT, BrokerScenario.API_ERROR, BrokerScenario.CONNECTION_LOST):
            event_type = {
                BrokerScenario.NETWORK_TIMEOUT: ExecutionEventType.API_TIMEOUT,
                BrokerScenario.API_ERROR: ExecutionEventType.API_ERROR,
                BrokerScenario.CONNECTION_LOST: ExecutionEventType.NETWORK_ERROR,
            }[scenario]
            self._events.record(ExecutionEvent(
                event_type=event_type, timestamp=self._clock.now(), broker_order_id=broker_order_id,
                symbol=order.symbol, details={"stage": "get_order_status"},
            ))
            raise BrokerCommunicationError(f"Simulated {scenario.value} polling status for {broker_order_id}.")

        else:
            # Found via the long-duration simulation: QUOTE_UNAVAILABLE was
            # silently unhandled here, and an order stuck in this branch
            # made no progress and raised nothing -- a permanent, silent
            # hang, not a crash. This catch-all makes that SHAPE of bug
            # structurally impossible for any future scenario too: an
            # order that reaches here with a scenario this method doesn't
            # explicitly know how to resolve fails loudly, immediately,
            # rather than silently accumulating forever. Every scenario in
            # BrokerScenario must have an explicit branch above; reaching
            # this line means one doesn't, which is a defect in this
            # method, not a legitimate runtime outcome.
            raise ExecutionManagerError(
                f"PaperBrokerPort.get_order_status() has no explicit handling for scenario "
                f"{scenario.value!r} (order {broker_order_id}). This is a defect in PaperBrokerPort "
                "itself -- every BrokerScenario value must be explicitly handled here, since an order "
                "reaching this branch would otherwise silently never resolve."
            )

        return order

    def _fill(self, order, quantity):
        # Found while preparing the Milestone 3 stress test: cash was
        # never actually deducted on fill, which would have made the
        # "no negative cash" property test vacuously true (starting_cash
        # never changes, so it can never go negative) rather than a real
        # check. A real broker's available-cash figure decreases as fills
        # consume it -- simulating that here.
        self._cash -= quantity * order.limit_price
        order.executed_quantity += quantity
        order.executed_price = order.limit_price
        order.transition_to(
            OrderLifecycleState.FILLED, self._clock, self._events, ExecutionEventType.ORDER_FILLED,
            {"executed_quantity": order.executed_quantity, "executed_price": order.executed_price},
        )

    def _partial_fill(self, order, quantity):
        self._cash -= quantity * order.limit_price
        order.executed_quantity += quantity
        order.executed_price = order.limit_price
        order.transition_to(
            OrderLifecycleState.PARTIALLY_FILLED, self._clock, self._events, ExecutionEventType.PARTIAL_FILL,
            {"executed_quantity": order.executed_quantity, "remaining": order.quantity - order.executed_quantity},
        )

    def cancel_order(self, broker_order_id):
        order = self._orders.get(broker_order_id)
        if order is None:
            raise ExecutionManagerError(f"PaperBrokerPort has no record of broker_order_id={broker_order_id!r}")
        if order.state in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED):
            return
        order.transition_to(
            OrderLifecycleState.CANCELLED, self._clock, self._events, ExecutionEventType.ORDER_CANCELLED,
            {"reason": "Explicit cancellation requested."},
        )

    def get_open_orders(self):
        return [
            o for o in self._orders.values()
            if o.state not in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED)
        ]

    def get_available_cash(self):
        return self._cash

    def purge_terminal_orders(self, older_than=None, clock_now=None):
        """Found via long-duration simulation (Milestone 2, recommendation
        6): self._orders never shrinks on its own -- even FILLED/CANCELLED
        orders stay in memory for the life of the instance, unbounded.
        Over a short test run this is invisible (a few thousand small
        objects), but a real multi-month continuous paper-trading
        deployment (the exit criteria's 2-3 month requirement) would
        accumulate this indefinitely.

        Deliberately explicit and caller-invoked, never automatic --
        matching InMemoryEventRecorder.clear()'s philosophy: a caller
        might still want get_order_status() to answer for an old,
        already-terminal broker_order_id (e.g. during reconciliation), so
        silently purging on a timer would risk breaking that. `older_than`
        (a timedelta) plus `clock_now` (the caller's current clock reading)
        lets a long-running caller purge orders terminal for longer than
        some retention window; omitting both purges every terminal order
        immediately. Returns the number of orders purged."""
        terminal_states = (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED)
        to_remove = []
        for broker_order_id, order in self._orders.items():
            if order.state not in terminal_states:
                continue
            if older_than is not None and clock_now is not None:
                age = clock_now - order.submitted_at
                if age < older_than:
                    continue
            to_remove.append(broker_order_id)
        for broker_order_id in to_remove:
            del self._orders[broker_order_id]
            del self._order_parameters[broker_order_id]
        return len(to_remove)
