"""Unit tests for Portfolio — cash-flow sign correctness is the highest-risk
part of this class (see portfolio.py's module docstring)."""

from __future__ import annotations

import unittest
from datetime import date

from etf_platform.backtesting.models import Fill, OrderType
from etf_platform.backtesting.portfolio import InsufficientCashError, OverSellError, Portfolio
from etf_platform.cost_tax_engine import CostTaxEngine, Side


def make_fill(side, price, qty, cost_tax_engine, order_type=OrderType.MARKET, d=date(2025, 1, 2)) -> Fill:
    cost = cost_tax_engine.compute_transaction_cost(side, price, qty)
    return Fill(
        symbol="X", side=side, order_type=order_type, quantity=qty, fill_price=price,
        fill_date=d, decided_date=date(2025, 1, 1), cost=cost, rationale="test",
    )


class TestBuyCashFlow(unittest.TestCase):
    def test_buy_reduces_cash_by_gross_plus_cost(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100000, cost_tax_engine=cte)
        fill = make_fill(Side.BUY, price=100, qty=10, cost_tax_engine=cte)
        portfolio.apply_fill(fill)
        expected_cash = 100000 - (fill.cost.gross_amount + fill.cost.total_cost)
        self.assertAlmostEqual(portfolio.cash, expected_cash, places=6)

    def test_buy_increases_position(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100000, cost_tax_engine=cte)
        portfolio.apply_fill(make_fill(Side.BUY, price=100, qty=10, cost_tax_engine=cte))
        self.assertEqual(portfolio.position("X"), 10)

    def test_buy_exceeding_cash_raises(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100, cost_tax_engine=cte)
        fill = make_fill(Side.BUY, price=100, qty=10, cost_tax_engine=cte)
        with self.assertRaises(InsufficientCashError):
            portfolio.apply_fill(fill)

    def test_can_afford_buy_check_before_applying(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100, cost_tax_engine=cte)
        fill = make_fill(Side.BUY, price=100, qty=10, cost_tax_engine=cte)
        self.assertFalse(portfolio.can_afford_buy(fill))


class TestSellCashFlow(unittest.TestCase):
    def test_sell_increases_cash_by_gross_minus_cost(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100000, cost_tax_engine=cte)
        buy_fill = make_fill(Side.BUY, price=100, qty=10, cost_tax_engine=cte, d=date(2025, 1, 2))
        portfolio.apply_fill(buy_fill)
        cash_after_buy = portfolio.cash

        sell_fill = make_fill(Side.SELL, price=110, qty=10, cost_tax_engine=cte, d=date(2026, 6, 1))
        portfolio.apply_fill(sell_fill)
        expected_cash = cash_after_buy + (sell_fill.cost.gross_amount - sell_fill.cost.total_cost)
        self.assertAlmostEqual(portfolio.cash, expected_cash, places=6)

    def test_sell_reduces_position(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100000, cost_tax_engine=cte)
        portfolio.apply_fill(make_fill(Side.BUY, price=100, qty=10, cost_tax_engine=cte, d=date(2025, 1, 2)))
        portfolio.apply_fill(make_fill(Side.SELL, price=110, qty=4, cost_tax_engine=cte, d=date(2025, 2, 1)))
        self.assertEqual(portfolio.position("X"), 6)

    def test_oversell_raises(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100000, cost_tax_engine=cte)
        portfolio.apply_fill(make_fill(Side.BUY, price=100, qty=5, cost_tax_engine=cte, d=date(2025, 1, 2)))
        oversell_fill = make_fill(Side.SELL, price=110, qty=10, cost_tax_engine=cte, d=date(2025, 2, 1))
        with self.assertRaises(OverSellError):
            portfolio.apply_fill(oversell_fill)

    def test_sell_produces_realized_gain(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100000, cost_tax_engine=cte)
        portfolio.apply_fill(make_fill(Side.BUY, price=100, qty=10, cost_tax_engine=cte, d=date(2025, 1, 2)))
        trade = portfolio.apply_fill(make_fill(Side.SELL, price=120, qty=10, cost_tax_engine=cte, d=date(2025, 6, 1)))
        self.assertEqual(len(trade.realized_gains), 1)
        self.assertAlmostEqual(trade.realized_gains[0].gross_gain, 200.0)


class TestEquityCurvePoint(unittest.TestCase):
    def test_values_positions_at_given_prices(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100000, cost_tax_engine=cte)
        portfolio.apply_fill(make_fill(Side.BUY, price=100, qty=10, cost_tax_engine=cte, d=date(2025, 1, 2)))
        point = portfolio.equity_curve_point(date(2025, 1, 5), {"X": 105.0})
        self.assertAlmostEqual(point.positions_value, 1050.0)
        self.assertAlmostEqual(point.total_value, point.cash + 1050.0)

    def test_missing_price_values_position_at_zero_with_warning(self) -> None:
        cte = CostTaxEngine()
        portfolio = Portfolio(initial_capital=100000, cost_tax_engine=cte)
        portfolio.apply_fill(make_fill(Side.BUY, price=100, qty=10, cost_tax_engine=cte, d=date(2025, 1, 2)))
        point = portfolio.equity_curve_point(date(2025, 1, 5), {})  # no price for X
        self.assertEqual(point.positions_value, 0.0)


if __name__ == "__main__":
    unittest.main()
