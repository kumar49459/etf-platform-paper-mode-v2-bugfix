from src.etf_platform.system.status import SystemStatus
from src.etf_platform.portfolio.portfolio_manager import Portfolio


class Dashboard:
    def __init__(self):
        self.status = SystemStatus()
        self.portfolio = Portfolio()

    def show(self):
        print("=" * 60)
        print("KRISHNA'S INDIAN ETF PLATFORM")
        print("Version 0.4 - Dashboard")
        print("=" * 60)

        print("\nSYSTEM STATUS")
        print("Python            : 🟢 Running")
        print(f"Database          : {'🟢 Connected' if self.status.database_connected() else '🔴 Offline'}")
        print("Paper Trading     : 🟢 Ready")
        print("AWS               : ⚪ Not Connected")
        print("Zerodha           : ⚪ Not Connected")

        print("\nDATABASE")
        print(f"Orders Stored     : {self.status.total_orders()}")
        print(f"Orders Filled     : {self.status.reconciled_orders()}")
        print(f"Orders Pending    : {self.status.pending_orders()}")

        print("\nPORTFOLIO")
        print(f"Cash Available    : ₹{self.portfolio.cash:,.2f}")
        print(f"Invested Value    : ₹{self.portfolio.invested_value():,.2f}")
        print(f"Portfolio Value   : ₹{self.portfolio.portfolio_value():,.2f}")
        print(f"Today's P/L       : ₹{self.portfolio.today_pl():,.2f}")
        print(f"Holdings          : {len(self.portfolio.holdings)}")

        print("\nNEXT STEP")
        print("Waiting for Strategy Engine...")

        print("=" * 60)
