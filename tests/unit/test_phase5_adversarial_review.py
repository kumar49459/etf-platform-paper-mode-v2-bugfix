"""Regression tests for every weakness found during the aggressive
adversarial review of Phases 1-5 (see CHANGELOG.md). Each test corresponds
to a specific finding and must never silently regress.
"""

from __future__ import annotations

import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer.models import ETFScore
from etf_platform.portfolio_optimizer import PortfolioOptimizer
from etf_platform.risk_management import HardConstraints, RiskConstraints, RiskManagementEngine
from etf_platform.risk_management.exceptions import InvalidConstraintsError, ManualSellingViolationError
from etf_platform.risk_management.models import RiskEvent, RiskEventType, Severity
from etf_platform.risk_management.registry import RiskEventRegistry


def bars(n=300, price=100.0):
    return [
        OHLCVBar(
            "X", date(2023, 1, 1) + timedelta(days=i),
            price + (i % 5) * 0.3, price + (i % 5) * 0.3 + 0.5, price + (i % 5) * 0.3 - 0.5,
            price + (i % 5) * 0.3, 20000,
        )
        for i in range(n)
    ]


class TestSellGuardWordInflections(unittest.TestCase):
    def _assert_blocked(self, text):
        with self.assertRaises(ManualSellingViolationError, msg=f"Should have blocked: {text!r}"):
            RiskEvent("e", datetime.now(timezone.utc), RiskEventType.BREACH_MAX_WEIGHT_PER_ETF, Severity.WARNING, "d", text)

    def _assert_allowed(self, text):
        try:
            RiskEvent("e", datetime.now(timezone.utc), RiskEventType.BREACH_MAX_WEIGHT_PER_ETF, Severity.WARNING, "d", text)
        except ManualSellingViolationError:
            self.fail(f"Should have been allowed (false positive): {text!r}")

    def test_gerund_form_selling_is_blocked(self):
        self._assert_blocked("Consider selling this position.")

    def test_reduce_exposure_by_selling_is_blocked(self):
        self._assert_blocked("Reduce your exposure by selling half.")

    def test_past_tense_sold_is_blocked(self):
        self._assert_blocked("The position was sold without authorization.")

    def test_liquidating_inflection_is_blocked(self):
        self._assert_blocked("We recommend liquidating this position.")

    def test_negated_sell_disclosure_still_allowed(self):
        self._assert_allowed("No sell will be proposed automatically.")

    def test_never_sell_disclosure_still_allowed(self):
        self._assert_allowed("This engine will never sell on your behalf.")


class TestSellGuardNegationWindow(unittest.TestCase):
    def test_distant_unrelated_negation_does_not_shield_a_real_sell_instruction(self):
        with self.assertRaises(ManualSellingViolationError):
            RiskEvent(
                "e", datetime.now(timezone.utc), RiskEventType.BREACH_MAX_WEIGHT_PER_ETF, Severity.WARNING,
                "d", "no no no you should actually sell this",
            )

    def test_adjacent_negation_still_correctly_allows_legitimate_text(self):
        try:
            RiskEvent(
                "e", datetime.now(timezone.utc), RiskEventType.BREACH_MAX_WEIGHT_PER_ETF, Severity.WARNING,
                "d", "No sell will be proposed.",
            )
        except ManualSellingViolationError:
            self.fail("Adjacent negation should still allow this legitimate disclosure.")


class TestPortfolioOptimizerPriceSanityCheck(unittest.TestCase):
    def setUp(self):
        self.engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=1.0, max_weight_per_asset_class=1.0, min_history_days_required=100))
        )
        self.optimizer = PortfolioOptimizer(self.engine)

    def test_zero_price_bar_excludes_symbol_with_clear_reason(self):
        corrupted = list(bars())
        corrupted[150] = OHLCVBar("X", corrupted[150].trade_date, 0.0, 0.01, 0.0, 0.0, 1000)
        result = self.optimizer.optimize(
            [ETFScore("CORRUPTED", 0, ()), ETFScore("GOOD", 0, ())],
            {"CORRUPTED": "eq", "GOOD": "eq"},
            {"CORRUPTED": corrupted, "GOOD": bars()},
        )
        excluded_symbols = {s for s, _ in result.excluded_symbols}
        self.assertIn("CORRUPTED", excluded_symbols)
        self.assertNotIn("GOOD", excluded_symbols)
        reason = dict(result.excluded_symbols)["CORRUPTED"]
        self.assertIn("Non-positive price", reason)

    def test_low_greater_than_high_excludes_symbol(self):
        corrupted = list(bars())
        corrupted[50] = OHLCVBar("X", corrupted[50].trade_date, 100, 90, 110, 95, 1000)
        result = self.optimizer.optimize([ETFScore("BADRANGE", 0, ())], {"BADRANGE": "eq"}, {"BADRANGE": corrupted})
        self.assertFalse(result.feasible)
        excluded_symbols = {s for s, _ in result.excluded_symbols}
        self.assertIn("BADRANGE", excluded_symbols)

    def test_clean_data_is_unaffected(self):
        result = self.optimizer.optimize([ETFScore("CLEAN", 0, ())], {"CLEAN": "eq"}, {"CLEAN": bars()})
        self.assertTrue(result.feasible)
        self.assertEqual(result.excluded_symbols, ())


class TestDriftTolerancePctValidation(unittest.TestCase):
    def test_negative_tolerance_rejected(self):
        with self.assertRaises(InvalidConstraintsError):
            RiskManagementEngine(RiskConstraints(hard=HardConstraints(drift_tolerance_pct=-0.5)))

    def test_tolerance_above_one_rejected(self):
        with self.assertRaises(InvalidConstraintsError):
            RiskManagementEngine(RiskConstraints(hard=HardConstraints(drift_tolerance_pct=5.0)))

    def test_zero_tolerance_rejected(self):
        with self.assertRaises(InvalidConstraintsError):
            RiskManagementEngine(RiskConstraints(hard=HardConstraints(drift_tolerance_pct=0.0)))

    def test_nan_tolerance_rejected(self):
        with self.assertRaises(InvalidConstraintsError):
            RiskManagementEngine(RiskConstraints(hard=HardConstraints(drift_tolerance_pct=float("nan"))))

    def test_valid_tolerance_produces_zero_drift_events_for_identical_weights(self):
        engine = RiskManagementEngine(RiskConstraints(hard=HardConstraints(drift_tolerance_pct=0.05)))
        events = engine.evaluate({"A": 0.5, "B": 0.5}, {"A": "x", "B": "y"}, last_approved_weights={"A": 0.5, "B": 0.5})
        drift_events = [e for e in events if e.event_type == RiskEventType.ALLOCATION_DRIFT]
        self.assertEqual(len(drift_events), 0)


class TestRiskEventRegistryConcurrency(unittest.TestCase):
    def test_concurrent_writes_all_persist_without_error(self):
        import shutil
        import tempfile

        tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        registry = RiskEventRegistry(tmp_dir / "risk_events.db")
        self.addCleanup(registry.close)

        errors = []
        num_threads, events_per_thread = 8, 15

        def worker(thread_id):
            try:
                for i in range(events_per_thread):
                    event = RiskEvent(
                        f"t{thread_id}-e{i}", datetime.now(timezone.utc), RiskEventType.ALLOCATION_DRIFT,
                        Severity.INFO, "concurrency test", "Review allocation.",
                    )
                    registry.record(event)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(len(registry.list_events()), num_threads * events_per_thread)


if __name__ == "__main__":
    unittest.main()
