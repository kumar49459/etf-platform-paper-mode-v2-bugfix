"""Regression tests for every issue found during the Phase 4 adversarial
review (see CHANGELOG.md for the full list). Each test corresponds to a
specific weakness that was found, fixed, and must never silently
regress.
"""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.backtesting import BacktestConfig, BacktestEngine, OrderIntent, OrderType, Strategy
from etf_platform.backtesting.exceptions import InvalidOrderError
from etf_platform.cost_tax_engine import CostTaxEngine, Side
from etf_platform.data_engine.models import CorporateAction, CorporateActionType, OHLCVBar


def make_bars(symbol: str, closes: list[float], start: date, volume: int = 50000) -> list[OHLCVBar]:
    return [
        OHLCVBar(symbol, start + timedelta(days=i), c - 0.3, c + 0.3, c - 0.6, c, volume)
        for i, c in enumerate(closes)
    ]


class BuyOnceStrategy(Strategy):
    def __init__(self, symbol="X", qty=10):
        self.done = False
        self.symbol = symbol
        self.qty = qty

    def generate_orders(self, as_of_date, history, portfolio):
        if not self.done and portfolio.cash > 50000:
            self.done = True
            return [OrderIntent(self.symbol, Side.BUY, OrderType.MARKET, self.qty, "regression test buy")]
        return []


class TestFractionalQuantityRejected(unittest.TestCase):
    def test_fractional_quantity_raises(self):
        with self.assertRaises(InvalidOrderError):
            OrderIntent("X", Side.BUY, OrderType.MARKET, 3.7, "fractional quantity")

    def test_whole_number_quantity_accepted(self):
        intent = OrderIntent("X", Side.BUY, OrderType.MARKET, 10.0, "whole quantity")
        self.assertEqual(intent.quantity, 10.0)


class TestStalePriceWarning(unittest.TestCase):
    def test_warning_emitted_when_price_goes_stale(self):
        closes_x = [100 + i * 2 for i in range(20)]
        closes_y = [50 + i * 0.1 for i in range(100)]
        bars = {
            "X": make_bars("X", closes_x, date(2025, 1, 1)),
            "Y": make_bars("Y", closes_y, date(2025, 1, 1)),
        }

        class BuyBoth(Strategy):
            def __init__(self):
                self.done = False

            def generate_orders(self, as_of_date, history, portfolio):
                if not self.done and portfolio.cash > 50000:
                    self.done = True
                    return [
                        OrderIntent("X", Side.BUY, OrderType.MARKET, 10, "buy X"),
                        OrderIntent("Y", Side.BUY, OrderType.MARKET, 10, "buy Y"),
                    ]
                return []

        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 4, 10), initial_capital=100000, symbols=("X", "Y")
        )
        result = BacktestEngine(config, BuyBoth()).run(bars)
        self.assertTrue(any("stale" in w.lower() or "no price update" in w.lower() for w in result.warnings))

    def test_no_warning_when_data_is_current(self):
        bars = {"X": make_bars("X", [100 + i * 0.1 for i in range(100)], date(2025, 1, 1))}
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 4, 5), initial_capital=100000, symbols=("X",)
        )
        result = BacktestEngine(config, BuyOnceStrategy()).run(bars)
        self.assertEqual(result.warnings, [])


class TestEarlyTerminationWarning(unittest.TestCase):
    def test_warning_when_data_ends_before_configured_end_date(self):
        bars = {"X": make_bars("X", [100 + i * 2 for i in range(20)], date(2025, 1, 1))}
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 4, 10), initial_capital=100000, symbols=("X",)
        )
        result = BacktestEngine(config, BuyOnceStrategy()).run(bars)

        self.assertEqual(result.actual_end_date, date(2025, 1, 20))
        self.assertTrue(any("ended" in w.lower() for w in result.warnings))

    def test_no_warning_when_data_covers_full_range(self):
        bars = {"X": make_bars("X", [100 + i * 0.1 for i in range(100)], date(2025, 1, 1))}
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 4, 5), initial_capital=100000, symbols=("X",)
        )
        result = BacktestEngine(config, BuyOnceStrategy()).run(bars)
        self.assertEqual(result.actual_end_date, config.end_date)
        self.assertEqual(result.warnings, [])


class TestDividendHandling(unittest.TestCase):
    def test_dividend_credits_cash_based_on_held_quantity(self):
        bars = {"X": make_bars("X", [100 + i * 0.1 for i in range(150)], date(2025, 1, 1))}
        ex_date = date(2025, 1, 1) + timedelta(days=50)
        actions = {"X": [CorporateAction("X", ex_date, CorporateActionType.DIVIDEND, 2.0)]}

        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 1) + timedelta(days=149),
            initial_capital=100000, symbols=("X",),
        )
        engine = BacktestEngine(config, BuyOnceStrategy(qty=100))
        result = engine.run(bars, corporate_actions_by_symbol=actions)

        self.assertEqual(len(result.dividend_receipts), 1)
        receipt = result.dividend_receipts[0]
        self.assertEqual(receipt.quantity_held, 100)
        self.assertAlmostEqual(receipt.total_cash_credited, 200.0)

    def test_no_dividend_credited_if_no_position_held(self):
        bars = {"X": make_bars("X", [100.0] * 50, date(2025, 1, 1))}
        actions = {"X": [CorporateAction("X", date(2025, 1, 10), CorporateActionType.DIVIDEND, 2.0)]}

        class NoOp(Strategy):
            def generate_orders(self, as_of_date, history, portfolio):
                return []

        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 1) + timedelta(days=49),
            initial_capital=100000, symbols=("X",),
        )
        result = BacktestEngine(config, NoOp()).run(bars, corporate_actions_by_symbol=actions)
        self.assertEqual(result.dividend_receipts, [])


class TestCorporateActionSplitBonus(unittest.TestCase):
    def test_bonus_doubles_held_quantity(self):
        bars = {"X": make_bars("X", [100 + i * 0.1 for i in range(150)], date(2025, 1, 1))}
        ex_date = date(2025, 1, 1) + timedelta(days=100)
        actions = {"X": [CorporateAction("X", ex_date, CorporateActionType.BONUS, 2.0)]}

        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 1) + timedelta(days=149),
            initial_capital=100000, symbols=("X",),
        )
        engine = BacktestEngine(config, BuyOnceStrategy(qty=100))
        result = engine.run(bars, corporate_actions_by_symbol=actions)

        self.assertEqual(len(result.corporate_action_events), 1)
        event = result.corporate_action_events[0]
        self.assertEqual(event.quantity_before, 100)
        self.assertEqual(event.quantity_after, 200)

    def test_split_preserves_original_tax_lot_acquisition_date(self):
        """The critical correctness property: a bonus/split must NOT reset
        the holding period used for STCG/LTCG classification."""
        bars = {"X": make_bars("X", [100 + i * 0.1 for i in range(150)], date(2025, 1, 1))}
        ex_date = date(2025, 1, 1) + timedelta(days=100)
        actions = {"X": [CorporateAction("X", ex_date, CorporateActionType.BONUS, 2.0)]}

        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 1) + timedelta(days=149),
            initial_capital=100000, symbols=("X",),
        )
        cte = CostTaxEngine()
        engine = BacktestEngine(config, BuyOnceStrategy(qty=100), cte)
        engine.run(bars, corporate_actions_by_symbol=actions)

        lots = cte.open_lots("X")
        self.assertEqual(len(lots), 1)
        self.assertEqual(lots[0].buy_date, date(2025, 1, 2))
        self.assertNotEqual(lots[0].buy_date, ex_date)

    def test_split_preserves_total_cost_basis(self):
        # Flat bars with open == close (no O/C spread) so the fill price is
        # unambiguous and the expected cost basis isn't a guess.
        bars = {
            "X": [
                OHLCVBar("X", date(2025, 1, 1) + timedelta(days=i), 100.0, 100.5, 99.5, 100.0, 50000)
                for i in range(150)
            ]
        }
        ex_date = date(2025, 1, 1) + timedelta(days=50)
        actions = {"X": [CorporateAction("X", ex_date, CorporateActionType.SPLIT, 2.0)]}

        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 1) + timedelta(days=149),
            initial_capital=100000, symbols=("X",),
        )
        cte = CostTaxEngine()
        engine = BacktestEngine(config, BuyOnceStrategy(qty=100), cte)
        engine.run(bars, corporate_actions_by_symbol=actions)

        lots = cte.open_lots("X")
        total_cost_basis = sum(lot.quantity * lot.buy_price for lot in lots)
        self.assertAlmostEqual(total_cost_basis, 100 * 100.0, places=4)

    def test_unhandled_corporate_action_type_produces_explicit_warning(self):
        bars = {"X": make_bars("X", [100.0] * 50, date(2025, 1, 1))}
        actions = {"X": [CorporateAction("X", date(2025, 1, 10), CorporateActionType.MERGER, 1.0)]}
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 1) + timedelta(days=49),
            initial_capital=100000, symbols=("X",),
        )
        result = BacktestEngine(config, BuyOnceStrategy()).run(bars, corporate_actions_by_symbol=actions)
        self.assertTrue(any("merger" in w.lower() or "unhandled" in w.lower() for w in result.warnings))


class TestVolumeParticipationPartialFills(unittest.TestCase):
    def test_large_order_fills_partially_across_multiple_days(self):
        bars = {"X": [OHLCVBar("X", date(2025, 1, 1) + timedelta(days=i), 100, 101, 99, 100, 1000) for i in range(30)]}

        class BuyBig(Strategy):
            def __init__(self):
                self.done = False

            def generate_orders(self, as_of_date, history, portfolio):
                if not self.done:
                    self.done = True
                    return [OrderIntent("X", Side.BUY, OrderType.MARKET, 5000, "large order")]
                return []

        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 29), initial_capital=10_000_000,
            symbols=("X",), max_volume_participation_pct=0.1, limit_order_expiry_days=60,
        )
        result = BacktestEngine(config, BuyBig()).run(bars)

        self.assertGreater(len(result.trades), 1)
        for t in result.trades:
            self.assertLessEqual(t.fill.quantity, 100)

    def test_unfilled_remainder_expires_cleanly_with_readable_rationale(self):
        bars = {"X": [OHLCVBar("X", date(2025, 1, 1) + timedelta(days=i), 100, 101, 99, 100, 1000) for i in range(30)]}

        class BuyBig(Strategy):
            def __init__(self):
                self.done = False

            def generate_orders(self, as_of_date, history, portfolio):
                if not self.done:
                    self.done = True
                    return [OrderIntent("X", Side.BUY, OrderType.MARKET, 5000, "large order")]
                return []

        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 29), initial_capital=10_000_000,
            symbols=("X",), max_volume_participation_pct=0.1,
        )
        result = BacktestEngine(config, BuyBig()).run(bars)

        self.assertEqual(len(result.rejected_orders), 1)
        rejected_intent, reason = result.rejected_orders[0]
        self.assertEqual(rejected_intent.rationale, "large order")
        self.assertIn("expired", reason.lower())
        self.assertIn("Partially filled", reason)


class TestBarsSanityCheck(unittest.TestCase):
    """Finding: the engine would silently process obviously invalid price
    data (e.g. negative prices from a caller who bypassed the Data Quality
    Validator) and produce nonsensical results with no error raised."""

    def test_negative_price_bar_raises(self):
        bars = {
            "X": [OHLCVBar("X", date(2025, 1, 1) + timedelta(days=i), 100, 101, 99, 100, 1000) for i in range(5)]
            + [OHLCVBar("X", date(2025, 1, 6), -10, -5, -15, -8, 1000)]
        }
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 10), initial_capital=100000, symbols=("X",)
        )
        with self.assertRaises(InvalidOrderError):
            BacktestEngine(config, BuyOnceStrategy()).run(bars)

    def test_low_greater_than_high_raises(self):
        bars = {"X": [OHLCVBar("X", date(2025, 1, 1), 100, 90, 110, 95, 1000)]}  # low > high
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 1, 10), initial_capital=100000, symbols=("X",)
        )
        with self.assertRaises(InvalidOrderError):
            BacktestEngine(config, BuyOnceStrategy()).run(bars)

    def test_valid_data_passes_sanity_check(self):
        bars = {"X": make_bars("X", [100 + i * 0.1 for i in range(50)], date(2025, 1, 1))}
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 2, 19), initial_capital=100000, symbols=("X",)
        )
        result = BacktestEngine(config, BuyOnceStrategy()).run(bars)  # must not raise
        self.assertGreaterEqual(len(result.equity_curve), 1)


if __name__ == "__main__":
    unittest.main()
