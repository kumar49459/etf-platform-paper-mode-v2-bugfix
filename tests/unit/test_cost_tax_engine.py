"""Unit tests for CostTaxEngine — cost calculations verified against
hand-computed values, not just "does it run"."""

from __future__ import annotations

import unittest
from datetime import date, timedelta

from etf_platform.cost_tax_engine import CostTaxEngine, GainType, IndiaEquityCostConfig, Side
from etf_platform.cost_tax_engine.exceptions import InsufficientLotsError


class TestCostBreakdownHandComputed(unittest.TestCase):
    def setUp(self) -> None:
        # Zero out slippage/brokerage/exchange/SEBI for this test class so
        # only STT + stamp duty + GST are in play — makes the hand
        # computation simple and unambiguous.
        self.config = IndiaEquityCostConfig(
            brokerage_pct=0.0, brokerage_flat_per_order=0.0,
            exchange_txn_charge_pct=0.0, sebi_turnover_fee_pct=0.0, slippage_bps=0.0,
        )
        self.engine = CostTaxEngine(self.config)

    def test_buy_stt_is_point_one_percent(self) -> None:
        cost = self.engine.compute_transaction_cost(Side.BUY, price=100.0, quantity=1000)
        # gross = 100,000; STT buy = 0.1% = 100.00
        self.assertAlmostEqual(cost.stt, 100.0, places=6)

    def test_sell_stt_is_point_one_percent(self) -> None:
        cost = self.engine.compute_transaction_cost(Side.SELL, price=100.0, quantity=1000)
        self.assertAlmostEqual(cost.stt, 100.0, places=6)

    def test_stamp_duty_only_on_buy(self) -> None:
        buy_cost = self.engine.compute_transaction_cost(Side.BUY, price=100.0, quantity=1000)
        sell_cost = self.engine.compute_transaction_cost(Side.SELL, price=100.0, quantity=1000)
        # gross = 100,000; stamp duty = 0.015% = 15.00
        self.assertAlmostEqual(buy_cost.stamp_duty, 15.0, places=6)
        self.assertEqual(sell_cost.stamp_duty, 0.0)

    def test_gst_zero_when_brokerage_and_charges_zero(self) -> None:
        cost = self.engine.compute_transaction_cost(Side.BUY, price=100.0, quantity=1000)
        self.assertEqual(cost.gst, 0.0)  # GST base (brokerage+exchange+SEBI) is all zero here

    def test_gst_applies_only_to_brokerage_and_exchange_charges_not_stt(self) -> None:
        config = IndiaEquityCostConfig(
            brokerage_pct=0.0, brokerage_flat_per_order=20.0,  # flat Rs.20 brokerage
            exchange_txn_charge_pct=0.0, sebi_turnover_fee_pct=0.0, slippage_bps=0.0,
        )
        engine = CostTaxEngine(config)
        cost = engine.compute_transaction_cost(Side.BUY, price=100.0, quantity=1000)
        # GST = 18% of Rs.20 brokerage = Rs.3.60 — NOT 18% of (brokerage + STT + stamp duty)
        self.assertAlmostEqual(cost.gst, 3.6, places=6)


class TestSlippage(unittest.TestCase):
    def test_slippage_scales_with_bps(self) -> None:
        config = IndiaEquityCostConfig(slippage_bps=10.0)  # 10 bps = 0.1%
        engine = CostTaxEngine(config)
        cost = engine.compute_transaction_cost(Side.BUY, price=100.0, quantity=1000)
        self.assertAlmostEqual(cost.slippage_cost, 100000 * 0.001, places=6)


class TestFIFOTaxLotMatching(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = CostTaxEngine()

    def test_single_lot_full_sell(self) -> None:
        cost = self.engine.compute_transaction_cost(Side.BUY, 100.0, 10)
        self.engine.record_buy("X", date(2025, 1, 1), 10, 100.0, cost)
        gains = self.engine.match_sell("X", date(2025, 2, 1), 10, 110.0)
        self.assertEqual(len(gains), 1)
        self.assertAlmostEqual(gains[0].gross_gain, 100.0)  # (110-100)*10

    def test_sell_spans_multiple_lots_fifo_order(self) -> None:
        cost1 = self.engine.compute_transaction_cost(Side.BUY, 100.0, 5)
        cost2 = self.engine.compute_transaction_cost(Side.BUY, 120.0, 5)
        self.engine.record_buy("X", date(2025, 1, 1), 5, 100.0, cost1)
        self.engine.record_buy("X", date(2025, 2, 1), 5, 120.0, cost2)

        gains = self.engine.match_sell("X", date(2025, 6, 1), 8, 130.0)
        # Should consume all 5 units of the FIRST lot (bought 100.0), then
        # 3 units of the SECOND lot (bought 120.0) — FIFO order.
        self.assertEqual(len(gains), 2)
        self.assertEqual(gains[0].buy_price, 100.0)
        self.assertEqual(gains[0].quantity, 5)
        self.assertEqual(gains[1].buy_price, 120.0)
        self.assertEqual(gains[1].quantity, 3)

    def test_oversell_raises_insufficient_lots(self) -> None:
        cost = self.engine.compute_transaction_cost(Side.BUY, 100.0, 5)
        self.engine.record_buy("X", date(2025, 1, 1), 5, 100.0, cost)
        with self.assertRaises(InsufficientLotsError):
            self.engine.match_sell("X", date(2025, 2, 1), 10, 110.0)

    def test_partial_lot_consumption_leaves_remainder(self) -> None:
        cost = self.engine.compute_transaction_cost(Side.BUY, 100.0, 10)
        self.engine.record_buy("X", date(2025, 1, 1), 10, 100.0, cost)
        self.engine.match_sell("X", date(2025, 2, 1), 4, 110.0)
        self.assertAlmostEqual(self.engine.total_open_quantity("X"), 6.0)


class TestGainTypeClassification(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = CostTaxEngine()
        cost = self.engine.compute_transaction_cost(Side.BUY, 100.0, 10)
        self.engine.record_buy("X", date(2025, 1, 1), 10, 100.0, cost)

    def test_exactly_365_days_is_short_term(self) -> None:
        # Held <= 365 days -> STCG per config (long_term_threshold_days=365,
        # classification is "> threshold", so exactly 365 is still short-term).
        sell_date = date(2025, 1, 1) + timedelta(days=365)
        gains = self.engine.match_sell("X", sell_date, 10, 110.0)
        self.assertEqual(gains[0].gain_type, GainType.SHORT_TERM)
        self.assertAlmostEqual(gains[0].tax_rate, 0.20)

    def test_366_days_is_long_term(self) -> None:
        sell_date = date(2025, 1, 1) + timedelta(days=366)
        gains = self.engine.match_sell("X", sell_date, 10, 110.0)
        self.assertEqual(gains[0].gain_type, GainType.LONG_TERM)
        self.assertAlmostEqual(gains[0].tax_rate, 0.125)

    def test_loss_has_zero_estimated_tax(self) -> None:
        sell_date = date(2025, 1, 1) + timedelta(days=366)
        gains = self.engine.match_sell("X", sell_date, 10, 90.0)  # sold at a loss
        self.assertLess(gains[0].gross_gain, 0)
        self.assertEqual(gains[0].estimated_tax, 0.0)


if __name__ == "__main__":
    unittest.main()
