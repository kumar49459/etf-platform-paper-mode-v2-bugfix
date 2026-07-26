from src.etf_platform.system.status import SystemStatus
from src.etf_platform.portfolio.portfolio_manager import Portfolio

status = SystemStatus().get_status()
portfolio = Portfolio()

print("=" * 60)
print("      KRISHNA'S INDIAN ETF PLATFORM")
print("                 Version 0.3")
print("=" * 60)

print("\nSYSTEM STATUS")
print("-" * 60)
print(f"Python            : {status['python']}")
print(f"Database          : {status['database']}")
print("Paper Trading     : 🟢 Ready")
print("AWS               : ⚪ Not Connected")
print("Zerodha           : ⚪ Not Connected")

print("\nDATABASE")
print("-" * 60)
print(f"Orders Stored     : {status['orders']}")
print(f"Orders Filled     : {status['filled']}")
print(f"Orders Pending    : {status['pending']}")

print("\nPORTFOLIO")
print("-" * 60)
print(f"Cash Available    : ₹{portfolio.cash:,.2f}")
print(f"Invested Value    : ₹{portfolio.invested_value():,.2f}")
print(f"Portfolio Value   : ₹{portfolio.portfolio_value():,.2f}")
print(f"Today's P/L       : ₹{portfolio.today_pl():,.2f}")
print(f"Holdings          : {portfolio.total_holdings()}")

print("\nNEXT STEP")
print("-" * 60)
print("Waiting for Strategy Engine...")

print("=" * 60)