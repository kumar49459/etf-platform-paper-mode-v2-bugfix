"""Integration tests for HistoricalDataEngine: ingest() -> get_ohlcv()
end-to-end, using a fake in-process DataProvider (no real HTTP), CSV storage,
and a temp SQLite registry. This exercises the full wiring — provider
fallback, DataQualityValidator gating, storage, and the snapshot registry —
together, complementing the narrower unit tests of each piece.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock as unittest_mock

import yaml

from etf_platform.config_manager.config_manager import ConfigManager
from etf_platform.data_engine.data_engine import HistoricalDataEngine
from etf_platform.data_engine.exceptions import SnapshotNotFoundError
from etf_platform.data_engine.models import CorporateAction, InstrumentMeta, OHLCVBar
from etf_platform.data_engine.providers.base import DataProvider
from etf_platform.data_quality.exceptions import CriticalDataQualityError


class FakeProvider(DataProvider):
    """In-process DataProvider stand-in — returns whatever bars/actions the
    test configures per symbol, with no network involved."""

    def __init__(self, provider_name: str = "fake") -> None:
        self._name = provider_name
        self.ohlcv_by_symbol: dict[str, list[OHLCVBar]] = {}
        self.corporate_actions_by_symbol: dict[str, list[CorporateAction]] = {}

    @property
    def name(self) -> str:
        return self._name

    def fetch_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCVBar]:
        return self.ohlcv_by_symbol.get(symbol, [])

    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        return self.corporate_actions_by_symbol.get(symbol, [])

    def fetch_instrument_master(self) -> list[InstrumentMeta]:
        return []


def good_bar(d: date, close: float = 100.0) -> OHLCVBar:
    return OHLCVBar(symbol="X", trade_date=d, open=close - 1, high=close + 1, low=close - 2, close=close, volume=1000)


class DataEngineIntegrationTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

        config_dir = self.tmp_dir / "config"
        config_dir.mkdir()
        (config_dir / "base.yaml").write_text(
            yaml.safe_dump(
                {
                    "data_engine": {
                        "primary_provider": "nse",
                        "secondary_provider": "kite",
                        "storage_backend": "csv",
                        "storage_path": str(self.tmp_dir / "data"),
                        "snapshot_registry_db": str(self.tmp_dir / "snapshots.db"),
                        "max_price_jump_pct": 20.0,
                        "stale_price_max_days": 10,
                        "rate_limits": {
                            "nse": {"calls_per_second": 1000.0, "calls_per_minute": 100000.0},
                        },
                    },
                    "database": {"sqlite_path": str(self.tmp_dir / "platform.db")},
                }
            ),
            encoding="utf-8",
        )
        self.config = ConfigManager(config_dir=config_dir, environment="dev").load()

        # No SecretsManager passed -> Kite provider is not built (as designed,
        # see HistoricalDataEngine._build_providers), leaving only "nse" in
        # self._providers, which we then swap for our FakeProvider.
        self.engine = HistoricalDataEngine(self.config, secrets_manager=None)
        self.addCleanup(self.engine.close)

        self.fake_provider = FakeProvider(provider_name="nse")
        self.engine._providers["nse"] = self.fake_provider


class TestIngestAndReadRoundtrip(DataEngineIntegrationTestBase):
    def test_ingest_then_get_ohlcv(self) -> None:
        self.fake_provider.ohlcv_by_symbol["NIFTYBEES"] = [
            good_bar(date(2026, 1, 2), 100.0),
            good_bar(date(2026, 1, 5), 101.0),
        ]
        snapshot = self.engine.ingest(["NIFTYBEES"], date(2026, 1, 2), date(2026, 1, 5))
        self.assertEqual(snapshot.row_count, 2)

        result = self.engine.get_ohlcv(["NIFTYBEES"], date(2026, 1, 1), date(2026, 1, 6))
        self.assertEqual(len(result["NIFTYBEES"]), 2)
        self.assertEqual(result["NIFTYBEES"][0].close, 100.0)

    def test_get_ohlcv_before_any_ingest_raises(self) -> None:
        with self.assertRaises(SnapshotNotFoundError):
            self.engine.get_ohlcv(["NIFTYBEES"], date(2026, 1, 1), date(2026, 1, 6))

    def test_explicit_snapshot_id_isolates_reads(self) -> None:
        self.fake_provider.ohlcv_by_symbol["X"] = [good_bar(date(2026, 1, 2), 100.0)]
        snap1 = self.engine.ingest(["X"], date(2026, 1, 2), date(2026, 1, 2))

        self.fake_provider.ohlcv_by_symbol["X"] = [good_bar(date(2026, 1, 2), 999.0)]
        snap2 = self.engine.ingest(["X"], date(2026, 1, 2), date(2026, 1, 2))

        result1 = self.engine.get_ohlcv(["X"], date(2026, 1, 1), date(2026, 1, 3), snapshot_id=snap1.snapshot_id)
        result2 = self.engine.get_ohlcv(["X"], date(2026, 1, 1), date(2026, 1, 3), snapshot_id=snap2.snapshot_id)
        self.assertEqual(result1["X"][0].close, 100.0)
        self.assertEqual(result2["X"][0].close, 999.0)

        # No snapshot_id -> defaults to the most recent one.
        latest = self.engine.get_ohlcv(["X"], date(2026, 1, 1), date(2026, 1, 3))
        self.assertEqual(latest["X"][0].close, 999.0)


class TestCriticalHaltBehavior(DataEngineIntegrationTestBase):
    def test_no_data_for_symbol_halts_entire_ingestion_run(self) -> None:
        self.fake_provider.ohlcv_by_symbol["GOOD"] = [good_bar(date(2026, 1, 2))]
        # "BAD" has no data configured -> triggers the no_data CRITICAL check.
        with self.assertRaises(CriticalDataQualityError):
            self.engine.ingest(["GOOD", "BAD"], date(2026, 1, 2), date(2026, 1, 2))

        # Per design: a halted run does not get registered as a usable
        # snapshot at all (not even partially) — confirm no snapshot exists.
        with self.assertRaises(SnapshotNotFoundError):
            self.engine.get_ohlcv(["GOOD"], date(2026, 1, 1), date(2026, 1, 3))

    def test_force_override_allows_partial_bad_data_through(self) -> None:
        # "BAD" still has no data, but we force past it with a reason.
        self.fake_provider.ohlcv_by_symbol["GOOD"] = [good_bar(date(2026, 1, 2))]
        snapshot = self.engine.ingest(
            ["GOOD", "BAD"], date(2026, 1, 2), date(2026, 1, 2),
            force=True, force_reason="Testing force override in integration test.",
        )
        self.assertIsNotNone(snapshot)
        result = self.engine.get_ohlcv(["GOOD", "BAD"], date(2026, 1, 1), date(2026, 1, 3))
        self.assertEqual(len(result["GOOD"]), 1)
        self.assertEqual(len(result["BAD"]), 0)


class TestProviderFallback(DataEngineIntegrationTestBase):
    def test_falls_back_to_secondary_when_primary_empty(self) -> None:
        secondary = FakeProvider(provider_name="kite")
        secondary.ohlcv_by_symbol["X"] = [good_bar(date(2026, 1, 2), 55.0)]
        self.engine._providers["kite"] = secondary
        # primary ("nse" / self.fake_provider) has no data for "X" configured.
        snapshot = self.engine.ingest(["X"], date(2026, 1, 2), date(2026, 1, 2))
        self.assertIn("kite", snapshot.source_providers)
        result = self.engine.get_ohlcv(["X"], date(2026, 1, 1), date(2026, 1, 3))
        self.assertEqual(result["X"][0].close, 55.0)


class TestResourceCleanup(DataEngineIntegrationTestBase):
    def test_context_manager_closes_registry(self) -> None:
        with HistoricalDataEngine(self.config, secrets_manager=None) as engine:
            engine._providers["nse"] = FakeProvider(provider_name="nse")
        # After exiting the context, the underlying SQLite connection should
        # be closed — executing against it should raise.
        import sqlite3
        with self.assertRaises(sqlite3.ProgrammingError):
            engine._registry._conn.execute("SELECT 1")

    def test_close_is_idempotent(self) -> None:
        engine = HistoricalDataEngine(self.config, secrets_manager=None)
        engine._providers["nse"] = FakeProvider(provider_name="nse")
        engine.close()
        engine.close()  # must not raise on double-close

    def test_close_closes_provider_sessions(self) -> None:
        engine = HistoricalDataEngine(self.config, secrets_manager=None)
        fake_close = unittest_mock.Mock()
        provider = FakeProvider(provider_name="nse")
        provider.close = fake_close
        engine._providers["nse"] = provider
        engine.close()
        fake_close.assert_called_once()

    def test_provider_close_failure_does_not_block_registry_close(self) -> None:
        engine = HistoricalDataEngine(self.config, secrets_manager=None)
        provider = FakeProvider(provider_name="nse")
        provider.close = unittest_mock.Mock(side_effect=RuntimeError("boom"))
        engine._providers["nse"] = provider
        engine.close()  # must not raise, and registry must still close
        import sqlite3
        with self.assertRaises(sqlite3.ProgrammingError):
            engine._registry._conn.execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
