"""Tests for VerificationService -- purity, affordability, compliance,
liquidity, order validity."""

from __future__ import annotations

import unittest

from etf_platform.execution_manager import (
    ComplianceCheckResult,
    ComplianceResult,
    FixedScenarioProvider,
    InMemoryEventRecorder,
    MarketDepthSnapshot,
    MinimalInlineComplianceChecker,
    PaperBrokerPort,
    RejectionReason,
    SimulatedClock,
    VerificationOutcome,
    VerificationService,
    utc_now,
)
from etf_platform.execution_manager.scenarios import BrokerScenario


class RejectingComplianceChecker:
    def check(self, symbol, quantity, limit_price):
        return ComplianceResult(result=ComplianceCheckResult.FAIL, reason="Simulated compliance failure.")


class FixedDepthQuoteProvider:
    def __init__(self, bid, ask, symbol="A"):
        self._bid, self._ask, self._symbol = bid, ask, symbol

    def get_last_traded_price(self, symbol):
        return (self._bid + self._ask) / 2

    def get_market_depth(self, symbol):
        if symbol != self._symbol:
            return None
        return MarketDepthSnapshot(symbol, utc_now(), self._bid, self._ask, 1000, 1000)


class NoDepthQuoteProvider:
    def get_last_traded_price(self, symbol):
        return 100.0

    def get_market_depth(self, symbol):
        return None


def make_env(cash=100000.0, bid=99.9, ask=100.1):
    quotes = FixedDepthQuoteProvider(bid, ask)
    clock = SimulatedClock()
    events = InMemoryEventRecorder()
    provider = FixedScenarioProvider(BrokerScenario.IMMEDIATE_FILL)
    broker = PaperBrokerPort(clock, events, provider, starting_cash=cash)
    compliance = MinimalInlineComplianceChecker()
    return quotes, broker, compliance


class TestVerificationServicePurity(unittest.TestCase):
    def test_verify_never_mutates_its_own_state_between_calls(self):
        service = VerificationService()
        quotes, broker, compliance = make_env()
        r1 = service.verify("A", 100, 100.0, quotes, broker, compliance)
        r2 = service.verify("A", 100, 100.0, quotes, broker, compliance)
        self.assertEqual(r1, r2)

    def test_result_is_frozen_dataclass(self):
        service = VerificationService()
        quotes, broker, compliance = make_env()
        result = service.verify("A", 100, 100.0, quotes, broker, compliance)
        with self.assertRaises(Exception):
            result.outcome = VerificationOutcome.REJECTED


class TestAffordability(unittest.TestCase):
    def test_approved_when_affordable(self):
        service = VerificationService()
        quotes, broker, compliance = make_env(cash=100000.0)
        result = service.verify("A", 100, 100.0, quotes, broker, compliance)
        self.assertTrue(result.approved)
        self.assertEqual(result.outcome, VerificationOutcome.APPROVED)
        self.assertEqual(result.verified_quantity, 100)

    def test_reduced_when_insufficient_for_full_quantity(self):
        service = VerificationService()
        quotes, broker, compliance = make_env(cash=5000.0)
        result = service.verify("A", 100, 100.0, quotes, broker, compliance)
        self.assertEqual(result.outcome, VerificationOutcome.APPROVED_WITH_REDUCED_QUANTITY)
        self.assertLess(result.verified_quantity, 100)
        self.assertTrue(result.notes)

    def test_never_increases_quantity(self):
        service = VerificationService()
        quotes, broker, compliance = make_env(cash=10_000_000.0)
        result = service.verify("A", 100, 100.0, quotes, broker, compliance)
        self.assertLessEqual(result.verified_quantity, 100)

    def test_rejected_when_cash_insufficient_for_even_one_unit(self):
        service = VerificationService()
        quotes, broker, compliance = make_env(cash=1.0)
        result = service.verify("A", 100, 100.0, quotes, broker, compliance)
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_reason, RejectionReason.INSUFFICIENT_CASH)

    def test_full_cost_breakdown_reserved_not_just_gross(self):
        from etf_platform.cost_tax_engine import CostTaxEngine, IndiaEquityCostConfig, Side

        service = VerificationService(cost_tax_engine=CostTaxEngine(IndiaEquityCostConfig()))
        quotes, broker, compliance = make_env(cash=50000.0)
        result = service.verify("A", 1000, 100.0, quotes, broker, compliance)
        cte = CostTaxEngine(IndiaEquityCostConfig())
        cost = cte.compute_transaction_cost(Side.BUY, 100.0, result.verified_quantity)
        self.assertLessEqual(result.verified_quantity * 100.0 + cost.total_cost, 50000.0)


class TestCompliance(unittest.TestCase):
    def test_rejected_on_compliance_failure(self):
        service = VerificationService()
        quotes, broker, _ = make_env()
        result = service.verify("A", 100, 100.0, quotes, broker, RejectingComplianceChecker())
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_reason, RejectionReason.COMPLIANCE_FAILED)

    def test_compliance_checked_before_affordability(self):
        service = VerificationService()
        quotes, broker, _ = make_env(cash=1.0)
        result = service.verify("A", 100, 100.0, quotes, broker, RejectingComplianceChecker())
        self.assertEqual(result.rejection_reason, RejectionReason.COMPLIANCE_FAILED)


class TestLiquidity(unittest.TestCase):
    def test_rejected_when_spread_too_wide(self):
        service = VerificationService(max_spread_pct=0.01)
        quotes = FixedDepthQuoteProvider(bid=90.0, ask=110.0)
        _, broker, compliance = make_env()
        result = service.verify("A", 100, 100.0, quotes, broker, compliance)
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_reason, RejectionReason.LIQUIDITY_INSUFFICIENT)

    def test_approved_when_spread_acceptable(self):
        service = VerificationService(max_spread_pct=0.05)
        quotes = FixedDepthQuoteProvider(bid=99.9, ask=100.1)
        _, broker, compliance = make_env()
        result = service.verify("A", 100, 100.0, quotes, broker, compliance)
        self.assertTrue(result.approved)

    def test_rejected_when_quote_unavailable(self):
        service = VerificationService()
        _, broker, compliance = make_env()
        result = service.verify("A", 100, 100.0, NoDepthQuoteProvider(), broker, compliance)
        self.assertFalse(result.approved)
        self.assertEqual(result.rejection_reason, RejectionReason.QUOTE_UNAVAILABLE)


class TestOrderValidity(unittest.TestCase):
    def test_rejected_for_zero_quantity(self):
        service = VerificationService()
        quotes, broker, compliance = make_env()
        result = service.verify("A", 0, 100.0, quotes, broker, compliance)
        self.assertEqual(result.rejection_reason, RejectionReason.INVALID_ORDER)

    def test_rejected_for_negative_price(self):
        service = VerificationService()
        quotes, broker, compliance = make_env()
        result = service.verify("A", 100, -5.0, quotes, broker, compliance)
        self.assertEqual(result.rejection_reason, RejectionReason.INVALID_ORDER)


if __name__ == "__main__":
    unittest.main()
