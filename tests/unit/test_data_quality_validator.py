"""Unit tests for the DataQualityValidator orchestrator (the halt/force-override
behavior, not the individual checks — those are covered in
test_data_quality_checks.py)."""

from __future__ import annotations

import unittest
from datetime import date

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.data_quality.exceptions import CriticalDataQualityError
from etf_platform.data_quality.validator import DataQualityValidator


def good_bar(d: date) -> OHLCVBar:
    return OHLCVBar(symbol="NIFTYBEES", trade_date=d, open=100, high=101, low=99, close=100.5, volume=1000)


def bad_bar(d: date) -> OHLCVBar:
    # low > high: structurally invalid, always CRITICAL.
    return OHLCVBar(symbol="NIFTYBEES", trade_date=d, open=100, high=90, low=110, close=100, volume=1000)


class TestDataQualityValidator(unittest.TestCase):
    def setUp(self) -> None:
        self.validator = DataQualityValidator(max_price_jump_pct=20.0, stale_price_max_days=10)

    def test_clean_data_returns_report_without_raising(self) -> None:
        bars = [good_bar(date(2026, 1, 2))]
        report = self.validator.validate(
            "snap-1", "NIFTYBEES", bars, [], expected_start=date(2026, 1, 2), expected_end=date(2026, 1, 2)
        )
        self.assertFalse(report.has_critical)

    def test_critical_issue_raises_by_default(self) -> None:
        bars = [bad_bar(date(2026, 1, 2))]
        with self.assertRaises(CriticalDataQualityError):
            self.validator.validate(
                "snap-1", "NIFTYBEES", bars, [], expected_start=date(2026, 1, 2), expected_end=date(2026, 1, 2)
            )

    def test_force_without_reason_raises_valueerror(self) -> None:
        bars = [bad_bar(date(2026, 1, 2))]
        with self.assertRaises(ValueError):
            self.validator.validate(
                "snap-1", "NIFTYBEES", bars, [], expected_start=date(2026, 1, 2), expected_end=date(2026, 1, 2),
                force=True,
            )

    def test_force_with_reason_bypasses_halt(self) -> None:
        bars = [bad_bar(date(2026, 1, 2))]
        report = self.validator.validate(
            "snap-1", "NIFTYBEES", bars, [], expected_start=date(2026, 1, 2), expected_end=date(2026, 1, 2),
            force=True, force_reason="Known false positive, verified manually against NSE website.",
        )
        # The issue is still recorded in the report — force doesn't erase
        # history, it only prevents the halt.
        self.assertTrue(report.has_critical)

    def test_empty_data_skips_secondary_checks(self) -> None:
        report = self.validator.validate(
            "snap-1", "NIFTYBEES", [], [], expected_start=date(2026, 1, 2), expected_end=date(2026, 1, 2),
            force=True, force_reason="testing empty-data short-circuit",
        )
        # Only the no_data issue should be present, not e.g. spurious
        # missing_trading_day issues for a range with zero bars.
        self.assertEqual(len(report.issues), 1)
        self.assertEqual(report.issues[0].check_name, "no_data")


if __name__ == "__main__":
    unittest.main()
