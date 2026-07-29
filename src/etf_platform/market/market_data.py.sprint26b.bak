from datetime import datetime


class MarketData:
    """
    Market Data Engine
    Version 0.6
    """

    def __init__(self):
        self.last_updated = datetime.now()

    def nifty50(self):
        return 25250.35

    def banknifty(self):
        return 57340.20

    def india_vix(self):
        return 13.42

    def market_trend(self):
        return "BULLISH"

    def market_sentiment(self):
        return "POSITIVE"

    def etf_price(self, symbol):
        prices = {
            "MON100": 340.50,
            "NIFTYBEES": 284.15,
            "BANKBEES": 612.75,
            "GOLDBEES": 82.45,
            "CPSEETF": 71.20,
        }

        return prices.get(symbol.upper(), 0.00)

    def last_update(self):
        return self.last_updated.strftime("%d-%m-%Y %H:%M:%S")
