"""RequestsHTTPTransport (Milestone 8). The real HTTP transport that was
missing -- every prior test correctly mocked the transport interface
(KiteHTTPClient/TelegramNotificationPort's .request(...) -> (status_code,
json_body) contract), by design, since this environment has no real
network access. This class is the first concrete, requests-backed
implementation of that same interface -- purely additive, changes
nothing about the interface itself.

Exception normalization is not cosmetic: requests' own exceptions
(ConnectionError, Timeout, SSLError) do NOT inherit from Python's
builtin ConnectionError/TimeoutError -- confirmed directly, not assumed.
Both KiteBrokerPort's and TelegramNotificationPort's existing, frozen
_is_retryable() checks test for the BUILTIN types. Without normalization
here, every real network failure would silently fail to be recognized
as retryable by code that was never touched and was never supposed to
need touching.
"""

from __future__ import annotations

from etf_platform.common.logging_setup import get_logger

logger = get_logger("execution_manager.requests_http_transport")

_QUERY_STRING_METHODS = ("GET", "DELETE")


class RequestsHTTPTransport:
    """session is injectable (a real requests.Session() by default, or a
    mock in every test -- no external mocking library needed)."""

    def __init__(self, session=None):
        import requests as _requests
        self._requests = _requests
        self._session = session or _requests.Session()

    def request(self, method, url, headers=None, data=None, params=None, json_body=None, timeout=10.0):
        """Matches the exact shape every existing caller already uses.
        Backward-compatible interpretation of data: for GET/DELETE, if
        data is supplied and params is not, data is treated as query
        parameters. For POST/PUT, data is sent as a form-encoded body,
        matching Kite Connect's actual documented API contract."""
        method_upper = method.upper()
        effective_params = params
        effective_data = data
        if method_upper in _QUERY_STRING_METHODS and data is not None and params is None:
            effective_params = data
            effective_data = None

        try:
            response = self._session.request(
                method=method_upper, url=url, headers=headers,
                params=effective_params,
                data=effective_data if json_body is None else None,
                json=json_body,
                timeout=timeout,
            )
        except self._requests.exceptions.Timeout as exc:
            raise TimeoutError(f"Request to {url} timed out: {exc}") from exc
        except self._requests.exceptions.ConnectionError as exc:
            raise ConnectionError(f"Connection failure for {url}: {exc}") from exc
        except self._requests.exceptions.RequestException as exc:
            raise ConnectionError(f"Request failure for {url}: {exc}") from exc

        try:
            json_response = response.json()
        except ValueError:
            logger.warning("Non-JSON response body from %s (status %d) -- returning empty dict.",
                            url, response.status_code)
            json_response = {}

        return response.status_code, json_response
