"""Unit tests for UniverseScreeningEngine."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFMetadata, ScreeningStatus, ScreeningThresholds
from etf_platform.etf_optimizer.screening_engine import UniverseScreeningEngine


def make_bars(n: int, close: float = 100.0, volume: int = 100000, start: date = date(2025, 1, 1)) -> list[OHLCVBar]:
    return [
        OHLCVBar("X", start + timedelta(days=i), close, close + 1, close - 1, close, volume)
        for i in range(n)
    ]


def make_metadata(**overrides) -> ETFMetadata:
    defaults = dict(symbol="X", name="X Fund", exchange="NSE")
    defaults.update(overrides)
    return ETFMetadata(**defaults)


class TestHistoryCheck(unittest.TestCase):
    def test_fails_when_below_minimum_history(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=100))
        result = engine.screen("X", make_metadata(), make_bars(50))
        self.assertEqual(result.overall_status, ScreeningStatus.FAIL)

    def test_passes_when_history_sufficient(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=50))
        result = engine.screen("X", make_metadata(), make_bars(100))
        self.assertEqual(result.overall_status, ScreeningStatus.PASS)

    def test_no_bars_fails(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=10))
        result = engine.screen("X", make_metadata(), [])
        self.assertEqual(result.overall_status, ScreeningStatus.FAIL)


class TestOptionalChecksOmittedWhenNoThreshold(unittest.TestCase):
    def test_aum_check_omitted_when_threshold_unset(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=10))
        result = engine.screen("X", make_metadata(aum_crores=None), make_bars(10))
        self.assertNotIn("min_aum_crores", [c.check_name for c in result.checks])


class TestAUMCheck(unittest.TestCase):
    def test_unknown_when_aum_missing(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=10, min_aum_crores=100.0))
        result = engine.screen("X", make_metadata(aum_crores=None), make_bars(10))
        self.assertEqual(result.overall_status, ScreeningStatus.UNKNOWN)

    def test_pass_when_aum_above_threshold(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=10, min_aum_crores=100.0))
        result = engine.screen("X", make_metadata(aum_crores=500.0), make_bars(10))
        self.assertEqual(result.overall_status, ScreeningStatus.PASS)

    def test_fail_when_aum_below_threshold(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=10, min_aum_crores=100.0))
        result = engine.screen("X", make_metadata(aum_crores=50.0), make_bars(10))
        self.assertEqual(result.overall_status, ScreeningStatus.FAIL)


class TestExpenseRatioCheck(unittest.TestCase):
    def test_pass_below_max(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=10, max_expense_ratio=0.01))
        result = engine.screen("X", make_metadata(expense_ratio=0.005), make_bars(10))
        self.assertEqual(result.overall_status, ScreeningStatus.PASS)

    def test_fail_above_max(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=10, max_expense_ratio=0.01))
        result = engine.screen("X", make_metadata(expense_ratio=0.02), make_bars(10))
        self.assertEqual(result.overall_status, ScreeningStatus.FAIL)


class TestTurnoverCheck(unittest.TestCase):
    def test_fail_low_turnover(self) -> None:
        engine = UniverseScreeningEngine(
            ScreeningThresholds(min_trading_days_history=10, min_avg_daily_turnover_inr=1_000_000)
        )
        bars = make_bars(10, close=10.0, volume=100)  # turnover ~1000, way below 1M
        result = engine.screen("X", make_metadata(), bars)
        self.assertEqual(result.overall_status, ScreeningStatus.FAIL)

    def test_pass_high_turnover(self) -> None:
        engine = UniverseScreeningEngine(
            ScreeningThresholds(min_trading_days_history=10, min_avg_daily_turnover_inr=1_000_000)
        )
        bars = make_bars(10, close=100.0, volume=50000)  # turnover 5,000,000
        result = engine.screen("X", make_metadata(), bars)
        self.assertEqual(result.overall_status, ScreeningStatus.PASS)


class TestMixedStatusPriority(unittest.TestCase):
    def test_fail_takes_priority_over_unknown(self) -> None:
        # min_trading_days_history FAILs, AND aum is UNKNOWN — overall must be FAIL, not UNKNOWN.
        engine = UniverseScreeningEngine(
            ScreeningThresholds(min_trading_days_history=1000, min_aum_crores=100.0)
        )
        result = engine.screen("X", make_metadata(aum_crores=None), make_bars(10))
        self.assertEqual(result.overall_status, ScreeningStatus.FAIL)

    def test_explainable_failure_reasons(self) -> None:
        engine = UniverseScreeningEngine(ScreeningThresholds(min_trading_days_history=1000))
        result = engine.screen("X", make_metadata(), make_bars(10))
        reasons = result.failure_reasons()
        self.assertEqual(len(reasons), 1)
        self.assertIn("10 trading days", reasons[0])


if __name__ == "__main__":
    unittest.main()
