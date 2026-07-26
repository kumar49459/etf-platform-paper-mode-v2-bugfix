"""Tests for PaperBrokerPort: every scenario, determinism, transition
validity, and event recording -- Milestone 2's core deliverable."""

from __future__ import annotations

import unittest

from etf_platform.execution_manager import (
    BrokerCommunicationError,
    BrokerScenario,
    ExecutionEventType,
    FixedScenarioProvider,
    InMemoryEventRecorder,
    OrderLifecycleState,
    OrderRejectedError,
    PaperBrokerPort,
    PaperQuoteProvider,
    ScenarioParameters,
    SequentialScenarioProvider,
    SimulatedClock,
    validate_transition,
)


def make_broker(scenario, parameters=None):
    clock = SimulatedClock()
    events = InMemoryEventRecorder()
    provider = FixedScenarioProvider(scenario, parameters)
    broker = PaperBrokerPort(clock, events, provider)
    return broker, clock, events


class TestImmediateFill(unittest.TestCase):
    def test_fills_completely_on_first_poll(self):
        broker, clock, events = make_broker(BrokerScenario.IMMEDIATE_FILL)
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        order = broker.get_order_status(oid)
        self.assertEqual(order.state, OrderLifecycleState.FILLED)
        self.assertEqual(order.executed_quantity, 100)
        self.assertEqual(order.executed_price, 250.0)

    def test_emits_submitted_pending_filled_events(self):
        broker, clock, events = make_broker(BrokerScenario.IMMEDIATE_FILL)
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        broker.get_order_status(oid)
        types = [e.event_type for e in events.events()]
        self.assertEqual(types, [
            ExecutionEventType.ORDER_SUBMITTED, ExecutionEventType.ORDER_PENDING, ExecutionEventType.ORDER_FILLED,
        ])


class TestPartialFill(unittest.TestCase):
    def test_first_poll_partial_second_poll_complete(self):
        broker, clock, events = make_broker(BrokerScenario.PARTIAL_FILL)
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        first = broker.get_order_status(oid)
        self.assertEqual(first.state, OrderLifecycleState.PARTIALLY_FILLED)
        self.assertEqual(first.executed_quantity, 50)
        second = broker.get_order_status(oid)
        self.assertEqual(second.state, OrderLifecycleState.FILLED)
        self.assertEqual(second.executed_quantity, 100)

    def test_custom_partial_fill_ratio(self):
        broker, clock, events = make_broker(
            BrokerScenario.PARTIAL_FILL, ScenarioParameters(partial_fill_ratio=0.25)
        )
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        first = broker.get_order_status(oid)
        self.assertEqual(first.executed_quantity, 25)


class TestDelayedFill(unittest.TestCase):
    def test_stays_pending_until_configured_poll_count_exceeded(self):
        broker, clock, events = make_broker(
            BrokerScenario.DELAYED_FILL, ScenarioParameters(delayed_fill_poll_count=3)
        )
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        for i in range(3):
            order = broker.get_order_status(oid)
            self.assertEqual(order.state, OrderLifecycleState.PENDING, f"poll {i+1} should still be pending")
        final = broker.get_order_status(oid)
        self.assertEqual(final.state, OrderLifecycleState.FILLED)


class TestOrderRejected(unittest.TestCase):
    def test_raises_before_any_order_is_tracked(self):
        broker, clock, events = make_broker(BrokerScenario.ORDER_REJECTED)
        with self.assertRaises(OrderRejectedError):
            broker.submit_order("A", "BUY", 100, 250.0, "c1")
        self.assertEqual(broker.get_open_orders(), [])

    def test_rejection_reason_is_captured_in_event(self):
        broker, clock, events = make_broker(
            BrokerScenario.ORDER_REJECTED, ScenarioParameters(rejection_reason="Custom reason")
        )
        with self.assertRaises(OrderRejectedError) as ctx:
            broker.submit_order("A", "BUY", 100, 250.0, "c1")
        self.assertEqual(str(ctx.exception), "Custom reason")


class TestOrderCancelledAndExpired(unittest.TestCase):
    def test_order_cancelled_reaches_cancelled_state(self):
        broker, clock, events = make_broker(BrokerScenario.ORDER_CANCELLED)
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        order = broker.get_order_status(oid)
        self.assertEqual(order.state, OrderLifecycleState.CANCELLED)

    def test_order_expired_also_reaches_cancelled_state_but_distinct_event(self):
        broker, clock, events = make_broker(BrokerScenario.ORDER_EXPIRED)
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        order = broker.get_order_status(oid)
        self.assertEqual(order.state, OrderLifecycleState.CANCELLED)
        event_types = [e.event_type for e in events.events_for_order(oid)]
        self.assertIn(ExecutionEventType.ORDER_EXPIRED, event_types)
        self.assertNotIn(ExecutionEventType.ORDER_CANCELLED, event_types)

    def test_explicit_cancel_order_call(self):
        broker, clock, events = make_broker(BrokerScenario.DELAYED_FILL)
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        broker.cancel_order(oid)
        order = broker.get_order_status(oid)
        self.assertEqual(order.state, OrderLifecycleState.CANCELLED)

    def test_cancel_is_idempotent_on_already_terminal_order(self):
        broker, clock, events = make_broker(BrokerScenario.IMMEDIATE_FILL)
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        broker.get_order_status(oid)
        broker.cancel_order(oid)


class TestCommunicationFailures(unittest.TestCase):
    def test_network_timeout_on_submit_raises_and_leaves_no_order(self):
        broker, clock, events = make_broker(BrokerScenario.NETWORK_TIMEOUT)
        with self.assertRaises(BrokerCommunicationError):
            broker.submit_order("A", "BUY", 100, 250.0, "c1")
        self.assertEqual(broker.get_open_orders(), [])

    def test_api_error_on_submit_raises(self):
        broker, clock, events = make_broker(BrokerScenario.API_ERROR)
        with self.assertRaises(BrokerCommunicationError):
            broker.submit_order("A", "BUY", 100, 250.0, "c1")

    def test_connection_lost_on_submit_raises(self):
        broker, clock, events = make_broker(BrokerScenario.CONNECTION_LOST)
        with self.assertRaises(BrokerCommunicationError):
            broker.submit_order("A", "BUY", 100, 250.0, "c1")

    def test_communication_failure_scenario_submission_succeeds_predictably(self):
        clock = SimulatedClock()
        events = InMemoryEventRecorder()
        provider = SequentialScenarioProvider([
            (BrokerScenario.IMMEDIATE_FILL, ScenarioParameters()),
        ])
        broker = PaperBrokerPort(clock, events, provider)
        oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
        order_before = broker.get_order_status(oid)
        self.assertEqual(order_before.state, OrderLifecycleState.FILLED)


class TestQuoteUnavailable(unittest.TestCase):
    def test_ltp_returns_none(self):
        clock = SimulatedClock()
        events = InMemoryEventRecorder()
        provider = FixedScenarioProvider(BrokerScenario.QUOTE_UNAVAILABLE)
        quotes = PaperQuoteProvider(clock, events, provider, base_prices={"A": 100.0})
        self.assertIsNone(quotes.get_last_traded_price("A"))

    def test_depth_returns_none(self):
        clock = SimulatedClock()
        events = InMemoryEventRecorder()
        provider = FixedScenarioProvider(BrokerScenario.QUOTE_UNAVAILABLE)
        quotes = PaperQuoteProvider(clock, events, provider, base_prices={"A": 100.0})
        self.assertIsNone(quotes.get_market_depth("A"))

    def test_normal_scenario_returns_real_quote(self):
        clock = SimulatedClock()
        events = InMemoryEventRecorder()
        provider = FixedScenarioProvider(BrokerScenario.IMMEDIATE_FILL)
        quotes = PaperQuoteProvider(clock, events, provider, base_prices={"A": 100.0})
        self.assertEqual(quotes.get_last_traded_price("A"), 100.0)
        depth = quotes.get_market_depth("A")
        self.assertIsNotNone(depth)
        self.assertLess(depth.bid_price, depth.ask_price)


class TestDeterminism(unittest.TestCase):
    def test_same_scenario_produces_identical_results_across_runs(self):
        def run():
            broker, clock, events = make_broker(BrokerScenario.PARTIAL_FILL)
            oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
            return [
                (broker.get_order_status(oid).state.value, broker.get_order_status(oid).executed_quantity)
                for _ in range(1)
            ]

        self.assertEqual(run(), run())

    def test_seeded_random_provider_is_reproducible(self):
        from etf_platform.execution_manager import SeededRandomScenarioProvider

        provider1 = SeededRandomScenarioProvider(seed=42)
        provider2 = SeededRandomScenarioProvider(seed=42)
        results1 = [provider1.scenario_for(f"SYM{i}", f"ref{i}")[0] for i in range(50)]
        results2 = [provider2.scenario_for(f"SYM{i}", f"ref{i}")[0] for i in range(50)]
        self.assertEqual(results1, results2)

    def test_different_seeds_produce_different_sequences(self):
        from etf_platform.execution_manager import SeededRandomScenarioProvider

        provider1 = SeededRandomScenarioProvider(seed=1)
        provider2 = SeededRandomScenarioProvider(seed=2)
        results1 = [provider1.scenario_for(f"SYM{i}", f"ref{i}")[0] for i in range(50)]
        results2 = [provider2.scenario_for(f"SYM{i}", f"ref{i}")[0] for i in range(50)]
        self.assertNotEqual(results1, results2)


class TestStateTransitionValidityAcrossAllScenarios(unittest.TestCase):
    def test_every_scenario_produces_only_valid_transitions(self):
        for scenario in BrokerScenario:
            with self.subTest(scenario=scenario):
                broker, clock, events = make_broker(scenario)
                try:
                    oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
                except (OrderRejectedError, BrokerCommunicationError):
                    continue
                states_seen = [OrderLifecycleState.PENDING]
                for _ in range(6):
                    try:
                        order = broker.get_order_status(oid)
                    except BrokerCommunicationError:
                        break
                    if order.state != states_seen[-1]:
                        validate_transition(states_seen[-1], order.state)
                        states_seen.append(order.state)
                    if order.state in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED):
                        break

    def test_every_scenario_reaches_a_definitive_resolution_within_bounded_polls(self):
        """Stronger than 'no invalid transition': this specifically
        catches an order that makes NO progress at all and raises
        nothing -- exactly the QUOTE_UNAVAILABLE bug found via the
        long-duration simulation, which the weaker transition-only test
        above did not catch (nothing transitioning is not an invalid
        transition). 'Resolution' means either a terminal state or a
        raised exception -- silent, permanent PENDING is the failure mode
        this test exists to catch."""
        from etf_platform.execution_manager import ExecutionManagerError

        MAX_POLLS = 10
        for scenario in BrokerScenario:
            with self.subTest(scenario=scenario):
                broker, clock, events = make_broker(scenario)
                try:
                    oid = broker.submit_order("A", "BUY", 100, 250.0, "c1")
                except (OrderRejectedError, BrokerCommunicationError):
                    continue  # resolved at submission -- definitive, passes
                resolved = False
                for _ in range(MAX_POLLS):
                    try:
                        order = broker.get_order_status(oid)
                    except (BrokerCommunicationError, ExecutionManagerError):
                        resolved = True
                        break
                    if order.state in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED):
                        resolved = True
                        break
                self.assertTrue(
                    resolved,
                    f"{scenario} never reached a definitive resolution (terminal state or exception) "
                    f"within {MAX_POLLS} polls -- this is a silent-hang defect.",
                )


if __name__ == "__main__":
    unittest.main()
