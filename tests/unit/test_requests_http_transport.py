"""RequestsHTTPTransport test suite (Milestone 8). Every test injects a
mocked requests.Session -- no real network access required.
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock

import requests

from etf_platform.common.requests_http_transport import RequestsHTTPTransport


def make_response(status_code, json_data=None, json_raises=False):
    response = Mock()
    response.status_code = status_code
    if json_raises:
        response.json.side_effect = ValueError("not JSON")
    else:
        response.json.return_value = json_data or {}
    return response


class TestSuccessResponses(unittest.TestCase):
    def test_get_returns_status_and_json_body(self):
        session = Mock()
        session.request.return_value = make_response(200, {"ok": True, "data": {"net": 5000.0}})
        transport = RequestsHTTPTransport(session=session)
        status, body = transport.request("GET", "https://api.kite.trade/user/margins/equity", timeout=5.0)
        self.assertEqual(status, 200)
        self.assertEqual(body, {"ok": True, "data": {"net": 5000.0}})

    def test_response_is_never_a_raw_requests_response(self):
        session = Mock()
        session.request.return_value = make_response(200, {"ok": True})
        transport = RequestsHTTPTransport(session=session)
        status, body = transport.request("GET", "https://api.kite.trade/orders", timeout=5.0)
        self.assertIsInstance(status, int)
        self.assertIsInstance(body, dict)
        self.assertNotIsInstance(body, requests.Response)


class TestHTTPMethods(unittest.TestCase):
    def test_get_supported(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)
        self.assertEqual(session.request.call_args.kwargs["method"], "GET")

    def test_post_supported(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request("POST", "https://x/orders/regular", data={"a": 1}, timeout=5.0)
        self.assertEqual(session.request.call_args.kwargs["method"], "POST")
        self.assertEqual(session.request.call_args.kwargs["data"], {"a": 1})

    def test_put_supported(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request("PUT", "https://x/orders/regular/1", data={"a": 1}, timeout=5.0)
        self.assertEqual(session.request.call_args.kwargs["method"], "PUT")
        self.assertEqual(session.request.call_args.kwargs["data"], {"a": 1})

    def test_delete_supported(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request("DELETE", "https://x/orders/regular/1", timeout=5.0)
        self.assertEqual(session.request.call_args.kwargs["method"], "DELETE")

    def test_lowercase_method_is_normalized(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request("get", "https://x/orders", timeout=5.0)
        self.assertEqual(session.request.call_args.kwargs["method"], "GET")


class TestHeadersAndParams(unittest.TestCase):
    def test_headers_passed_through(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        headers = {"Authorization": "token abc:def", "X-Kite-Version": "3"}
        RequestsHTTPTransport(session=session).request("GET", "https://x/orders", headers=headers, timeout=5.0)
        self.assertEqual(session.request.call_args.kwargs["headers"], headers)

    def test_explicit_params_used_directly(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request(
            "GET", "https://x/orders", params={"symbol": "NIFTYBEES"}, timeout=5.0,
        )
        self.assertEqual(session.request.call_args.kwargs["params"], {"symbol": "NIFTYBEES"})

    def test_data_on_get_becomes_query_params_backward_compatible(self):
        session = Mock()
        session.request.return_value = make_response(200, {"ok": True, "result": []})
        RequestsHTTPTransport(session=session).request("GET", "https://x/getUpdates", data={"offset": 7}, timeout=5.0)
        self.assertEqual(session.request.call_args.kwargs["params"], {"offset": 7})
        self.assertIsNone(session.request.call_args.kwargs["data"])

    def test_data_on_post_stays_form_body(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request(
            "POST", "https://x/orders/regular", data={"tradingsymbol": "A", "quantity": 10}, timeout=5.0,
        )
        self.assertEqual(session.request.call_args.kwargs["data"], {"tradingsymbol": "A", "quantity": 10})
        self.assertIsNone(session.request.call_args.kwargs["params"])


class TestJSONBody(unittest.TestCase):
    def test_json_body_supported_and_suppresses_form_data(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request(
            "POST", "https://x/y", data={"ignored": True}, json_body={"real": "payload"}, timeout=5.0,
        )
        self.assertEqual(session.request.call_args.kwargs["json"], {"real": "payload"})
        self.assertIsNone(session.request.call_args.kwargs["data"])


class TestConfigurableTimeout(unittest.TestCase):
    def test_timeout_passed_through(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=42.0)
        self.assertEqual(session.request.call_args.kwargs["timeout"], 42.0)

    def test_default_timeout_is_ten_seconds(self):
        session = Mock()
        session.request.return_value = make_response(200, {})
        RequestsHTTPTransport(session=session).request("GET", "https://x/orders")
        self.assertEqual(session.request.call_args.kwargs["timeout"], 10.0)


class Test4xxAnd5xxPassThroughAsStatusCodes(unittest.TestCase):
    def test_400_returned_not_raised(self):
        session = Mock()
        session.request.return_value = make_response(400, {"error_type": "InputException", "message": "bad"})
        status, body = RequestsHTTPTransport(session=session).request("POST", "https://x/orders/regular", timeout=5.0)
        self.assertEqual(status, 400)
        self.assertEqual(body["error_type"], "InputException")

    def test_403_returned_not_raised(self):
        session = Mock()
        session.request.return_value = make_response(403, {"error_type": "TokenException", "message": "bad token"})
        status, body = RequestsHTTPTransport(session=session).request("GET", "https://x/orders/1", timeout=5.0)
        self.assertEqual(status, 403)

    def test_500_returned_not_raised(self):
        session = Mock()
        session.request.return_value = make_response(500, {"error_type": "GeneralException", "message": "down"})
        status, body = RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)
        self.assertEqual(status, 500)

    def test_429_returned_not_raised(self):
        session = Mock()
        session.request.return_value = make_response(429, {"error_type": "TooManyRequestsException"})
        status, body = RequestsHTTPTransport(session=session).request("POST", "https://x/orders/regular", timeout=5.0)
        self.assertEqual(status, 429)


class TestExceptionNormalization(unittest.TestCase):
    def test_timeout_raises_builtin_timeout_error(self):
        session = Mock()
        session.request.side_effect = requests.exceptions.Timeout("timed out")
        with self.assertRaises(TimeoutError) as ctx:
            RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)
        self.assertIsInstance(ctx.exception, TimeoutError)
        self.assertNotIsInstance(ctx.exception, requests.exceptions.Timeout)

    def test_connect_timeout_raises_builtin_timeout_error(self):
        session = Mock()
        session.request.side_effect = requests.exceptions.ConnectTimeout("connect timed out")
        with self.assertRaises(TimeoutError):
            RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)

    def test_read_timeout_raises_builtin_timeout_error(self):
        session = Mock()
        session.request.side_effect = requests.exceptions.ReadTimeout("read timed out")
        with self.assertRaises(TimeoutError):
            RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)

    def test_connection_failure_raises_builtin_connection_error(self):
        session = Mock()
        session.request.side_effect = requests.exceptions.ConnectionError("connection refused")
        with self.assertRaises(ConnectionError) as ctx:
            RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)
        self.assertIsInstance(ctx.exception, ConnectionError)

    def test_dns_failure_raises_builtin_connection_error(self):
        session = Mock()
        session.request.side_effect = requests.exceptions.ConnectionError(
            "HTTPSConnectionPool: Max retries exceeded: Failed to resolve 'api.kite.trade' "
            "([Errno -2] Name or service not known)"
        )
        with self.assertRaises(ConnectionError):
            RequestsHTTPTransport(session=session).request("GET", "https://api.kite.trade/orders", timeout=5.0)

    def test_ssl_failure_raises_builtin_connection_error(self):
        session = Mock()
        session.request.side_effect = requests.exceptions.SSLError("certificate verify failed")
        with self.assertRaises(ConnectionError) as ctx:
            RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)
        self.assertIsInstance(ctx.exception, ConnectionError)

    def test_other_request_exception_raises_builtin_connection_error(self):
        session = Mock()
        session.request.side_effect = requests.exceptions.TooManyRedirects("too many redirects")
        with self.assertRaises(ConnectionError):
            RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)

    def test_normalized_exceptions_are_recognized_by_kite_broker_ports_existing_retry_logic(self):
        from etf_platform.execution_manager.kite_broker import _is_retryable

        session = Mock()
        session.request.side_effect = requests.exceptions.ConnectionError("down")
        try:
            RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)
        except Exception as exc:
            self.assertTrue(_is_retryable(exc), "Normalized ConnectionError must be recognized as retryable.")

        session2 = Mock()
        session2.request.side_effect = requests.exceptions.Timeout("timed out")
        try:
            RequestsHTTPTransport(session=session2).request("GET", "https://x/orders", timeout=5.0)
        except Exception as exc:
            self.assertTrue(_is_retryable(exc), "Normalized TimeoutError must be recognized as retryable.")


class TestMalformedJSON(unittest.TestCase):
    def test_non_json_response_returns_empty_dict_not_a_crash(self):
        session = Mock()
        session.request.return_value = make_response(200, json_raises=True)
        status, body = RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)
        self.assertEqual(status, 200)
        self.assertEqual(body, {})

    def test_non_json_error_response_still_returns_status_and_empty_dict(self):
        session = Mock()
        session.request.return_value = make_response(403, json_raises=True)
        status, body = RequestsHTTPTransport(session=session).request("GET", "https://x/orders", timeout=5.0)
        self.assertEqual(status, 403)
        self.assertEqual(body, {})


class TestRetryInteraction(unittest.TestCase):
    def test_kite_http_client_retries_through_normalized_connection_error(self):
        from etf_platform.execution_manager.kite_broker import KiteBrokerPort
        from etf_platform.execution_manager.kite_http_client import KiteHTTPClient
        from etf_platform.execution_manager.kite_tag_encoding import TagMappingStore
        import tempfile
        from pathlib import Path

        session = Mock()
        session.request.side_effect = [
            requests.exceptions.ConnectionError("transient failure"),
            make_response(200, {"status": "success", "data": {"order_id": "kite-1"}}),
        ]
        transport = RequestsHTTPTransport(session=session)
        http_client = KiteHTTPClient(api_key="k", access_token="t", transport=transport)
        tmp = Path(tempfile.mkdtemp())
        broker = KiteBrokerPort(http_client, TagMappingStore(tmp / "tags.jsonl"), sleep_fn=lambda s: None)

        order_id = broker.submit_order("A", "BUY", 10, 100.0, "ref")
        self.assertEqual(order_id, "kite-1")
        self.assertEqual(session.request.call_count, 2, "Must have retried once after the normalized ConnectionError.")


class TestNoRealNetworkAccessRequired(unittest.TestCase):
    def test_construction_does_not_require_network_access(self):
        transport = RequestsHTTPTransport()
        self.assertIsNotNone(transport)


if __name__ == "__main__":
    unittest.main()
