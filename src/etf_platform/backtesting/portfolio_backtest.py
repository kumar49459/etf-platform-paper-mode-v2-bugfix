from etf_platform.market.historical_data import HistoricalData
from etf_platform.analytics.performance import PerformanceAnalytics


class PortfolioBacktest:

    def __init__(self):
        self._history = HistoricalData()

    def sip(self, allocations, investment_per_period):

        total_investment = 0.0
        portfolio_value = 0.0

        for symbol, weight in allocations.items():

            # Explicitly validate that history exists
            rows = self._history.get_history(symbol)

            amount = investment_per_period * weight / 100.0

            units = 0.0

            for row in rows:
                units += amount / row["close"]
                total_investment += amount

            portfolio_value += units * rows[-1]["close"]

        result = PerformanceAnalytics.calculate(
            total_investment,
            portfolio_value,
        )

        result["strategy"] = "PORTFOLIO_SIP"

        return result
