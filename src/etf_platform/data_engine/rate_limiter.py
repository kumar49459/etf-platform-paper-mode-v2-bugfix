"""Thread-safe token-bucket rate limiter.

Design decision: token bucket over a fixed sliding window or naive
"sleep(1/rate)" approach. Token bucket allows short bursts up to the bucket
capacity while still enforcing a long-run average rate — this matches how
most real APIs (including NSE and Kite) actually rate-limit, and avoids
unnecessarily throttling a burst of, say, 5 quick calls when the budget
allows it, while still guaranteeing we never sustained-exceed the configured
rate. A naive fixed-interval sleep would either be more conservative than
necessary or require its own bookkeeping to get bursts right anyway.

Two independent buckets are tracked — per-second and per-minute — because
real provider limits are usually expressed as both (e.g. "3/sec, burst
protection, and 180/min sustained"). `acquire()` blocks (sleeps) until both
constraints are satisfied, never raises on a "too fast" caller — backing off
silently and continuing is the correct behavior for a rate limiter used
inside a data-ingestion loop; the caller shouldn't need retry logic layered
on top just to respect a config value.
"""

from __future__ import annotations

import threading
import time

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine.exceptions import RateLimitConfigError

logger = get_logger("data_engine.rate_limiter")


class _TokenBucket:
    def __init__(self, rate_per_second: float, capacity: float) -> None:
        if rate_per_second <= 0:
            raise RateLimitConfigError(f"rate_per_second must be > 0, got {rate_per_second}")
        if capacity <= 0:
            raise RateLimitConfigError(f"capacity must be > 0, got {capacity}")
        self._rate = rate_per_second
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now

    def time_until_available(self, cost: float = 1.0) -> float:
        self._refill()
        if self._tokens >= cost:
            return 0.0
        deficit = cost - self._tokens
        return deficit / self._rate

    def consume(self, cost: float = 1.0) -> None:
        self._refill()
        self._tokens -= cost


class RateLimiter:
    """Enforces both a per-second and a per-minute call budget for one provider."""

    def __init__(self, calls_per_second: float, calls_per_minute: float) -> None:
        self._lock = threading.Lock()
        self._per_second = _TokenBucket(rate_per_second=calls_per_second, capacity=max(1.0, calls_per_second))
        self._per_minute = _TokenBucket(rate_per_second=calls_per_minute / 60.0, capacity=calls_per_minute)

    def acquire(self, *, sleep_fn=time.sleep) -> None:
        """Block until a call is permitted under both budgets, then consume one unit.

        `sleep_fn` is injectable for testing (avoids real wall-clock sleeps in
        the unit test suite).
        """
        with self._lock:
            wait_seconds = max(
                self._per_second.time_until_available(),
                self._per_minute.time_until_available(),
            )
            if wait_seconds > 0:
                logger.debug("Rate limit reached, sleeping %.3fs before next call.", wait_seconds)
                sleep_fn(wait_seconds)
            self._per_second.consume()
            self._per_minute.consume()
