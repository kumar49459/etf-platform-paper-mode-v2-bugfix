"""Unit tests for ETFMetadataManager."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from etf_platform.data_engine.models import InstrumentMeta
from etf_platform.etf_optimizer.exceptions import MetadataError
from etf_platform.etf_optimizer.metadata_manager import ETFMetadataManager


class FakeDataEngine:
    def __init__(self, instruments: list[InstrumentMeta]) -> None:
        self._instruments = instruments

    def get_instrument_master(self) -> list[InstrumentMeta]:
        return self._instruments


class TestETFMetadataManager(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.overrides_path = self.tmp_dir / "overrides.yaml"

    def _write_overrides(self, content: dict) -> None:
        self.overrides_path.write_text(yaml.safe_dump(content), encoding="utf-8")

    def test_merged_source_when_both_present(self) -> None:
        self._write_overrides({"etfs": {"NIFTYBEES": {"asset_class": "equity_large_cap", "index_tracked": "NIFTY 50"}}})
        engine = FakeDataEngine([InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 123)])
        manager = ETFMetadataManager(engine, self.overrides_path)
        meta = manager.get_metadata("NIFTYBEES")
        self.assertEqual(meta.metadata_source, "merged")
        self.assertEqual(meta.asset_class, "equity_large_cap")
        self.assertEqual(meta.name, "Nifty BeES")  # from provider, no override name given

    def test_provider_only_when_no_override_entry(self) -> None:
        self._write_overrides({"etfs": {}})
        engine = FakeDataEngine([InstrumentMeta("GOLDBEES", "Gold BeES", "NSE", 456)])
        manager = ETFMetadataManager(engine, self.overrides_path)
        meta = manager.get_metadata("GOLDBEES")
        self.assertEqual(meta.metadata_source, "provider_only")
        self.assertIsNone(meta.asset_class)

    def test_override_only_when_no_provider_entry(self) -> None:
        self._write_overrides({"etfs": {"XYZ": {"asset_class": "gold", "name": "XYZ Gold Fund"}}})
        engine = FakeDataEngine([])
        manager = ETFMetadataManager(engine, self.overrides_path)
        meta = manager.get_metadata("XYZ")
        self.assertEqual(meta.metadata_source, "override_only")
        self.assertEqual(meta.name, "XYZ Gold Fund")

    def test_unknown_when_neither_source_has_symbol(self) -> None:
        self._write_overrides({"etfs": {}})
        engine = FakeDataEngine([])
        manager = ETFMetadataManager(engine, self.overrides_path)
        meta = manager.get_metadata("NOTAREALSYMBOL")
        self.assertEqual(meta.metadata_source, "unknown")

    def test_missing_overrides_file_does_not_raise(self) -> None:
        engine = FakeDataEngine([InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 123)])
        manager = ETFMetadataManager(engine, self.tmp_dir / "does_not_exist.yaml")
        meta = manager.get_metadata("NIFTYBEES")
        self.assertEqual(meta.metadata_source, "provider_only")

    def test_malformed_overrides_file_raises_metadata_error(self) -> None:
        self.overrides_path.write_text("etfs: [this, is, not, a, mapping]", encoding="utf-8")
        engine = FakeDataEngine([])
        with self.assertRaises(MetadataError):
            ETFMetadataManager(engine, self.overrides_path)

    def test_needs_verification_flag(self) -> None:
        self._write_overrides({"etfs": {"MOMIDMTM": {"needs_verification": True}}})
        engine = FakeDataEngine([])
        manager = ETFMetadataManager(engine, self.overrides_path)
        self.assertTrue(manager.needs_verification("MOMIDMTM"))
        self.assertFalse(manager.needs_verification("NIFTYBEES"))

    def test_get_universe_metadata_returns_all_requested_symbols(self) -> None:
        self._write_overrides({"etfs": {}})
        engine = FakeDataEngine([InstrumentMeta("A", "A Fund", "NSE", 1), InstrumentMeta("B", "B Fund", "NSE", 2)])
        manager = ETFMetadataManager(engine, self.overrides_path)
        result = manager.get_universe_metadata(["A", "B", "C"])
        self.assertEqual(set(result), {"A", "B", "C"})

    def test_case_insensitive_symbol_lookup(self) -> None:
        self._write_overrides({"etfs": {"NIFTYBEES": {"asset_class": "equity_large_cap"}}})
        engine = FakeDataEngine([])
        manager = ETFMetadataManager(engine, self.overrides_path)
        meta = manager.get_metadata("niftybees")
        self.assertEqual(meta.asset_class, "equity_large_cap")

    def test_real_overrides_file_loads_and_parses(self) -> None:
        """Sanity check against the actual shipped config/etf_metadata_overrides.yaml."""
        real_path = Path(__file__).resolve().parents[2] / "config" / "etf_metadata_overrides.yaml"
        if not real_path.exists():
            self.skipTest("Real overrides file not found relative to test — skipping.")
        engine = FakeDataEngine([])
        manager = ETFMetadataManager(engine, real_path)
        meta = manager.get_metadata("NIFTYBEES")
        self.assertEqual(meta.asset_class, "equity_large_cap")
        self.assertEqual(meta.index_tracked, "NIFTY 50")
        self.assertIsNone(meta.aum_crores)  # honestly disclosed as unpopulated


if __name__ == "__main__":
    unittest.main()
