"""Unit tests for WalkForwardValidator."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.backtesting.models import BacktestConfig, OrderIntent, OrderType
from etf_platform.backtesting.strategy import Strategy
from etf_platform.cost_tax_engine import Side
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.validation.walk_forward import WalkForwardValidator


def make_bars(closes, start=date(2024, 1, 1)):
    return [
        OHLCVBar("X", start + timedelta(days=i), c - 0.3, c + 0.3, c - 0.6, c, 50000)
        for i, c in enumerate(closes)
    ]


class NoOpStrategy(Strategy):
    def generate_orders(self, as_of_date, history, portfolio):
        return []


class BuyOnceStrategy(Strategy):
    def __init__(self):
        self.bought = False

    def generate_orders(self, as_of_date, history, portfolio):
        if not self.bought and portfolio.cash > 50000:
            self.bought = True
            return [OrderIntent("X", Side.BUY, OrderType.MARKET, 10, "walk-forward test buy")]
        return []


class TestGenerateWindows(unittest.TestCase):
    def test_rolling_windows_non_overlapping_by_default(self) -> None:
        validator = WalkForwardValidator(train_days=100, test_days=30)
        windows = validator.generate_windows(date(2024, 1, 1), date(2024, 12, 31))
        self.assertGreater(len(windows), 0)
        for w1, w2 in zip(windows, windows[1:]):
            self.assertEqual(w2.test_start, w1.test_end)

    def test_rolling_train_window_is_fixed_size(self) -> None:
        validator = WalkForwardValidator(train_days=100, test_days=30)
        windows = validator.generate_windows(date(2024, 1, 1), date(2024, 12, 31))
        for w in windows:
            self.assertEqual((w.train_end - w.train_start).days, 99)  # train_days-1 inclusive span

    def test_expanding_train_start_always_fixed(self) -> None:
        validator = WalkForwardValidator(train_days=100, test_days=30, expanding=True)
        windows = validator.generate_windows(date(2024, 1, 1), date(2024, 12, 31))
        for w in windows:
            self.assertEqual(w.train_start, date(2024, 1, 1))

    def test_too_short_range_gives_no_windows(self) -> None:
        validator = WalkForwardValidator(train_days=300, test_days=100)
        windows = validator.generate_windows(date(2024, 1, 1), date(2024, 3, 1))
        self.assertEqual(windows, [])

    def test_invalid_window_sizes_raise(self) -> None:
        with self.assertRaises(ValueError):
            WalkForwardValidator(train_days=0, test_days=30)
        with self.assertRaises(ValueError):
            WalkForwardValidator(train_days=30, test_days=-1)


class TestWalkForwardRun(unittest.TestCase):
    def setUp(self) -> None:
        closes = [100 + i * 0.05 for i in range(400)]
        self.bars = {"X": make_bars(closes)}
        self.base_config = BacktestConfig(
            start_date=date(2024, 1, 1), end_date=date(2025, 2, 4),
            initial_capital=100000, symbols=("X",),
        )

    def test_no_op_strategy_produces_flat_reports(self) -> None:
        validator = WalkForwardValidator(train_days=60, test_days=30)
        result = validator.run(self.bars, lambda: NoOpStrategy(), self.base_config)
        self.assertGreater(len(result.windows), 0)
        self.assertEqual(len(result.out_of_sample_results), len(result.windows))
        for report in result.per_window_reports:
            if report is not None:
                self.assertAlmostEqual(report.final_value, self.base_config.initial_capital, places=2)

    def test_each_window_gets_fresh_strategy_state(self) -> None:
        """If state leaked between windows, BuyOnceStrategy would only ever
        buy in the FIRST window (its `bought` flag would stay True
        forever). Fresh state per window means every window should trigger
        exactly one buy."""
        validator = WalkForwardValidator(train_days=60, test_days=30)
        result = validator.run(self.bars, lambda: BuyOnceStrategy(), self.base_config)
        for backtest_result in result.out_of_sample_results:
            self.assertEqual(len(backtest_result.trades), 1)

    def test_summary_statistics_computed(self) -> None:
        validator = WalkForwardValidator(train_days=60, test_days=30)
        result = validator.run(self.bars, lambda: BuyOnceStrategy(), self.base_config)
        self.assertEqual(result.summary.num_windows, len(result.windows))
        self.assertIsNotNone(result.summary.mean_xirr)

    def test_no_windows_raises(self) -> None:
        validator = WalkForwardValidator(train_days=1000, test_days=1000)
        with self.assertRaises(ValueError):
            validator.run(self.bars, lambda: NoOpStrategy(), self.base_config)


if __name__ == "__main__":
    unittest.main()
