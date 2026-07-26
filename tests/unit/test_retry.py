"""Unit tests for common.retry.retry_with_backoff."""

from __future__ import annotations

import unittest

from etf_platform.common.retry import RetryExhaustedError, retry_with_backoff


class TestRetryWithBackoff(unittest.TestCase):
    def test_succeeds_on_first_try_no_retry(self) -> None:
        calls = []

        def fn():
            calls.append(1)
            return "ok"

        result = retry_with_backoff(fn, is_retryable=lambda e: True, sleep_fn=lambda s: None)
        self.assertEqual(result, "ok")
        self.assertEqual(len(calls), 1)

    def test_retries_then_succeeds(self) -> None:
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("blip")
            return "ok"

        result = retry_with_backoff(
            fn, is_retryable=lambda e: True, max_attempts=5, sleep_fn=lambda s: None
        )
        self.assertEqual(result, "ok")
        self.assertEqual(attempts["n"], 3)

    def test_non_retryable_exception_raises_immediately(self) -> None:
        attempts = {"n": 0}

        def fn():
            attempts["n"] += 1
            raise ValueError("not retryable")

        with self.assertRaises(ValueError):
            retry_with_backoff(fn, is_retryable=lambda e: False, max_attempts=5, sleep_fn=lambda s: None)
        self.assertEqual(attempts["n"], 1)

    def test_exhausting_all_attempts_raises_retry_exhausted(self) -> None:
        def fn():
            raise ConnectionError("always fails")

        with self.assertRaises(RetryExhaustedError) as ctx:
            retry_with_backoff(fn, is_retryable=lambda e: True, max_attempts=3, sleep_fn=lambda s: None)
        self.assertIsInstance(ctx.exception.last_exception, ConnectionError)

    def test_sleep_called_between_attempts_not_after_last(self) -> None:
        sleep_calls = []

        def fn():
            raise ConnectionError("fails")

        with self.assertRaises(RetryExhaustedError):
            retry_with_backoff(
                fn, is_retryable=lambda e: True, max_attempts=3, sleep_fn=lambda s: sleep_calls.append(s)
            )
        # 3 attempts -> 2 sleeps between them, no sleep after the final failure.
        self.assertEqual(len(sleep_calls), 2)

    def test_invalid_max_attempts_raises(self) -> None:
        with self.assertRaises(ValueError):
            retry_with_backoff(lambda: None, is_retryable=lambda e: True, max_attempts=0)

    def test_delay_never_exceeds_max_delay(self) -> None:
        sleep_calls = []

        def fn():
            raise ConnectionError("fails")

        with self.assertRaises(RetryExhaustedError):
            retry_with_backoff(
                fn, is_retryable=lambda e: True, max_attempts=6,
                base_delay_seconds=100.0, max_delay_seconds=1.0,
                sleep_fn=lambda s: sleep_calls.append(s),
            )
        self.assertTrue(all(s <= 1.0 for s in sleep_calls))

    def test_on_retry_callback_invoked(self) -> None:
        callback_calls = []

        def fn():
            if len(callback_calls) < 1:
                raise ConnectionError("blip")
            return "ok"

        def on_retry(attempt, exc, delay):
            callback_calls.append((attempt, str(exc)))

        retry_with_backoff(
            fn, is_retryable=lambda e: True, max_attempts=3, sleep_fn=lambda s: None, on_retry=on_retry
        )
        self.assertEqual(len(callback_calls), 1)
        self.assertEqual(callback_calls[0][0], 1)


if __name__ == "__main__":
    unittest.main()
