"""Unit tests for BacktestRunRegistry."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path

from etf_platform.backtesting.exceptions import InvalidOrderError
from etf_platform.backtesting.models import BacktestConfig, OrderIntent, OrderType, ReproducibilityRecord
from etf_platform.backtesting.registry import BacktestRunRegistry, run_and_register
from etf_platform.cost_tax_engine import Side


class TestBacktestRunRegistry(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.registry = BacktestRunRegistry(self.tmp_dir / "runs.db")
        self.addCleanup(self.registry.close)

    def _make_config_and_repro(self):
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 6, 1),
            initial_capital=100000, symbols=("NIFTYBEES",),
        )
        repro = ReproducibilityRecord(
            run_id="test-run-1", code_commit_hash="abc123", code_is_dirty=False,
            config_version="cfg-v1", data_snapshot_id="snap-v1", started_at="2025-01-01T00:00:00",
        )
        return config, repro

    def test_register_start_then_finish_roundtrip(self) -> None:
        config, repro = self._make_config_and_repro()
        self.registry.register_run_start(config, repro)
        self.registry.register_run_finish("test-run-1", "2025-06-02T00:00:00", {"xirr": 0.12})

        row = self.registry.get_run("test-run-1")
        self.assertIsNotNone(row)
        self.assertEqual(row["code_commit_hash"], "abc123")
        self.assertEqual(row["code_is_dirty"], 0)
        self.assertIsNotNone(row["finished_at"])
        self.assertIn("0.12", row["metrics_json"])

    def test_get_nonexistent_run_returns_none(self) -> None:
        self.assertIsNone(self.registry.get_run("does-not-exist"))

    def test_wal_mode_enabled(self) -> None:
        mode = self.registry._conn.execute("PRAGMA journal_mode;").fetchone()[0]
        self.assertEqual(mode.lower(), "wal")


class NoOpStrategy:
    def generate_orders(self, as_of_date, history, portfolio):
        return []


class TestRunAndRegister(unittest.TestCase):
    """Covers the failure-recovery gap found in the Phase 4 adversarial
    review: a backtest run must always be finalized in the registry, even
    if engine.run() raises partway through."""

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.registry = BacktestRunRegistry(self.tmp_dir / "runs.db")
        self.addCleanup(self.registry.close)

    def test_successful_run_is_finalized_as_succeeded(self) -> None:
        from etf_platform.backtesting.engine import BacktestEngine

        config, repro = self._make_config_and_repro()
        engine = BacktestEngine(config, NoOpStrategy())
        result = run_and_register(engine, {}, self.registry, repro)

        self.assertIsNotNone(result)
        row = self.registry.get_run(repro.run_id)
        self.assertIsNotNone(row)
        self.assertIsNotNone(row["finished_at"])
        self.assertIn("succeeded", row["metrics_json"])

    def test_failed_run_is_finalized_as_failed_not_left_stuck(self) -> None:
        from etf_platform.backtesting.engine import BacktestEngine

        class ExplodingStrategy:
            def generate_orders(self, as_of_date, history, portfolio):
                raise RuntimeError("simulated strategy crash")

        config, repro = self._make_config_and_repro()
        bars = {"NIFTYBEES": self._make_bars()}
        engine = BacktestEngine(config, ExplodingStrategy())

        with self.assertRaises(RuntimeError):
            run_and_register(engine, bars, self.registry, repro)

        row = self.registry.get_run(repro.run_id)
        self.assertIsNotNone(row, "Run must be recorded even though it failed — this is the whole point.")
        self.assertIsNotNone(row["finished_at"])
        self.assertIn("failed", row["metrics_json"])
        self.assertIn("simulated strategy crash", row["metrics_json"])

    def _make_config_and_repro(self):
        config = BacktestConfig(
            start_date=date(2025, 1, 1), end_date=date(2025, 6, 1),
            initial_capital=100000, symbols=("NIFTYBEES",),
        )
        repro = ReproducibilityRecord(
            run_id="test-run-2", code_commit_hash="abc123", code_is_dirty=False,
            config_version="cfg-v1", data_snapshot_id="snap-v1", started_at="2025-01-01T00:00:00",
        )
        return config, repro

    @staticmethod
    def _make_bars():
        from datetime import timedelta

        from etf_platform.data_engine.models import OHLCVBar

        return [
            OHLCVBar("NIFTYBEES", date(2025, 1, 1) + timedelta(days=i), 100, 101, 99, 100, 50000)
            for i in range(150)
        ]


if __name__ == "__main__":
    unittest.main()
