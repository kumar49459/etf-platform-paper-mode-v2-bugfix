"""Kite HTTP client (Milestone 6). A minimal, injectable wrapper around
raw HTTP calls to api.kite.trade -- the seam that makes KiteBrokerPort
testable at all in an environment with no real network access. Every
test in this module mocks this class; nothing here has been exercised
against the real API (see the Known Limitations report).
"""

from __future__ import annotations

from dataclasses import dataclass

BASE_URL = "https://api.kite.trade"


class KiteHTTPError(Exception):
    """Wraps any non-2xx response with enough detail to classify it
    against the error taxonomy resolved in the architecture review
    (TokenException/403, MarginException, InputException/400,
    GeneralException/DataException/500, 429, 502/503/504)."""

    def __init__(self, status_code, kite_error_type, message, raw_body=None):
        super().__init__(f"Kite HTTP error {status_code} ({kite_error_type}): {message}")
        self.status_code = status_code
        self.kite_error_type = kite_error_type
        self.message = message
        self.raw_body = raw_body


@dataclass(frozen=True)
class KiteHTTPResponse:
    status_code: int
    json_body: dict


class KiteHTTPClient:
    """Real HTTP calls, via whatever underlying transport is injected
    (e.g. requests, or a mock in every test). Deliberately thin -- no
    retry, no rate limiting, no auth logic here; those are separate,
    composable concerns (KiteAuthManager, the rate limiter,
    retry_with_backoff) layered on top by KiteBrokerPort, not baked into
    the transport itself."""

    def __init__(self, api_key, access_token, transport, timeout_seconds=10.0):
        self._api_key = api_key
        self._access_token = access_token
        self._transport = transport
        self._timeout = timeout_seconds

    def _headers(self):
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {self._api_key}:{self._access_token}",
        }

    def request(self, method, path, data=None):
        url = f"{BASE_URL}{path}"
        status_code, json_body = self._transport.request(
            method=method, url=url, headers=self._headers(), data=data, timeout=self._timeout,
        )
        if status_code >= 400:
            error_type = (json_body or {}).get("error_type", "UnknownException")
            message = (json_body or {}).get("message", "No message provided.")
            raise KiteHTTPError(status_code, error_type, message, raw_body=json_body)
        return KiteHTTPResponse(status_code=status_code, json_body=json_body or {})
