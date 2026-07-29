class PortfolioValuation:
    """
    Calculates the current market value of a portfolio.
    """

    def calculate(self, holdings, prices):
        total = 0.0

        for symbol, quantity in holdings.items():
            if symbol not in prices:
                raise KeyError(f"Missing market price for {symbol}")

            total += quantity * float(prices[symbol])

        return round(total, 2)
