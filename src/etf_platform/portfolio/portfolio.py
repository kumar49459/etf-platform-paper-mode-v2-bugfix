class Portfolio:
    """
    Portfolio Model
    Version 1.0

    Stores ETF holdings and calculates
    portfolio value.
    """

    def __init__(self):
        self._holdings = {}

    def add(self, symbol, units):
        symbol = symbol.upper()

        self._holdings[symbol] = (
            self._holdings.get(symbol, 0.0)
            + units
        )

    def remove(self, symbol, units):
        symbol = symbol.upper()

        if symbol not in self._holdings:
            raise ValueError(f"{symbol} not found")

        self._holdings[symbol] -= units

        if self._holdings[symbol] <= 0:
            del self._holdings[symbol]

    def holdings(self):
        return dict(self._holdings)

    def total_units(self):
        return round(sum(self._holdings.values()), 6)

    def value(self, prices):
        total = 0.0

        for symbol, units in self._holdings.items():
            total += units * prices.get(symbol, 0.0)

        return round(total, 2)
