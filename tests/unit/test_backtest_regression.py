"""Regression test locking in exact backtest output for a fixed,
deterministic scenario (Phase 1 §13.4's CI gate concept: "re-runs a locked
historical scenario and fails the deploy if results drift beyond a defined
tolerance — this catches the case where a 'small refactor' silently changes
strategy behavior").

The expected values below were captured by running this exact scenario
once and hand-verified against the trade log (see the fill dates/prices —
Jan 2 is the day after the Jan 1 decision, Feb 21 is the day after the
day-count-50 decision from Jan 1). If this test ever fails, do not just
update the expected numbers to make it pass — first understand whether the
change was intentional.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.backtesting import BacktestConfig, BacktestEngine, OrderIntent, OrderType, Strategy
from etf_platform.cost_tax_engine import CostTaxEngine, IndiaEquityCostConfig, Side
from etf_platform.data_engine.models import OHLCVBar


def _make_bars(closes: list[float], start: date) -> list[OHLCVBar]:
    return [
        OHLCVBar("NIFTYBEES", start + timedelta(days=i), c - 0.3, c + 0.3, c - 0.6, c, 50000)
        for i, c in enumerate(closes)
    ]


class DeterministicRegressionStrategy(Strategy):
    """Fixed, deterministic: buy on day 0, sell half on day 50. No
    randomness anywhere — this is what makes the output locked-in-value
    testable at all."""

    def generate_orders(self, as_of_date, history, portfolio):
        day_num = (as_of_date - date(2025, 1, 1)).days
        if day_num == 0 and portfolio.cash > 50000:
            return [OrderIntent("NIFTYBEES", Side.BUY, OrderType.MARKET, 100, "Regression baseline: initial buy.")]
        if day_num == 50 and portfolio.positions.get("NIFTYBEES", 0) > 0:
            return [OrderIntent("NIFTYBEES", Side.SELL, OrderType.MARKET, 50, "Regression baseline: partial sell.")]
        return []


class TestBacktestRegressionBaseline(unittest.TestCase):
    def setUp(self) -> None:
        closes = [100 + i * 0.15 for i in range(80)]
        self.bars = {"NIFTYBEES": _make_bars(closes, date(2025, 1, 1))}
        self.config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 3, 20),
            initial_capital=100000, symbols=("NIFTYBEES",),
        )
        self.engine = BacktestEngine(
            self.config, DeterministicRegressionStrategy(), CostTaxEngine(IndiaEquityCostConfig())
        )

    def test_locked_trade_count_and_details(self) -> None:
        result = self.engine.run(self.bars)
        self.assertEqual(len(result.trades), 2)

        buy = result.trades[0]
        self.assertEqual(buy.fill.fill_date, date(2025, 1, 2))
        self.assertEqual(buy.fill.side.value, "buy")
        self.assertEqual(buy.fill.quantity, 100)
        self.assertAlmostEqual(buy.fill.fill_price, 99.85, places=2)
        self.assertAlmostEqual(buy.fill.cost.total_cost, 16.836966609999998, places=6)

        sell = result.trades[1]
        self.assertEqual(sell.fill.fill_date, date(2025, 2, 21))
        self.assertEqual(sell.fill.side.value, "sell")
        self.assertEqual(sell.fill.quantity, 50)
        self.assertAlmostEqual(sell.fill.fill_price, 107.35, places=2)
        self.assertAlmostEqual(sell.fill.cost.total_cost, 8.245693055, places=6)

    def test_locked_final_equity(self) -> None:
        result = self.engine.run(self.bars)
        final = result.equity_curve[-1]
        self.assertAlmostEqual(final.total_value, 100942.417340335, places=4)
        self.assertAlmostEqual(final.cash, 95357.417340335, places=4)
        self.assertAlmostEqual(final.positions_value, 5585.0, places=4)

    def test_locked_equity_curve_length(self) -> None:
        result = self.engine.run(self.bars)
        self.assertEqual(len(result.equity_curve), 79)

    def test_locked_no_rejected_orders(self) -> None:
        result = self.engine.run(self.bars)
        self.assertEqual(len(result.rejected_orders), 0)

    def test_deterministic_across_repeated_runs(self) -> None:
        """The engine must produce bit-identical results for identical
        inputs — any nondeterminism (unseeded randomness, dict ordering
        dependence, etc.) would be a serious correctness bug for a platform
        whose core objective is validated, reproducible backtesting."""
        result1 = self.engine.run(self.bars)

        engine2 = BacktestEngine(
            self.config, DeterministicRegressionStrategy(), CostTaxEngine(IndiaEquityCostConfig())
        )
        result2 = engine2.run(self.bars)

        self.assertEqual(result1.equity_curve[-1].total_value, result2.equity_curve[-1].total_value)
        self.assertEqual(len(result1.trades), len(result2.trades))
        for t1, t2 in zip(result1.trades, result2.trades):
            self.assertEqual(t1.fill.fill_price, t2.fill.fill_price)
            self.assertEqual(t1.fill.fill_date, t2.fill.fill_date)


if __name__ == "__main__":
    unittest.main()
