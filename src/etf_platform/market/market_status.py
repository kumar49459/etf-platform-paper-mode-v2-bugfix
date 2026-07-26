from datetime import datetime, time


class MarketStatus:
    def __init__(self):
        self.now = datetime.now()

    def is_market_open(self):
        weekday = self.now.weekday()  # Monday = 0

        if weekday >= 5:
            return False

        market_open = time(9, 15)
        market_close = time(15, 30)

        return market_open <= self.now.time() <= market_close

    def status(self):
        return "🟢 OPEN" if self.is_market_open() else "🔴 CLOSED"

    def current_time(self):
        return self.now.strftime("%d-%m-%Y %H:%M:%S")
