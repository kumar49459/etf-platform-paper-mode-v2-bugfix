from etf_platform.market.historical_data import HistoricalData
from etf_platform.analytics.performance import PerformanceAnalytics


class SmartSIP:
    """
    Smart SIP Strategy

    Rule:
    Increase SIP investment when the market falls by a
    configurable percentage from the previous close.
    """

    def __init__(self):
        self._history = HistoricalData()

    def run(
        self,
        symbol,
        monthly_investment,
        dip_threshold=5.0,
        dip_multiplier=2.0,
    ):

        rows = self._history.get_history(symbol)

        total_units = 0.0
        total_investment = 0.0

        previous_close = None

        for row in rows:

            investment = monthly_investment

            if previous_close is not None:

                fall_percent = (
                    (previous_close - row["close"])
                    / previous_close
                ) * 100

                if fall_percent >= dip_threshold:
                    investment *= dip_multiplier

            total_units += investment / row["close"]
            total_investment += investment

            previous_close = row["close"]

        portfolio_value = total_units * rows[-1]["close"]

        result = PerformanceAnalytics.calculate(
            total_investment,
            portfolio_value
        )

        result["strategy"] = "SMART_SIP"

        return result
