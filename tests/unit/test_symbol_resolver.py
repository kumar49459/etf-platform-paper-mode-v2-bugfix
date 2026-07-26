"""Unit tests for SymbolResolver — mapping resolution and change detection."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from etf_platform.data_engine.exceptions import SymbolResolutionError
from etf_platform.data_engine.models import InstrumentMeta
from etf_platform.data_engine.symbol_resolver import SymbolResolver


class TestSymbolResolver(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.cache_path = self.tmp_dir / "resolver_cache.json"

    def _make_resolver(self, fetch_results: list[list[InstrumentMeta]]) -> SymbolResolver:
        call_count = {"n": 0}

        def fetcher() -> list[InstrumentMeta]:
            result = fetch_results[min(call_count["n"], len(fetch_results) - 1)]
            call_count["n"] += 1
            return result

        return SymbolResolver(instrument_master_fetcher=fetcher, cache_path=self.cache_path)

    def test_resolve_unknown_symbol_before_refresh_raises(self) -> None:
        resolver = self._make_resolver([[InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 123456)]])
        with self.assertRaises(SymbolResolutionError):
            resolver.resolve("NIFTYBEES")

    def test_refresh_then_resolve_succeeds(self) -> None:
        resolver = self._make_resolver([[InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 123456)]])
        resolver.refresh()
        self.assertEqual(resolver.resolve("NIFTYBEES"), 123456)

    def test_is_known(self) -> None:
        resolver = self._make_resolver([[InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 123456)]])
        self.assertFalse(resolver.is_known("NIFTYBEES"))
        resolver.refresh()
        self.assertTrue(resolver.is_known("NIFTYBEES"))
        self.assertFalse(resolver.is_known("GOLDBEES"))

    def test_token_change_detected(self) -> None:
        resolver = self._make_resolver(
            [
                [InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 111)],
                [InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 222)],  # token changed
            ]
        )
        resolver.refresh()
        events = resolver.refresh()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].old_instrument_token, 111)
        self.assertEqual(events[0].new_instrument_token, 222)
        self.assertEqual(resolver.resolve("NIFTYBEES"), 222)

    def test_delisting_detected(self) -> None:
        resolver = self._make_resolver(
            [
                [InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 111)],
                [],  # symbol disappeared
            ]
        )
        resolver.refresh()
        events = resolver.refresh()
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0].new_instrument_token)

    def test_no_change_produces_no_events(self) -> None:
        instruments = [InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 111)]
        resolver = self._make_resolver([instruments, instruments])
        resolver.refresh()
        events = resolver.refresh()
        self.assertEqual(events, [])

    def test_cache_persists_across_instances(self) -> None:
        instruments = [InstrumentMeta("NIFTYBEES", "Nifty BeES", "NSE", 555)]
        resolver1 = self._make_resolver([instruments])
        resolver1.refresh()

        # A fresh resolver instance pointed at the same cache file should be
        # able to resolve without calling refresh() again.
        resolver2 = SymbolResolver(instrument_master_fetcher=lambda: [], cache_path=self.cache_path)
        self.assertEqual(resolver2.resolve("NIFTYBEES"), 555)


if __name__ == "__main__":
    unittest.main()
