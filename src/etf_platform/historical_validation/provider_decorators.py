"""Two more DataProvider implementations (Historical Data Acquisition
Module, following Milestone 5A): index-proxy wrapping and mandatory
validation. Both are decorators around another DataProvider -- same
pattern, applied twice, rather than two unrelated pieces of code.
"""

from __future__ import annotations

import dataclasses

from etf_platform.data_engine.providers.base import DataProvider
from etf_platform.historical_validation.reproducibility_manifest import DataIntegrityAbortedError, validate_and_gate


class IndexProxyDataProvider(DataProvider):
    """Wraps another DataProvider (typically pointed at an index's own
    price series) and re-labels every bar's `source` field to mark it
    explicitly as proxy data. The wrapped provider does the actual
    fetching (CSV, or a future real index-data provider); this class's
    only job is the re-labeling and the name it reports."""

    def __init__(self, wrapped_provider, proxy_symbol_suffix="_PROXY"):
        self._wrapped = wrapped_provider
        self._suffix = proxy_symbol_suffix

    @property
    def name(self):
        return f"{self._wrapped.name}_index_proxy"

    def fetch_ohlcv(self, symbol, start, end):
        bars = self._wrapped.fetch_ohlcv(symbol, start, end)
        return [dataclasses.replace(bar, source=self.name) for bar in bars]

    def fetch_corporate_actions(self, symbol, start, end):
        actions = self._wrapped.fetch_corporate_actions(symbol, start, end)
        return [dataclasses.replace(a, source=self.name) for a in actions]

    def fetch_instrument_master(self):
        return self._wrapped.fetch_instrument_master()


class ValidatedDataProvider(DataProvider):
    """Wraps ANY DataProvider, running the mandatory data-quality gate
    (reproducibility_manifest.validate_and_gate, reusing Phase 2's frozen
    DataQualityValidator plus this package's ordering check) on every
    fetch before returning data. This is what makes "validate imported
    data before use" apply uniformly to every provider through
    composition, rather than needing every individual provider
    implementation to remember to call the gate itself."""

    def __init__(self, wrapped_provider, holidays=frozenset()):
        self._wrapped = wrapped_provider
        self._holidays = holidays

    @property
    def name(self):
        return f"{self._wrapped.name}_validated"

    def fetch_ohlcv(self, symbol, start, end):
        bars = self._wrapped.fetch_ohlcv(symbol, start, end)
        if not bars:
            return bars
        validate_and_gate(symbol, bars, start, end, holidays=self._holidays)
        return bars

    def fetch_corporate_actions(self, symbol, start, end):
        return self._wrapped.fetch_corporate_actions(symbol, start, end)

    def fetch_instrument_master(self):
        return self._wrapped.fetch_instrument_master()
