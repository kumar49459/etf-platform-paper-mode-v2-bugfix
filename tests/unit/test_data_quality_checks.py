"""Unit tests for individual data quality checks, using synthetic bars."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.data_engine.models import CorporateAction, CorporateActionType, OHLCVBar
from etf_platform.data_quality import checks
from etf_platform.data_quality.models import Severity


def make_bar(d: date, o=100.0, h=101.0, l=99.0, c=100.5, v=1000) -> OHLCVBar:
    return OHLCVBar(symbol="NIFTYBEES", trade_date=d, open=o, high=h, low=l, close=c, volume=v)


class TestNoDataCheck(unittest.TestCase):
    def test_empty_bars_flagged_critical(self) -> None:
        issues = checks.check_no_data("NIFTYBEES", [])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.CRITICAL)

    def test_nonempty_bars_no_issue(self) -> None:
        issues = checks.check_no_data("NIFTYBEES", [make_bar(date(2026, 1, 2))])
        self.assertEqual(issues, [])


class TestOHLCConsistency(unittest.TestCase):
    def test_valid_bar_no_issue(self) -> None:
        bar = make_bar(date(2026, 1, 2), o=100, h=101, l=99, c=100.5)
        self.assertEqual(checks.check_ohlc_consistency("SYM", [bar]), [])

    def test_low_greater_than_high_is_critical(self) -> None:
        bar = make_bar(date(2026, 1, 2), o=100, h=99, l=101, c=100)
        issues = checks.check_ohlc_consistency("SYM", [bar])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.CRITICAL)

    def test_open_outside_range_is_critical(self) -> None:
        bar = make_bar(date(2026, 1, 2), o=200, h=101, l=99, c=100)
        issues = checks.check_ohlc_consistency("SYM", [bar])
        self.assertTrue(any("open" in i.message for i in issues))

    def test_close_outside_range_is_critical(self) -> None:
        bar = make_bar(date(2026, 1, 2), o=100, h=101, l=99, c=200)
        issues = checks.check_ohlc_consistency("SYM", [bar])
        self.assertTrue(any("close" in i.message for i in issues))


class TestNegativeOrZero(unittest.TestCase):
    def test_negative_price_is_critical(self) -> None:
        bar = make_bar(date(2026, 1, 2), o=-5, h=101, l=99, c=100)
        issues = checks.check_negative_or_zero("SYM", [bar])
        self.assertTrue(any(i.severity == Severity.CRITICAL for i in issues))

    def test_zero_volume_is_warning_not_critical(self) -> None:
        bar = make_bar(date(2026, 1, 2), v=0)
        issues = checks.check_negative_or_zero("SYM", [bar])
        volume_issues = [i for i in issues if i.check_name == "zero_volume"]
        self.assertEqual(len(volume_issues), 1)
        self.assertEqual(volume_issues[0].severity, Severity.WARNING)


class TestDuplicates(unittest.TestCase):
    def test_duplicate_date_flagged_critical(self) -> None:
        d = date(2026, 1, 2)
        issues = checks.check_duplicates("SYM", [make_bar(d), make_bar(d)])
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.CRITICAL)

    def test_no_duplicates_no_issue(self) -> None:
        bars = [make_bar(date(2026, 1, 2)), make_bar(date(2026, 1, 3))]
        self.assertEqual(checks.check_duplicates("SYM", bars), [])


class TestPriceJump(unittest.TestCase):
    def test_large_unexplained_jump_is_critical(self) -> None:
        bars = [
            make_bar(date(2026, 1, 2), c=100),
            make_bar(date(2026, 1, 3), o=150, h=151, l=149, c=150),  # +50%
        ]
        issues = checks.check_price_jump("SYM", bars, corporate_actions=[], max_jump_pct=20.0)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.CRITICAL)

    def test_jump_explained_by_corporate_action_is_info_not_critical(self) -> None:
        jump_date = date(2026, 1, 3)
        bars = [
            make_bar(date(2026, 1, 2), c=100),
            make_bar(jump_date, o=50, h=51, l=49, c=50),  # -50%, e.g. a split
        ]
        action = CorporateAction(
            symbol="SYM", ex_date=jump_date, action_type=CorporateActionType.SPLIT, ratio_or_amount=2.0
        )
        issues = checks.check_price_jump("SYM", bars, corporate_actions=[action], max_jump_pct=20.0)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].severity, Severity.INFO)

    def test_small_move_no_issue(self) -> None:
        bars = [make_bar(date(2026, 1, 2), c=100), make_bar(date(2026, 1, 3), o=101, h=102, l=100, c=101)]
        issues = checks.check_price_jump("SYM", bars, corporate_actions=[], max_jump_pct=20.0)
        self.assertEqual(issues, [])


class TestStalePrice(unittest.TestCase):
    def test_long_flat_run_flagged_warning(self) -> None:
        start = date(2026, 1, 1)
        bars = [make_bar(start + timedelta(days=i), c=100.0) for i in range(15)]
        issues = checks.check_stale_price("SYM", bars, max_stale_days=10)
        self.assertTrue(any(i.severity == Severity.WARNING for i in issues))

    def test_normal_variation_no_issue(self) -> None:
        start = date(2026, 1, 1)
        bars = [make_bar(start + timedelta(days=i), c=100.0 + i) for i in range(15)]
        issues = checks.check_stale_price("SYM", bars, max_stale_days=10)
        self.assertEqual(issues, [])


class TestMissingTradingDays(unittest.TestCase):
    def test_missing_weekday_flagged(self) -> None:
        # Mon Jan 5 2026 and Wed Jan 7 2026 present, Tue Jan 6 missing.
        bars = [make_bar(date(2026, 1, 5)), make_bar(date(2026, 1, 7))]
        issues = checks.check_missing_trading_days(
            "SYM", bars, start=date(2026, 1, 5), end=date(2026, 1, 7)
        )
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].trade_date, date(2026, 1, 6))

    def test_holiday_excluded_from_gap_detection(self) -> None:
        bars = [make_bar(date(2026, 1, 5)), make_bar(date(2026, 1, 7))]
        issues = checks.check_missing_trading_days(
            "SYM", bars, start=date(2026, 1, 5), end=date(2026, 1, 7),
            holidays=frozenset({date(2026, 1, 6)}),
        )
        self.assertEqual(issues, [])

    def test_weekend_not_flagged(self) -> None:
        # Fri Jan 2 2026, Mon Jan 5 2026 present; Sat/Sun in between should
        # not be flagged as missing trading days.
        bars = [make_bar(date(2026, 1, 2)), make_bar(date(2026, 1, 5))]
        issues = checks.check_missing_trading_days(
            "SYM", bars, start=date(2026, 1, 2), end=date(2026, 1, 5)
        )
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
