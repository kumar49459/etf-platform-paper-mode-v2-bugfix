from src.etf_platform.market.market_status import MarketStatus
from src.etf_platform.market.market_data import MarketData
from src.etf_platform.system.status import SystemStatus
from src.etf_platform.portfolio.portfolio_manager import Portfolio


class Dashboard:

    def __init__(self):
        self.market = MarketStatus()
        self.market_data = MarketData()
        self.status = SystemStatus()
        self.portfolio = Portfolio()

    def show(self):

        print("=" * 60)
        print("KRISHNA'S INDIAN ETF PLATFORM")
        print("Version 0.6 - Dashboard")
        print("=" * 60)

        print("\nMARKET")
        print(f"Status            : {'🟢 OPEN' if self.market.is_market_open() else '🔴 CLOSED'}")
        print(f"Current Time      : {self.market.current_time()}")

        print("\nMARKET DATA")
        print(f"NIFTY 50          : {self.market_data.nifty50():,.2f}")
        print(f"Bank Nifty        : {self.market_data.banknifty():,.2f}")
        print(f"India VIX         : {self.market_data.india_vix():.2f}")
        print(f"MON100            : ₹{self.market_data.etf_price('MON100'):.2f}")
        print(f"NIFTYBEES         : ₹{self.market_data.etf_price('NIFTYBEES'):.2f}")
        print(f"Market Trend      : {self.market_data.market_trend()}")
        print(f"Market Sentiment  : {self.market_data.market_sentiment()}")
        print(f"Last Updated      : {self.market_data.last_update()}")

        print("\nSYSTEM STATUS")
        print("Python            : 🟢 Running")
        print(f"Database          : {'🟢 Connected' if self.status.database_connected() else '🔴 Disconnected'}")
        print("Paper Trading     : 🟢 Ready")
        print("AWS               : ⚪ Not Connected")
        print("Zerodha           : ⚪ Not Connected")

        print("\nDATABASE")
        print(f"Orders Stored     : {self.status.total_orders()}")
        print(f"Orders Filled     : {self.status.reconciled_orders()}")
        print(f"Orders Pending    : {self.status.pending_orders()}")

        print("\nPORTFOLIO")
        print(f"Cash Available    : ₹{self.portfolio.cash_balance():,.2f}")
        print(f"Invested Value    : ₹{self.portfolio.invested_value():,.2f}")
        print(f"Portfolio Value   : ₹{self.portfolio.total_value():,.2f}")
        print(f"Today's P/L       : ₹{self.portfolio.today_pl():,.2f}")
        print(f"Holdings          : {self.portfolio.total_holdings()}")

        print("\nNEXT STEP")
        print("Waiting for Strategy Engine...")

        print("=" * 60)
