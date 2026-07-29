class PortfolioAllocation:
    """
    Portfolio Allocation Model
    Version 1.0
    """

    def __init__(self):
        self._weights = {}

    def set_weight(self, symbol, percent):
        symbol = symbol.upper()

        if percent < 0:
            raise ValueError("Weight cannot be negative")

        self._weights[symbol] = float(percent)

    def total_weight(self):
        return round(sum(self._weights.values()), 2)

    def is_valid(self):
        return abs(self.total_weight() - 100.0) < 0.001

    def weights(self):
        return dict(self._weights)

    def allocation_amounts(self, investment):
        if not self.is_valid():
            raise ValueError("Allocation must total 100%")

        return {
            symbol: round(investment * weight / 100.0, 2)
            for symbol, weight in self._weights.items()
        }
