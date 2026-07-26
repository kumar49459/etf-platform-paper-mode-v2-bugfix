"""Retry with exponential backoff + jitter, for transient failures against
external APIs (NSE, Kite).

Design decisions:
- Only network-level/transient failures are retried (connection errors,
  timeouts, and 5xx server errors) — NOT 4xx client errors like bad auth or
  bad request, which will fail identically on every retry and just waste the
  rate-limit budget while delaying the fallback-to-secondary-provider path
  in HistoricalDataEngine. The caller supplies which exception types /
  status codes count as retryable via `is_retryable`.
- Full jitter (not just exponential backoff) — reduces the "thundering herd"
  effect if multiple ingestion runs happen to retry at the same moment
  (e.g. after a shared network blip). This is a well-established pattern
  (AWS's own retry guidance recommends it) and costs nothing to implement.
- A hard `max_attempts` ceiling — this is a rate-limited, scheduled batch
  process, not a request that must succeed at any cost; per Phase 1 §1.4
  fail-safe default, giving up and letting the caller fall back to the
  secondary provider (or halt) is the correct behavior, not retrying forever.
"""

from __future__ import annotations

import random
import time
from typing import Callable, TypeVar

from etf_platform.common.logging_setup import get_logger

logger = get_logger("common.retry")

T = TypeVar("T")


class RetryExhaustedError(Exception):
    """Raised when all retry attempts are exhausted. Wraps the last
    underlying exception so callers can still inspect the root cause."""

    def __init__(self, message: str, last_exception: Exception) -> None:
        super().__init__(message)
        self.last_exception = last_exception


def retry_with_backoff(
    fn: Callable[[], T],
    *,
    is_retryable: Callable[[Exception], bool],
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> T:
    """Call `fn()`, retrying on retryable exceptions with exponential
    backoff and full jitter.

    Delay for attempt `n` (0-indexed) is `random.uniform(0, min(max_delay,
    base_delay * 2**n))` — full jitter, per the design rationale above.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    last_exception: Exception | None = None
    for attempt in range(max_attempts):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — filtered by is_retryable below
            last_exception = exc
            if not is_retryable(exc):
                raise
            if attempt == max_attempts - 1:
                break
            delay = random.uniform(0, min(max_delay_seconds, base_delay_seconds * (2**attempt)))
            if on_retry is not None:
                on_retry(attempt + 1, exc, delay)
            logger.warning(
                "Retryable error on attempt %d/%d: %s. Retrying in %.2fs.",
                attempt + 1, max_attempts, exc, delay,
            )
            sleep_fn(delay)

    assert last_exception is not None  # loop always sets this before falling through
    raise RetryExhaustedError(
        f"All {max_attempts} attempt(s) failed. Last error: {last_exception}", last_exception
    )
