from etf_platform.execution.cash_manager import CashManager
from etf_platform.execution.holdings_manager import HoldingsManager
from etf_platform.execution.portfolio_valuation import PortfolioValuation
from etf_platform.execution.profit_loss_engine import ProfitLossEngine
from etf_platform.execution.trade_ledger import TradeLedger


class PaperTradingEngine:
    """
    Integrates cash, holdings, trades, valuation and P&L.
    """

    def __init__(self, initial_cash):
        self.initial_cash = float(initial_cash)
        self.cash = CashManager(initial_cash)
        self.ledger = TradeLedger()
        self.holdings = HoldingsManager()
        self.valuation = PortfolioValuation()
        self.pnl = ProfitLossEngine()

    def buy(
        self,
        symbol,
        quantity,
        price,
        timestamp,
    ):
        cost = float(quantity) * float(price)

        self.cash.withdraw(cost)

        self.holdings.buy(symbol, quantity)

        self.ledger.add_trade(
            symbol=symbol,
            side="BUY",
            quantity=quantity,
            price=price,
            timestamp=timestamp,
        )

    def portfolio_summary(self, prices):
        portfolio_value = self.valuation.calculate(
            self.holdings.all_holdings(),
            prices,
        )

        pnl = self.pnl.calculate(
            initial_cash=self.initial_cash,
            current_cash=self.cash.balance,
            portfolio_value=portfolio_value,
        )

        return {
            "cash": round(self.cash.balance, 2),
            "portfolio_value": portfolio_value,
            "holdings": self.holdings.all_holdings(),
            "trade_count": self.ledger.count(),
            **pnl,
        }
