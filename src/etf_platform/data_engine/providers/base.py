"""Abstract DataProvider interface.

Binding design constraint (Phase 1 §12.6): no module outside the Data Engine
may import a source-specific client directly. Everything goes through this
interface, implemented by NSEProvider, KiteProvider, and — later — a paid
vendor adapter, without any change to callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from etf_platform.data_engine.models import CorporateAction, InstrumentMeta, OHLCVBar


class DataProvider(ABC):
    """Common interface every historical data source adapter must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short, stable provider identifier, e.g. 'nse' or 'kite'. Used as the
        `source` field on emitted records and in logs/quality reports."""

    @abstractmethod
    def fetch_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCVBar]:
        """Fetch daily OHLCV bars for `symbol` in [start, end], inclusive.
        Must raise DataProviderError (not a bare exception) on failure, so
        callers can distinguish "no data available" from "provider is down"."""

    @abstractmethod
    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        """Fetch corporate actions for `symbol` in [start, end], inclusive."""

    @abstractmethod
    def fetch_instrument_master(self) -> list[InstrumentMeta]:
        """Fetch the provider's full instrument/ETF metadata listing.
        For NSE this is symbol/name/exchange level; for Kite it additionally
        carries instrument_token, which SymbolResolver depends on."""
