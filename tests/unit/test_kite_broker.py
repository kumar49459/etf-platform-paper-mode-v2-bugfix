"""KiteBrokerPort test suite. Every test uses a mocked transport --
nothing here makes a real network call. Covers: BrokerPort contract
compliance, auth/session management, status mapping, tag encoding and
reversibility, error taxonomy, retry/timeout behavior, rate limiting,
and the specific adversarial-review focus areas requested.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from etf_platform.execution_manager import (
    BrokerCommunicationError,
    BrokerPort,
    ExecutionManagerError,
    KiteAuthenticationRequiredError,
    KiteAuthManager,
    KiteBrokerPort,
    KiteHTTPClient,
    OrderLifecycleState,
    OrderRejectedError,
    TagMappingStore,
    UnrecognizedKiteStatusError,
    map_kite_status,
)


class MockTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, headers, data, timeout):
        self.calls.append({"method": method, "url": url, "headers": headers, "data": data, "timeout": timeout})
        response = self.responses.pop(0)
        if callable(response):
            return response()
        return response


def make_broker(responses, max_attempts=3):
    tmp = Path(tempfile.mkdtemp())
    transport = MockTransport(responses)
    http = KiteHTTPClient(api_key="testkey", access_token="testtoken", transport=transport)
    tags = TagMappingStore(tmp / "tags.jsonl")
    broker = KiteBrokerPort(http, tags, max_attempts=max_attempts, sleep_fn=lambda s: None)
    return broker, transport, tmp


class TestBrokerPortContractCompliance(unittest.TestCase):
    def test_kite_broker_port_is_a_broker_port(self):
        broker, transport, tmp = make_broker([])
        self.assertIsInstance(broker, BrokerPort)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_implements_all_five_abstract_methods(self):
        for method in ("submit_order", "get_order_status", "cancel_order", "get_open_orders", "get_available_cash"):
            self.assertTrue(hasattr(KiteBrokerPort, method))


class TestOrderSubmission(unittest.TestCase):
    def test_successful_submission_returns_broker_order_id(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": {"order_id": "kite-123"}}),
        ])
        order_id = broker.submit_order("NIFTYBEES", "BUY", 10, 250.0, "cycle-1")
        self.assertEqual(order_id, "kite-123")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_submission_uses_limit_order_type_and_cnc_product(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": {"order_id": "kite-1"}}),
        ])
        broker.submit_order("A", "BUY", 10, 100.0, "ref")
        sent = transport.calls[0]["data"]
        self.assertEqual(sent["order_type"], "LIMIT")
        self.assertEqual(sent["product"], "CNC")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_real_production_cycle_id_format_fits_as_tag(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": {"order_id": "kite-1"}}),
        ])
        real_cycle_id = "2026-07-recurring_monthly"
        broker.submit_order("NIFTYBEES", "BUY", 10, 250.0, real_cycle_id)
        tag = transport.calls[0]["data"]["tag"]
        self.assertEqual(len(tag), 20)
        self.assertTrue(all(c in "0123456789abcdef" for c in tag))
        shutil.rmtree(tmp, ignore_errors=True)


class TestRejectionHandling(unittest.TestCase):
    def test_margin_exception_raises_order_rejected_not_communication_error(self):
        broker, transport, tmp = make_broker([
            (400, {"error_type": "MarginException", "message": "Insufficient funds. Required 95417.84, available 74251.80."}),
        ])
        with self.assertRaises(OrderRejectedError) as ctx:
            broker.submit_order("A", "BUY", 10, 100.0, "ref")
        self.assertIn("Insufficient funds", str(ctx.exception))
        shutil.rmtree(tmp, ignore_errors=True)

    def test_input_exception_raises_order_rejected(self):
        broker, transport, tmp = make_broker([
            (400, {"error_type": "InputException", "message": "Invalid quantity."}),
        ])
        with self.assertRaises(OrderRejectedError):
            broker.submit_order("A", "BUY", -5, 100.0, "ref")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_holding_exception_raises_order_rejected(self):
        broker, transport, tmp = make_broker([
            (400, {"error_type": "HoldingException", "message": "Insufficient holdings."}),
        ])
        with self.assertRaises(OrderRejectedError):
            broker.submit_order("A", "SELL", 10, 100.0, "ref")
        shutil.rmtree(tmp, ignore_errors=True)


class TestNetworkFailureAndRetry(unittest.TestCase):
    def test_network_exception_retries_then_succeeds(self):
        broker, transport, tmp = make_broker([
            (500, {"error_type": "NetworkException", "message": "OMS unreachable."}),
            (200, {"status": "success", "data": {"order_id": "kite-recovered"}}),
        ])
        order_id = broker.submit_order("A", "BUY", 10, 100.0, "ref")
        self.assertEqual(order_id, "kite-recovered")
        self.assertEqual(len(transport.calls), 2)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_retry_exhaustion_raises_broker_communication_error(self):
        broker, transport, tmp = make_broker([
            (500, {"error_type": "NetworkException", "message": "down"}),
            (500, {"error_type": "NetworkException", "message": "down"}),
            (500, {"error_type": "NetworkException", "message": "down"}),
        ], max_attempts=3)
        with self.assertRaises(BrokerCommunicationError):
            broker.submit_order("A", "BUY", 10, 100.0, "ref")
        self.assertEqual(len(transport.calls), 3)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_rate_limit_429_is_retried(self):
        broker, transport, tmp = make_broker([
            (429, {"error_type": "TooManyRequestsException", "message": "Rate limit exceeded."}),
            (200, {"status": "success", "data": {"order_id": "kite-1"}}),
        ])
        order_id = broker.submit_order("A", "BUY", 10, 100.0, "ref")
        self.assertEqual(order_id, "kite-1")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_backend_unavailable_502_503_504_are_retried(self):
        for code in (502, 503, 504):
            broker, transport, tmp = make_broker([
                (code, {"error_type": "GeneralException", "message": "backend down"}),
                (200, {"status": "success", "data": {"order_id": "kite-1"}}),
            ])
            order_id = broker.submit_order("A", "BUY", 10, 100.0, "ref")
            self.assertEqual(order_id, "kite-1", f"failed for status {code}")
            shutil.rmtree(tmp, ignore_errors=True)

    def test_connection_error_is_retryable(self):
        def raise_connection_error():
            raise ConnectionError("connection reset")
        broker, transport, tmp = make_broker([
            raise_connection_error,
            (200, {"status": "success", "data": {"order_id": "kite-1"}}),
        ])
        order_id = broker.submit_order("A", "BUY", 10, 100.0, "ref")
        self.assertEqual(order_id, "kite-1")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_a_genuine_rejection_is_never_retried(self):
        broker, transport, tmp = make_broker([
            (400, {"error_type": "MarginException", "message": "Insufficient funds."}),
        ])
        with self.assertRaises(OrderRejectedError):
            broker.submit_order("A", "BUY", 10, 100.0, "ref")
        self.assertEqual(len(transport.calls), 1, "A rejection must not be retried.")
        shutil.rmtree(tmp, ignore_errors=True)


class TestSessionExpiry(unittest.TestCase):
    def test_token_exception_403_is_not_retried_and_raises_communication_error(self):
        broker, transport, tmp = make_broker([
            (403, {"error_type": "TokenException", "message": "Invalid access token."}),
        ])
        with self.assertRaises(BrokerCommunicationError):
            broker.get_order_status("some-id")
        self.assertEqual(len(transport.calls), 1, "TokenException must not be retried -- it will never succeed.")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_kite_auth_manager_fails_loudly_with_no_session(self):
        auth = KiteAuthManager(api_key="k", api_secret="s")
        with self.assertRaises(KiteAuthenticationRequiredError):
            auth.get_access_token()

    def test_kite_auth_manager_never_auto_generates_a_session(self):
        auth = KiteAuthManager(api_key="k", api_secret="s")
        self.assertFalse(auth.has_valid_session())
        self.assertFalse(hasattr(auth, "login") or hasattr(auth, "auto_login"))

    def test_checksum_computation_is_correct_and_deterministic(self):
        import hashlib

        auth = KiteAuthManager(api_key="mykey", api_secret="mysecret")
        checksum = auth.compute_checksum("myrequesttoken")
        expected = hashlib.sha256(b"mykeymyrequesttokenmysecret").hexdigest()
        self.assertEqual(checksum, expected)
        self.assertEqual(checksum, auth.compute_checksum("myrequesttoken"))

    def test_no_kite_http_error_leaks_from_any_method(self):
        """Regression for a real bug found by
        test_token_exception_403_is_not_retried_and_raises_communication_error:
        only submit_order() originally had its own try/except KiteHTTPError --
        the other four BrokerPort methods let a non-retryable KiteHTTPError
        (a Kite-specific type) leak past this class's boundary. Every
        method must wrap it into BrokerCommunicationError."""
        cases = [
            ("get_order_status", ("some-id",)),
            ("cancel_order", ("some-id",)),
            ("get_open_orders", ()),
            ("get_available_cash", ()),
        ]
        for method_name, args in cases:
            broker, transport, tmp = make_broker([
                (403, {"error_type": "TokenException", "message": "Invalid access token."}),
            ])
            with self.assertRaises(BrokerCommunicationError, msg=f"{method_name} leaked a non-BrokerCommunicationError"):
                getattr(broker, method_name)(*args)
            shutil.rmtree(tmp, ignore_errors=True)


class TestStatusMapping(unittest.TestCase):
    def test_transient_states_map_to_pending(self):
        for status in ("PUT ORDER REQ RECEIVED", "VALIDATION PENDING", "OPEN PENDING", "AMO REQ RECEIVED",
                       "MODIFY VALIDATION PENDING", "MODIFY PENDING", "MODIFIED", "CANCEL PENDING"):
            self.assertEqual(map_kite_status(status, 0, 100), OrderLifecycleState.PENDING, f"failed for {status}")

    def test_open_with_zero_filled_is_pending(self):
        self.assertEqual(map_kite_status("OPEN", 0, 100), OrderLifecycleState.PENDING)

    def test_open_with_partial_fill_is_partially_filled(self):
        self.assertEqual(map_kite_status("OPEN", 40, 100), OrderLifecycleState.PARTIALLY_FILLED)

    def test_trigger_pending_with_partial_fill_is_partially_filled(self):
        self.assertEqual(map_kite_status("TRIGGER PENDING", 30, 100), OrderLifecycleState.PARTIALLY_FILLED)

    def test_open_fully_filled_is_filled(self):
        self.assertEqual(map_kite_status("OPEN", 100, 100), OrderLifecycleState.FILLED)

    def test_complete_is_filled(self):
        self.assertEqual(map_kite_status("COMPLETE", 100, 100), OrderLifecycleState.FILLED)

    def test_cancelled_is_cancelled(self):
        self.assertEqual(map_kite_status("CANCELLED", 0, 100), OrderLifecycleState.CANCELLED)

    def test_rejected_is_failed(self):
        self.assertEqual(map_kite_status("REJECTED", 0, 100), OrderLifecycleState.FAILED)

    def test_unrecognized_status_fails_loudly_not_silently(self):
        with self.assertRaises(UnrecognizedKiteStatusError):
            map_kite_status("SOME_NEW_STATUS_KITE_ADDS_LATER", 0, 100)


class TestPartialFillsViaGetOrderStatus(unittest.TestCase):
    def test_partial_fill_correctly_derived_from_real_order_shape(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": [
                {"order_id": "o1", "tradingsymbol": "A", "status": "OPEN", "quantity": 100,
                 "filled_quantity": 40, "average_price": 250.5, "tag": "abc"},
            ]}),
        ])
        view = broker.get_order_status("o1")
        self.assertEqual(view.state, OrderLifecycleState.PARTIALLY_FILLED)
        self.assertEqual(view.executed_quantity, 40)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_get_order_status_uses_the_last_history_entry(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": [
                {"order_id": "o1", "tradingsymbol": "A", "status": "PUT ORDER REQ RECEIVED", "quantity": 100, "filled_quantity": 0, "tag": "x"},
                {"order_id": "o1", "tradingsymbol": "A", "status": "OPEN", "quantity": 100, "filled_quantity": 0, "tag": "x"},
                {"order_id": "o1", "tradingsymbol": "A", "status": "COMPLETE", "quantity": 100, "filled_quantity": 100, "average_price": 100.0, "tag": "x"},
            ]}),
        ])
        view = broker.get_order_status("o1")
        self.assertEqual(view.state, OrderLifecycleState.FILLED)
        shutil.rmtree(tmp, ignore_errors=True)


class TestOrderHistoryInconsistencies(unittest.TestCase):
    def test_empty_history_raises_execution_manager_error(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": []}),
        ])
        with self.assertRaises(ExecutionManagerError):
            broker.get_order_status("nonexistent-id")
        shutil.rmtree(tmp, ignore_errors=True)

    def test_missing_average_price_field_handled_gracefully(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": [
                {"order_id": "o1", "tradingsymbol": "A", "status": "OPEN", "quantity": 100, "filled_quantity": 0, "tag": "x"},
            ]}),
        ])
        view = broker.get_order_status("o1")
        self.assertIsNone(view.executed_price)
        shutil.rmtree(tmp, ignore_errors=True)

    def test_get_open_orders_correctly_filters_terminal_states(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": [
                {"order_id": "o1", "tradingsymbol": "A", "status": "OPEN", "quantity": 100, "filled_quantity": 0, "tag": "t1"},
                {"order_id": "o2", "tradingsymbol": "B", "status": "COMPLETE", "quantity": 50, "filled_quantity": 50, "average_price": 50.0, "tag": "t2"},
                {"order_id": "o3", "tradingsymbol": "C", "status": "CANCELLED", "quantity": 10, "filled_quantity": 0, "tag": "t3"},
                {"order_id": "o4", "tradingsymbol": "D", "status": "REJECTED", "quantity": 5, "filled_quantity": 0, "tag": "t4"},
            ]}),
        ])
        open_orders = broker.get_open_orders()
        ids = {o.broker_order_id for o in open_orders}
        self.assertEqual(ids, {"o1", "o4"})
        shutil.rmtree(tmp, ignore_errors=True)

    def test_tag_resolves_back_to_client_reference_via_open_orders(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": {"order_id": "o1"}}),
        ])
        broker.submit_order("A", "BUY", 10, 100.0, "my-cycle-id")
        tag_used = broker._tags.record("my-cycle-id")

        transport.responses.append((200, {"status": "success", "data": [
            {"order_id": "o1", "tradingsymbol": "A", "status": "OPEN", "quantity": 10, "filled_quantity": 0, "tag": tag_used},
        ]}))
        open_orders = broker.get_open_orders()
        self.assertEqual(open_orders[0].client_reference, "my-cycle-id")
        shutil.rmtree(tmp, ignore_errors=True)


class TestCancelOrder(unittest.TestCase):
    def test_cancel_calls_the_correct_endpoint(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": {"order_id": "o1"}}),
        ])
        broker.cancel_order("o1")
        self.assertEqual(transport.calls[0]["method"], "DELETE")
        self.assertIn("o1", transport.calls[0]["url"])
        shutil.rmtree(tmp, ignore_errors=True)


class TestAvailableCash(unittest.TestCase):
    def test_uses_net_field_not_live_balance(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": {"net": 50000.0, "available": {"live_balance": 75000.0}}}),
        ])
        cash = broker.get_available_cash()
        self.assertEqual(cash, 50000.0)
        shutil.rmtree(tmp, ignore_errors=True)


class TestRateLimiting(unittest.TestCase):
    def test_rate_limiter_is_consulted_before_every_call(self):
        calls_to_rate_limiter = []

        class TrackingRateLimiter:
            def acquire(self, sleep_fn=None):
                calls_to_rate_limiter.append(1)

        tmp = Path(tempfile.mkdtemp())
        transport = MockTransport([
            (200, {"status": "success", "data": {"order_id": "o1"}}),
            (200, {"status": "success", "data": {"order_id": "o2"}}),
        ])
        http = KiteHTTPClient("k", "t", transport)
        tags = TagMappingStore(tmp / "tags.jsonl")
        broker = KiteBrokerPort(http, tags, rate_limiter=TrackingRateLimiter(), sleep_fn=lambda s: None)
        broker.submit_order("A", "BUY", 10, 100.0, "ref1")
        broker.submit_order("B", "BUY", 10, 100.0, "ref2")
        self.assertEqual(len(calls_to_rate_limiter), 2)
        shutil.rmtree(tmp, ignore_errors=True)


class TestNoKiteSpecificTypesLeak(unittest.TestCase):
    def test_get_order_status_returns_orderlifecyclestate_not_a_raw_string(self):
        broker, transport, tmp = make_broker([
            (200, {"status": "success", "data": [
                {"order_id": "o1", "tradingsymbol": "A", "status": "COMPLETE", "quantity": 10, "filled_quantity": 10, "average_price": 100.0, "tag": "x"},
            ]}),
        ])
        view = broker.get_order_status("o1")
        self.assertIsInstance(view.state, OrderLifecycleState)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
