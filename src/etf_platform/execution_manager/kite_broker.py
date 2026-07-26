"""KiteBrokerPort (Milestone 6). Implements BrokerPort (frozen, exactly
as designed -- no interface changes) against the real Kite Connect API.
Every Kite-specific detail (status vocabulary, tag encoding, rate limits,
error taxonomy) is absorbed inside this class; nothing Kite-specific is
exposed to any caller.

Nothing in this file has been exercised against a real Kite account --
see the KiteBrokerPort Verification Report and Known Limitations
document for exactly what is and isn't covered by testing in this
environment.
"""

from __future__ import annotations

from dataclasses import dataclass

from etf_platform.common.retry import RetryExhaustedError, retry_with_backoff
from etf_platform.data_engine.rate_limiter import RateLimiter
from etf_platform.execution_manager.exceptions import BrokerCommunicationError, ExecutionManagerError
from etf_platform.execution_manager.kite_http_client import KiteHTTPError
from etf_platform.execution_manager.kite_status_mapping import map_kite_status
from etf_platform.execution_manager.models import OrderLifecycleState
from etf_platform.execution_manager.paper_broker import OrderRejectedError
from etf_platform.execution_manager.ports import BrokerPort

RATE_LIMIT_ORDERS_PER_SECOND = 5.0
"""Configured with real headroom below Kite's documented 10/sec ceiling
(architecture review, Section 3.3) -- deliberately conservative, not the
maximum allowed, and explicitly a re-verify-at-integration-time constant."""
RATE_LIMIT_ORDERS_PER_MINUTE = 200.0

_RETRYABLE_KITE_ERROR_TYPES = frozenset({"NetworkException", "GeneralException", "DataException"})
_RETRYABLE_HTTP_STATUS = frozenset({429, 502, 503, 504})


@dataclass(frozen=True)
class KiteOrderView:
    """The broker-agnostic view KiteBrokerPort returns -- duck-type
    compatible with what ReconciliationService and SubmissionOrchestrator
    already consume from PaperOrder. No raw Kite JSON, no Kite-specific
    status strings, ever escape this class."""

    broker_order_id: str
    symbol: str
    state: OrderLifecycleState
    executed_quantity: int
    executed_price: object
    client_reference: object


def _is_retryable(exc):
    if isinstance(exc, KiteHTTPError):
        return exc.status_code in _RETRYABLE_HTTP_STATUS or exc.kite_error_type in _RETRYABLE_KITE_ERROR_TYPES
    return isinstance(exc, (ConnectionError, TimeoutError))


class KiteBrokerPort(BrokerPort):
    def __init__(self, http_client, tag_mapping_store, rate_limiter=None, max_attempts=3, sleep_fn=None):
        self._http = http_client
        self._tags = tag_mapping_store
        self._rate_limiter = rate_limiter or RateLimiter(
            calls_per_second=RATE_LIMIT_ORDERS_PER_SECOND, calls_per_minute=RATE_LIMIT_ORDERS_PER_MINUTE,
        )
        self._max_attempts = max_attempts
        self._sleep_fn = sleep_fn

    def _call(self, method, path, data=None):
        if self._sleep_fn:
            self._rate_limiter.acquire(sleep_fn=self._sleep_fn)
        else:
            self._rate_limiter.acquire()

        def attempt():
            return self._http.request(method, path, data=data)

        kwargs = {"is_retryable": _is_retryable, "max_attempts": self._max_attempts}
        if self._sleep_fn:
            kwargs["sleep_fn"] = self._sleep_fn
        try:
            return retry_with_backoff(attempt, **kwargs)
        except RetryExhaustedError as exc:
            raise BrokerCommunicationError(
                f"Kite API call to {path} failed after {self._max_attempts} attempts: {exc.last_exception}"
            ) from exc

    def submit_order(self, symbol, side, quantity, limit_price, client_reference):
        tag = self._tags.record(client_reference)
        try:
            response = self._call("POST", "/orders/regular", data={
                "tradingsymbol": symbol, "exchange": "NSE", "transaction_type": side,
                "order_type": "LIMIT", "quantity": quantity, "price": limit_price,
                "product": "CNC", "validity": "DAY", "tag": tag,
            })
        except KiteHTTPError as exc:
            if exc.kite_error_type in ("MarginException", "InputException", "OrderException", "HoldingException"):
                raise OrderRejectedError(f"{exc.kite_error_type}: {exc.message}") from exc
            raise BrokerCommunicationError(f"Kite order placement failed: {exc}") from exc
        return response.json_body["data"]["order_id"]

    def get_order_status(self, broker_order_id):
        try:
            response = self._call("GET", f"/orders/{broker_order_id}")
        except KiteHTTPError as exc:
            # Found via this milestone's own session-expiry test: a
            # non-retryable KiteHTTPError (e.g. TokenException/403) is
            # re-raised directly by retry_with_backoff, NOT wrapped in
            # RetryExhaustedError -- only submit_order() had its own
            # try/except for this; the other four BrokerPort methods did
            # not, meaning a raw KiteHTTPError (a Kite-specific type) was
            # leaking past this class's boundary, violating "do not
            # expose Kite-specific types outside KiteBrokerPort." Every
            # method now wraps explicitly, not just the retry-exhaustion path.
            raise BrokerCommunicationError(f"Kite order status query failed: {exc}") from exc
        history = response.json_body.get("data", [])
        if not history:
            raise ExecutionManagerError(f"KiteBrokerPort: no order history returned for {broker_order_id!r}")
        latest = history[-1]
        return self._to_order_view(latest)

    def cancel_order(self, broker_order_id):
        try:
            self._call("DELETE", f"/orders/regular/{broker_order_id}")
        except KiteHTTPError as exc:
            raise BrokerCommunicationError(f"Kite order cancellation failed: {exc}") from exc

    def get_open_orders(self):
        try:
            response = self._call("GET", "/orders")
        except KiteHTTPError as exc:
            raise BrokerCommunicationError(f"Kite open-orders query failed: {exc}") from exc
        views = [self._to_order_view(row) for row in response.json_body.get("data", [])]
        return [v for v in views if v.state not in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED)]

    def get_available_cash(self):
        try:
            response = self._call("GET", "/user/margins/equity")
        except KiteHTTPError as exc:
            raise BrokerCommunicationError(f"Kite funds query failed: {exc}") from exc
        return float(response.json_body["data"]["net"])

    def _to_order_view(self, kite_order_dict):
        quantity = int(kite_order_dict.get("quantity", 0))
        filled_quantity = int(kite_order_dict.get("filled_quantity", 0))
        state = map_kite_status(kite_order_dict["status"], filled_quantity, quantity)
        tag = kite_order_dict.get("tag")
        client_reference = self._tags.resolve(tag) if tag else None
        return KiteOrderView(
            broker_order_id=kite_order_dict["order_id"], symbol=kite_order_dict["tradingsymbol"], state=state,
            executed_quantity=filled_quantity,
            executed_price=kite_order_dict.get("average_price") or None,
            client_reference=client_reference,
        )

    def get_holdings(self):
        return self._call("GET", "/portfolio/holdings").json_body.get("data", [])

    def get_positions(self):
        return self._call("GET", "/portfolio/positions").json_body.get("data", {})

    def get_funds(self):
        return self._call("GET", "/user/margins").json_body.get("data", {})

    def get_order_history(self, broker_order_id):
        response = self._call("GET", f"/orders/{broker_order_id}")
        return [self._to_order_view(row) for row in response.json_body.get("data", [])]
