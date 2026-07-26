"""Tests for execution_manager.timezone_utils - the section 8.10 fix."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from etf_platform.execution_manager import (
    IST,
    UTC,
    NaiveDatetimeError,
    is_within_nse_trading_hours,
    require_aware,
    to_ist,
    to_utc,
    utc_now,
)


class TestUtcNow(unittest.TestCase):
    def test_utc_now_is_always_aware(self):
        now = utc_now()
        self.assertIsNotNone(now.tzinfo)
        self.assertEqual(now.tzinfo, UTC)


class TestRequireAware(unittest.TestCase):
    def test_naive_datetime_rejected(self):
        naive = datetime(2026, 7, 17, 10, 0, 0)
        with self.assertRaises(NaiveDatetimeError):
            require_aware(naive)

    def test_aware_datetime_passes_through(self):
        aware = datetime(2026, 7, 17, 10, 0, 0, tzinfo=UTC)
        result = require_aware(aware)
        self.assertEqual(result, aware)

    def test_error_message_includes_param_name(self):
        naive = datetime(2026, 7, 17, 10, 0, 0)
        with self.assertRaises(NaiveDatetimeError) as ctx:
            require_aware(naive, "created_at")
        self.assertIn("created_at", str(ctx.exception))


class TestConversions(unittest.TestCase):
    def test_to_utc_from_ist(self):
        ist_dt = datetime(2026, 7, 17, 15, 30, 0, tzinfo=IST)
        utc_dt = to_utc(ist_dt)
        self.assertEqual(utc_dt.hour, 10)
        self.assertEqual(utc_dt.tzinfo, UTC)

    def test_to_ist_from_utc(self):
        utc_dt = datetime(2026, 7, 17, 10, 0, 0, tzinfo=UTC)
        ist_dt = to_ist(utc_dt)
        self.assertEqual(ist_dt.hour, 15)
        self.assertEqual(ist_dt.minute, 30)

    def test_naive_input_rejected_by_to_utc(self):
        with self.assertRaises(NaiveDatetimeError):
            to_utc(datetime(2026, 7, 17, 10, 0, 0))

    def test_naive_input_rejected_by_to_ist(self):
        with self.assertRaises(NaiveDatetimeError):
            to_ist(datetime(2026, 7, 17, 10, 0, 0))

    def test_roundtrip_preserves_instant(self):
        original = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
        roundtripped = to_utc(to_ist(original))
        self.assertEqual(original, roundtripped)

    def test_arbitrary_third_timezone_converts_correctly(self):
        est = timezone(timedelta(hours=-4))
        est_dt = datetime(2026, 7, 17, 6, 0, 0, tzinfo=est)
        ist_dt = to_ist(est_dt)
        self.assertEqual(ist_dt.hour, 15)
        self.assertEqual(ist_dt.minute, 30)


class TestTradingHours(unittest.TestCase):
    def test_within_hours(self):
        dt = datetime(2026, 7, 17, 12, 0, 0, tzinfo=IST)
        self.assertTrue(is_within_nse_trading_hours(dt))

    def test_before_open(self):
        dt = datetime(2026, 7, 17, 8, 0, 0, tzinfo=IST)
        self.assertFalse(is_within_nse_trading_hours(dt))

    def test_after_close(self):
        dt = datetime(2026, 7, 17, 16, 0, 0, tzinfo=IST)
        self.assertFalse(is_within_nse_trading_hours(dt))

    def test_exact_open_boundary_included(self):
        dt = datetime(2026, 7, 17, 9, 15, 0, tzinfo=IST)
        self.assertTrue(is_within_nse_trading_hours(dt))

    def test_exact_close_boundary_included(self):
        dt = datetime(2026, 7, 17, 15, 30, 0, tzinfo=IST)
        self.assertTrue(is_within_nse_trading_hours(dt))

    def test_utc_input_correctly_converted_before_check(self):
        dt = datetime(2026, 7, 17, 10, 0, 0, tzinfo=UTC)
        self.assertTrue(is_within_nse_trading_hours(dt))
        dt2 = datetime(2026, 7, 17, 10, 1, 0, tzinfo=UTC)
        self.assertFalse(is_within_nse_trading_hours(dt2))

    def test_naive_input_rejected(self):
        with self.assertRaises(NaiveDatetimeError):
            is_within_nse_trading_hours(datetime(2026, 7, 17, 12, 0, 0))


if __name__ == "__main__":
    unittest.main()
