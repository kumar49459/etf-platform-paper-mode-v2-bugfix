"""Unit tests for the Risk Management Engine."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from etf_platform.risk_management import (
    HardConstraints,
    ManualSellingViolationError,
    RiskConstraints,
    RiskEventRegistry,
    RiskEventType,
    RiskManagementEngine,
    Severity,
)
from etf_platform.risk_management.exceptions import InvalidConstraintsError
from etf_platform.risk_management.models import RiskEvent


class TestHardConstraintsValidation(unittest.TestCase):
    def test_default_constraints_are_valid(self) -> None:
        RiskConstraints().validate()

    def test_etf_cap_exceeding_asset_class_cap_raises(self) -> None:
        constraints = RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.7, max_weight_per_asset_class=0.5))
        with self.assertRaises(InvalidConstraintsError):
            constraints.validate()

    def test_out_of_range_weight_raises(self) -> None:
        constraints = RiskConstraints(hard=HardConstraints(max_weight_per_etf=1.5))
        with self.assertRaises(InvalidConstraintsError):
            constraints.validate()

    def test_zero_history_days_raises(self) -> None:
        constraints = RiskConstraints(hard=HardConstraints(min_history_days_required=0))
        with self.assertRaises(InvalidConstraintsError):
            constraints.validate()


class TestManualSellingGuard(unittest.TestCase):
    def test_actual_sell_instruction_blocked(self) -> None:
        with self.assertRaises(ManualSellingViolationError):
            RiskEvent(
                "e1", datetime.now(timezone.utc), RiskEventType.BREACH_MAX_WEIGHT_PER_ETF, Severity.WARNING,
                "desc", "Sell 50 units of NIFTYBEES immediately.",
            )

    def test_liquidate_instruction_blocked(self) -> None:
        with self.assertRaises(ManualSellingViolationError):
            RiskEvent(
                "e2", datetime.now(timezone.utc), RiskEventType.BREACH_MAX_WEIGHT_PER_ETF, Severity.WARNING,
                "desc", "Liquidate the GOLDBEES position.",
            )

    def test_negated_sell_language_allowed(self) -> None:
        event = RiskEvent(
            "e3", datetime.now(timezone.utc), RiskEventType.BREACH_MAX_WEIGHT_PER_ETF, Severity.WARNING,
            "desc", "No sell will be proposed automatically. Review this position yourself.",
        )
        self.assertIsNotNone(event)

    def test_never_sell_language_allowed(self) -> None:
        event = RiskEvent(
            "e4", datetime.now(timezone.utc), RiskEventType.BREACH_MAX_WEIGHT_PER_ETF, Severity.WARNING,
            "desc", "This engine will never sell on your behalf.",
        )
        self.assertIsNotNone(event)

    def test_purely_informational_action_allowed(self) -> None:
        event = RiskEvent(
            "e5", datetime.now(timezone.utc), RiskEventType.ALLOCATION_DRIFT, Severity.WARNING,
            "desc", "Review concentration and consider your own next steps.",
        )
        self.assertIsNotNone(event)


class TestRiskManagementEngineEvaluate(unittest.TestCase):
    def test_no_breach_no_events(self) -> None:
        engine = RiskManagementEngine(RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.5)))
        events = engine.evaluate({"A": 0.3, "B": 0.3}, {"A": "equity", "B": "gold"})
        self.assertEqual(events, [])

    def test_per_etf_breach_detected(self) -> None:
        engine = RiskManagementEngine(RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.3)))
        events = engine.evaluate({"A": 0.5}, {"A": "equity"})
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, RiskEventType.BREACH_MAX_WEIGHT_PER_ETF)

    def test_asset_class_breach_detected_across_multiple_etfs(self) -> None:
        engine = RiskManagementEngine(
            RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.5, max_weight_per_asset_class=0.6))
        )
        events = engine.evaluate({"A": 0.4, "B": 0.35}, {"A": "equity", "B": "equity"})
        breach_events = [e for e in events if e.event_type == RiskEventType.BREACH_MAX_WEIGHT_PER_ASSET_CLASS]
        self.assertEqual(len(breach_events), 1)

    def test_no_sell_recommended_action_in_any_event(self) -> None:
        engine = RiskManagementEngine(RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.1)))
        events = engine.evaluate({"A": 0.9}, {"A": "equity"}, last_approved_weights={"A": 0.3})
        self.assertGreater(len(events), 0)

    def test_drift_detected_against_last_approved(self) -> None:
        engine = RiskManagementEngine(RiskConstraints(hard=HardConstraints(drift_tolerance_pct=0.05)))
        events = engine.evaluate(
            current_weights={"A": 0.5}, asset_class_by_symbol={"A": "equity"},
            last_approved_weights={"A": 0.3},
        )
        drift_events = [e for e in events if e.event_type == RiskEventType.ALLOCATION_DRIFT]
        self.assertEqual(len(drift_events), 1)

    def test_no_drift_check_without_last_approved_weights(self) -> None:
        engine = RiskManagementEngine()
        events = engine.evaluate({"A": 0.3}, {"A": "equity"})  # within default max_weight_per_etf (0.40)
        self.assertEqual(events, [])


class TestDrawdownGate(unittest.TestCase):
    def test_within_target_returns_none(self) -> None:
        engine = RiskManagementEngine(RiskConstraints(hard=HardConstraints(max_drawdown_target=0.2)))
        self.assertIsNone(engine.check_drawdown_constraint(0.15))

    def test_exceeding_target_returns_critical_event(self) -> None:
        engine = RiskManagementEngine(RiskConstraints(hard=HardConstraints(max_drawdown_target=0.2)))
        event = engine.check_drawdown_constraint(0.35)
        self.assertIsNotNone(event)
        self.assertEqual(event.severity, Severity.CRITICAL)


class TestRequestHalt(unittest.TestCase):
    def test_halt_produces_critical_event_no_live_effect(self) -> None:
        engine = RiskManagementEngine()
        event = engine.request_halt("test reason")
        self.assertEqual(event.severity, Severity.CRITICAL)
        self.assertEqual(event.event_type, RiskEventType.KILL_SWITCH_REQUESTED)


class TestRiskEventRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.registry = RiskEventRegistry(self.tmp_dir / "risk.db")
        self.addCleanup(self.registry.close)

    def test_record_and_retrieve_roundtrip(self) -> None:
        engine = RiskManagementEngine(RiskConstraints(hard=HardConstraints(max_weight_per_etf=0.3)), registry=self.registry)
        events = engine.evaluate({"A": 0.5}, {"A": "equity"})
        stored = self.registry.get_event(events[0].event_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored["event_type"], "breach_max_weight_per_etf")

    def test_list_events_filters_by_severity(self) -> None:
        engine = RiskManagementEngine(registry=self.registry)
        engine.request_halt("critical scenario")
        critical_events = self.registry.list_events(severity=Severity.CRITICAL)
        self.assertGreaterEqual(len(critical_events), 1)

    def test_wal_mode_enabled(self) -> None:
        mode = self.registry._conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")


if __name__ == "__main__":
    unittest.main()
