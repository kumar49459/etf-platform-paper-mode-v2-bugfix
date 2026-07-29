from etf_platform.config.universe_loader import ETFUniverse
from etf_platform.market.adapters.csv_adapter import CSVAdapter
from etf_platform.market.data_cache import DataCache


class HistoricalData:
    """
    Historical Data Engine
    Version 3.0

    Uses:
      - ETF Universe
      - CSV Adapter
      - In-memory cache
    """

    def __init__(self):
        self._universe = ETFUniverse()
        self._symbols = set(self._universe.all_symbols())

        self._adapter = CSVAdapter()
        self._cache = DataCache()

    def supported_symbols(self):
        return sorted(self._symbols)

    def is_supported(self, symbol):
        return symbol.upper() in self._symbols

    def clear_cache(self):
        self._cache.clear()

    def cache_size(self):
        return self._cache.size()

    def get_history(self, symbol, start_date=None, end_date=None):
        symbol = symbol.upper()

        if symbol not in self._symbols:
            raise ValueError(f"Unsupported ETF: {symbol}")

        if self._cache.contains(symbol):
            rows = self._cache.get(symbol)
        else:
            rows = self._adapter.get_history(symbol)
            self._cache.put(symbol, rows)

        if start_date:
            rows = [r for r in rows if r["date"] >= start_date]

        if end_date:
            rows = [r for r in rows if r["date"] <= end_date]

        return rows
