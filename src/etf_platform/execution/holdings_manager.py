class HoldingsManager:
    """
    Tracks portfolio holdings.
    """

    def __init__(self):
        self._holdings = {}

    def buy(self, symbol, quantity):
        quantity = float(quantity)
        self._holdings[symbol] = (
            self._holdings.get(symbol, 0.0) + quantity
        )

    def sell(self, symbol, quantity):
        quantity = float(quantity)

        if self._holdings.get(symbol, 0.0) < quantity:
            raise ValueError("Insufficient holdings")

        self._holdings[symbol] -= quantity

        if self._holdings[symbol] == 0:
            del self._holdings[symbol]

    def quantity(self, symbol):
        return self._holdings.get(symbol, 0.0)

    def all_holdings(self):
        return dict(self._holdings)
