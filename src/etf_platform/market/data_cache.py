class DataCache:
    """
    Simple in-memory cache for historical market data.

    Version 1.0
    """

    def __init__(self):
        self._cache = {}

    def get(self, symbol):
        return self._cache.get(symbol.upper())

    def put(self, symbol, data):
        self._cache[symbol.upper()] = data

    def contains(self, symbol):
        return symbol.upper() in self._cache

    def clear(self):
        self._cache.clear()

    def size(self):
        return len(self._cache)
