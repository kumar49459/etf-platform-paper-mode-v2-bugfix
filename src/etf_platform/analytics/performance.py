class PerformanceAnalytics:
    """
    Version 1.0

    Common performance calculations shared by all
    backtesting strategies.
    """

    @staticmethod
    def calculate(total_investment, portfolio_value):
        profit = portfolio_value - total_investment

        if total_investment == 0:
            return_percent = 0.0
        else:
            return_percent = (
                profit / total_investment
            ) * 100

        return {
            "investment": round(total_investment, 2),
            "portfolio_value": round(portfolio_value, 2),
            "profit": round(profit, 2),
            "return_percent": round(return_percent, 2),
        }
