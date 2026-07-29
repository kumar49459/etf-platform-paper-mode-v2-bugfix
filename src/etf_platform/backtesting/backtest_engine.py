from etf_platform.market.historical_data import HistoricalData


class BacktestEngine:

    def __init__(self):
        self._history = HistoricalData()

    def buy_and_hold(self, symbol):
        rows = self._history.get_history(symbol)

        if len(rows) < 2:
            raise ValueError("Not enough historical data")

        first = rows[0]
        last = rows[-1]

        buy_price = first["close"]
        sell_price = last["close"]

        return {
            "strategy": "BUY_AND_HOLD",
            "symbol": symbol.upper(),
            "buy_price": buy_price,
            "sell_price": sell_price,
            "return_percent":
                round(((sell_price - buy_price) / buy_price) * 100, 2),
            "days": len(rows),
        }

    def sip(self, symbol, investment_per_period):
        rows = self._history.get_history(symbol)

        total_units = 0.0
        total_investment = 0.0

        for row in rows:
            units = investment_per_period / row["close"]
            total_units += units
            total_investment += investment_per_period

        final_value = total_units * rows[-1]["close"]

        return {
            "strategy": "SIP",
            "symbol": symbol.upper(),
            "investment": round(total_investment, 2),
            "portfolio_value": round(final_value, 2),
            "profit": round(final_value - total_investment, 2),
            "return_percent":
                round(
                    ((final_value - total_investment)
                     / total_investment) * 100,
                    2,
                ),
        }
