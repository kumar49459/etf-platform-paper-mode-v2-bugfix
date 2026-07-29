class PortfolioRebalancer:
    """
    Portfolio Rebalancer
    Version 1.0
    """

    def rebalance(self, portfolio_value, current_weights, target_weights):
        result = {}

        for symbol, target in target_weights.items():
            current = current_weights.get(symbol, 0.0)

            current_value = portfolio_value * current / 100.0
            target_value = portfolio_value * target / 100.0

            difference = round(target_value - current_value, 2)

            result[symbol] = {
                "current_percent": round(current, 2),
                "target_percent": round(target, 2),
                "trade_amount": difference,
                "action": (
                    "BUY"
                    if difference > 0
                    else "SELL"
                    if difference < 0
                    else "HOLD"
                ),
            }

        return result
