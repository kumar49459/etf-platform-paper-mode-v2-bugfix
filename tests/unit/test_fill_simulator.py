"""Unit tests for FillSimulator — market/limit fill logic against next-bar OHLC."""

from __future__ import annotations

import unittest
from datetime import date

from etf_platform.backtesting.fill_simulator import FillSimulator
from etf_platform.backtesting.models import OrderIntent, OrderType, PendingOrder
from etf_platform.cost_tax_engine import CostTaxEngine, Side
from etf_platform.data_engine.models import OHLCVBar


def bar(o, h, l, c, d=date(2025, 1, 2)) -> OHLCVBar:
    return OHLCVBar("X", d, o, h, l, c, 100000)


def pending(side, order_type, qty=10, limit_price=None, decided=date(2025, 1, 1), target=date(2025, 1, 2)):
    intent = OrderIntent("X", side, order_type, qty, "test rationale", limit_price)
    return PendingOrder(intent=intent, decided_date=decided, target_fill_date=target, expiry_date=date(2025, 1, 10))


class TestMarketFills(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FillSimulator(CostTaxEngine())

    def test_market_buy_fills_at_open(self) -> None:
        b = bar(o=105, h=107, l=104, c=106)
        fill = self.sim.try_fill(pending(Side.BUY, OrderType.MARKET), b)
        self.assertIsNotNone(fill)
        self.assertEqual(fill.fill_price, 105)

    def test_market_sell_fills_at_open(self) -> None:
        b = bar(o=105, h=107, l=104, c=106)
        fill = self.sim.try_fill(pending(Side.SELL, OrderType.MARKET), b)
        self.assertEqual(fill.fill_price, 105)


class TestLimitBuyFills(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FillSimulator(CostTaxEngine())

    def test_limit_buy_fills_when_low_touches_limit(self) -> None:
        b = bar(o=105, h=107, l=99, c=103)  # low dips to 99, limit 100 -> should fill
        fill = self.sim.try_fill(pending(Side.BUY, OrderType.LIMIT, limit_price=100), b)
        self.assertIsNotNone(fill)

    def test_limit_buy_does_not_fill_when_low_above_limit(self) -> None:
        b = bar(o=105, h=107, l=101, c=103)  # low never reaches 100
        fill = self.sim.try_fill(pending(Side.BUY, OrderType.LIMIT, limit_price=100), b)
        self.assertIsNone(fill)

    def test_limit_buy_fills_at_favorable_gap_down_open(self) -> None:
        # Gaps open at 95, below the 100 limit — should fill at the BETTER
        # price (95), not at the limit price.
        b = bar(o=95, h=98, l=94, c=96)
        fill = self.sim.try_fill(pending(Side.BUY, OrderType.LIMIT, limit_price=100), b)
        self.assertEqual(fill.fill_price, 95)

    def test_limit_buy_fills_at_limit_when_touched_but_not_gapped(self) -> None:
        b = bar(o=105, h=107, l=99, c=103)  # opens above limit, dips down to touch it
        fill = self.sim.try_fill(pending(Side.BUY, OrderType.LIMIT, limit_price=100), b)
        self.assertEqual(fill.fill_price, 100)


class TestLimitSellFills(unittest.TestCase):
    def setUp(self) -> None:
        self.sim = FillSimulator(CostTaxEngine())

    def test_limit_sell_fills_when_high_touches_limit(self) -> None:
        b = bar(o=95, h=101, l=94, c=98)  # high reaches 101, limit 100 -> should fill
        fill = self.sim.try_fill(pending(Side.SELL, OrderType.LIMIT, limit_price=100), b)
        self.assertIsNotNone(fill)

    def test_limit_sell_does_not_fill_when_high_below_limit(self) -> None:
        b = bar(o=95, h=98, l=94, c=96)
        fill = self.sim.try_fill(pending(Side.SELL, OrderType.LIMIT, limit_price=100), b)
        self.assertIsNone(fill)

    def test_limit_sell_fills_at_favorable_gap_up_open(self) -> None:
        b = bar(o=110, h=112, l=109, c=111)  # gaps up above the 100 limit
        fill = self.sim.try_fill(pending(Side.SELL, OrderType.LIMIT, limit_price=100), b)
        self.assertEqual(fill.fill_price, 110)


class TestFillIncludesCost(unittest.TestCase):
    def test_fill_carries_cost_breakdown(self) -> None:
        sim = FillSimulator(CostTaxEngine())
        b = bar(o=105, h=107, l=104, c=106)
        fill = sim.try_fill(pending(Side.BUY, OrderType.MARKET, qty=100), b)
        self.assertGreater(fill.cost.total_cost, 0)
        self.assertEqual(fill.cost.gross_amount, 105 * 100)


if __name__ == "__main__":
    unittest.main()
