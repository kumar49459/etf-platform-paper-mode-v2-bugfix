class ProfitLossEngine:
    """
    Calculates portfolio profit/loss.
    """

    def calculate(
        self,
        initial_cash,
        current_cash,
        portfolio_value,
    ):
        initial_cash = float(initial_cash)
        current_cash = float(current_cash)
        portfolio_value = float(portfolio_value)

        total_equity = current_cash + portfolio_value

        profit = total_equity - initial_cash

        if initial_cash == 0:
            return {
                "total_equity": round(total_equity, 2),
                "profit": round(profit, 2),
                "return_percent": 0.0,
            }

        return_percent = (
            profit / initial_cash
        ) * 100

        return {
            "total_equity": round(total_equity, 2),
            "profit": round(profit, 2),
            "return_percent": round(return_percent, 2),
        }
