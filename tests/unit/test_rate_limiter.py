"""Unit tests for the token-bucket rate limiter.

Uses an injected `sleep_fn` throughout so these tests run in milliseconds,
not real wall-clock seconds — see RateLimiter.acquire()'s `sleep_fn` param.
"""

from __future__ import annotations

import unittest

from etf_platform.data_engine.exceptions import RateLimitConfigError
from etf_platform.data_engine.rate_limiter import RateLimiter


class FakeClock:
    """Advances only when told to — lets us simulate elapsed time deterministically."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleep_calls: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleep_calls.append(seconds)
        self.now += seconds


class TestRateLimiterConfig(unittest.TestCase):
    def test_zero_rate_raises(self) -> None:
        with self.assertRaises(RateLimitConfigError):
            RateLimiter(calls_per_second=0, calls_per_minute=60)

    def test_negative_rate_raises(self) -> None:
        with self.assertRaises(RateLimitConfigError):
            RateLimiter(calls_per_second=-1, calls_per_minute=60)


class TestRateLimiterBehavior(unittest.TestCase):
    def test_first_calls_up_to_capacity_do_not_sleep(self) -> None:
        limiter = RateLimiter(calls_per_second=3.0, calls_per_minute=180.0)
        clock = FakeClock()
        for _ in range(3):
            limiter.acquire(sleep_fn=clock.sleep)
        self.assertEqual(clock.sleep_calls, [])

    def test_exceeding_per_second_budget_triggers_sleep(self) -> None:
        limiter = RateLimiter(calls_per_second=2.0, calls_per_minute=180.0)
        clock = FakeClock()
        for _ in range(2):
            limiter.acquire(sleep_fn=clock.sleep)
        # third call within the same "instant" should have to wait
        limiter.acquire(sleep_fn=clock.sleep)
        self.assertTrue(len(clock.sleep_calls) >= 1)
        self.assertTrue(all(s > 0 for s in clock.sleep_calls))

    def test_per_minute_budget_is_enforced_independently(self) -> None:
        # Very generous per-second budget, very tight per-minute budget —
        # forces the per-minute bucket to be the binding constraint.
        limiter = RateLimiter(calls_per_second=1000.0, calls_per_minute=2.0)
        clock = FakeClock()
        limiter.acquire(sleep_fn=clock.sleep)
        limiter.acquire(sleep_fn=clock.sleep)
        limiter.acquire(sleep_fn=clock.sleep)  # 3rd call exceeds 2/min budget
        self.assertTrue(any(s > 0 for s in clock.sleep_calls))

    def test_thread_safety_smoke_test(self) -> None:
        """Not a rigorous concurrency proof, but confirms acquire() doesn't
        raise or deadlock when called from multiple threads concurrently."""
        import threading

        limiter = RateLimiter(calls_per_second=50.0, calls_per_minute=3000.0)
        errors: list[Exception] = []

        def worker() -> None:
            try:
                for _ in range(5):
                    limiter.acquire(sleep_fn=lambda s: None)  # no-op sleep for speed
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
