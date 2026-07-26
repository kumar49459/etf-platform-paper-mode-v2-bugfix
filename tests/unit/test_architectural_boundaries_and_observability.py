"""Architectural boundary audit (Milestone 4, requirement 1) and
observability reconstruction (requirement 7).
"""

from __future__ import annotations

import ast
import inspect
import shutil
import tempfile
import unittest
from pathlib import Path

from etf_platform.execution_manager import (
    ExecutionRecord,
    ExecutionStateStore,
    InMemoryEventRecorder,
    MinimalInlineComplianceChecker,
    OrderLifecycleState,
    PaperBrokerPort,
    PaperQuoteProvider,
    SeededRandomScenarioProvider,
    SimulatedClock,
    SubmissionOrchestrator,
    new_execution_id,
)
from etf_platform.strategy_engine.ports import NotificationPort


def _imports_of(module):
    tree = ast.parse(inspect.getsource(module))
    names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
    names += [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
    return names


class TestArchitecturalBoundaries(unittest.TestCase):
    def test_verification_service_never_imports_orchestrator_or_reconciliation(self):
        import etf_platform.execution_manager.verification as verification_module

        imports = _imports_of(verification_module)
        self.assertFalse(any("orchestrator" in i for i in imports))
        self.assertFalse(any("reconciliation" in i for i in imports))

    def test_reconciliation_never_imports_orchestrator_or_verification(self):
        import etf_platform.execution_manager.reconciliation as reconciliation_module

        imports = _imports_of(reconciliation_module)
        self.assertFalse(any("orchestrator" in i for i in imports))
        self.assertFalse(any("verification" in i for i in imports))

    def test_persistence_never_imports_orchestrator_verification_or_reconciliation(self):
        import etf_platform.execution_manager.persistence as persistence_module

        imports = _imports_of(persistence_module)
        for forbidden in ("orchestrator", "verification", "reconciliation"):
            self.assertFalse(any(forbidden in i for i in imports), f"persistence.py imports {forbidden}")

    def test_paper_broker_never_imports_orchestrator_verification_or_reconciliation(self):
        import etf_platform.execution_manager.paper_broker as paper_broker_module

        imports = _imports_of(paper_broker_module)
        for forbidden in ("orchestrator", "verification", "reconciliation"):
            self.assertFalse(any(forbidden in i for i in imports), f"paper_broker.py imports {forbidden}")

    def test_execution_manager_still_never_imports_ai_allocation(self):
        import etf_platform.execution_manager as em_pkg

        pkg_dir = Path(em_pkg.__file__).parent
        for py_file in pkg_dir.rglob("*.py"):
            source = py_file.read_text()
            self.assertNotIn("ai_allocation", source, f"{py_file} references ai_allocation")

    def test_strategy_engine_still_never_imports_execution_manager(self):
        import etf_platform.strategy_engine as se_pkg

        pkg_dir = Path(se_pkg.__file__).parent
        for py_file in pkg_dir.rglob("*.py"):
            source = py_file.read_text()
            self.assertNotIn("execution_manager", source, f"{py_file} references execution_manager")

    def test_orchestrator_is_the_only_component_that_imports_verification(self):
        import etf_platform.execution_manager.orchestrator as orchestrator_module

        imports = _imports_of(orchestrator_module)
        self.assertTrue(any("verification" in i for i in imports))
        self.assertFalse(any("reconciliation" in i for i in imports))


class TestObservabilityReconstruction(unittest.TestCase):
    def test_full_order_story_reconstructable_from_events_alone(self):
        tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        store = ExecutionStateStore(tmp_dir / "obs.db")
        self.addCleanup(store.close)
        clock = SimulatedClock()
        events = InMemoryEventRecorder()
        from etf_platform.execution_manager import BrokerScenario, FixedScenarioProvider

        provider = FixedScenarioProvider(BrokerScenario.IMMEDIATE_FILL)
        broker = PaperBrokerPort(clock, events, provider, starting_cash=1_000_000.0)
        quotes = PaperQuoteProvider(clock, events, provider, base_prices={"A": 100.0})
        compliance = MinimalInlineComplianceChecker()

        class FakeNotifier(NotificationPort):
            def send(self, message): pass
            def poll_commands(self): return []

        orchestrator = SubmissionOrchestrator(store, broker, quotes, compliance, FakeNotifier(), clock, events)

        record = ExecutionRecord(
            execution_id=new_execution_id(), queue_id=None, cycle_id="obs-cycle-1", symbol="A",
            quantity_proposed=50, quantity_final=None, limit_price=100.0,
            order_status=OrderLifecycleState.PROPOSAL, broker_order_id=None, executed_price=None,
            executed_quantity=0, is_paper_trade=True, created_at=clock.now(), last_status_check=None,
            priority_rank=1,
        )
        store.save_execution_record(record)
        correlation_id = "reconstruct-me-corr-1"
        for _ in range(4):
            record = orchestrator.process_order(record, correlation_id=correlation_id)
            if record.order_status in (OrderLifecycleState.FILLED, OrderLifecycleState.CANCELLED, OrderLifecycleState.FAILED):
                break

        story = [e for e in events.events() if e.correlation_id == correlation_id]
        self.assertGreater(len(story), 0, "No events found for this correlation_id -- reconstruction impossible.")

        cycle_ids_seen = {e.cycle_id for e in story if e.cycle_id}
        self.assertEqual(cycle_ids_seen, {"obs-cycle-1"})

        symbols_seen = {e.symbol for e in story if e.symbol}
        self.assertEqual(symbols_seen, {"A"})

        timestamps = [e.timestamp for e in story]
        self.assertEqual(timestamps, sorted(timestamps), "Event sequence must be chronologically ordered.")

        components_seen = {e.component for e in story if e.component}
        self.assertIn("SubmissionOrchestrator", components_seen)

        final_result = story[-1].result
        self.assertIsNotNone(final_result)

    def test_reconstruction_works_even_across_a_restart(self):
        tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        db_path = tmp_dir / "obs_restart.db"
        clock = SimulatedClock()
        events = InMemoryEventRecorder()
        from etf_platform.execution_manager import BrokerScenario, FixedScenarioProvider

        provider = FixedScenarioProvider(BrokerScenario.IMMEDIATE_FILL)  # deterministic success -- this test is about
                                                                          # observability, not failure-handling variety
        broker = PaperBrokerPort(clock, events, provider, starting_cash=1_000_000.0)
        quotes = PaperQuoteProvider(clock, events, provider, base_prices={"A": 100.0})
        compliance = MinimalInlineComplianceChecker()

        class FakeNotifier(NotificationPort):
            def send(self, message): pass
            def poll_commands(self): return []

        store = ExecutionStateStore(db_path)
        orchestrator = SubmissionOrchestrator(store, broker, quotes, compliance, FakeNotifier(), clock, events)
        record = ExecutionRecord(
            execution_id=new_execution_id(), queue_id=None, cycle_id="obs-restart-cycle", symbol="A",
            quantity_proposed=50, quantity_final=None, limit_price=100.0,
            order_status=OrderLifecycleState.PROPOSAL, broker_order_id=None, executed_price=None,
            executed_quantity=0, is_paper_trade=True, created_at=clock.now(), last_status_check=None,
            priority_rank=1,
        )
        store.save_execution_record(record)
        record = orchestrator.process_order(record, correlation_id="restart-corr")
        store.close()

        store2 = ExecutionStateStore(db_path)
        self.addCleanup(store2.close)
        orchestrator2 = SubmissionOrchestrator(store2, broker, quotes, compliance, FakeNotifier(), clock, events)
        reloaded = store2.load_execution_record(record.execution_id)
        orchestrator2.process_order(reloaded, correlation_id="restart-corr")

        story = [e for e in events.events() if e.correlation_id == "restart-corr"]
        self.assertGreater(len(story), 1, "Story should span both pre- and post-restart events.")
        timestamps = [e.timestamp for e in story]
        self.assertEqual(timestamps, sorted(timestamps))


if __name__ == "__main__":
    unittest.main()
