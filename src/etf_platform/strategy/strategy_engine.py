from etf_platform.backtesting.backtest_engine import BacktestEngine
from etf_platform.backtesting.portfolio_backtest import PortfolioBacktest


class StrategyEngine:
    """
    Central Strategy Engine

    Dispatches different investment strategies through
    a single interface.
    """

    def __init__(self):
        self._single = BacktestEngine()
        self._portfolio = PortfolioBacktest()

    def run(self, strategy, **kwargs):
        strategy = strategy.upper()

        if strategy == "BUY_AND_HOLD":
            return self._single.buy_and_hold(
                kwargs["symbol"]
            )

        elif strategy == "SIP":
            return self._single.sip(
                kwargs["symbol"],
                kwargs["investment_per_period"],
            )

        elif strategy == "PORTFOLIO_SIP":
            return self._portfolio.sip(
                kwargs["allocations"],
                kwargs["investment_per_period"],
            )

        raise ValueError(f"Unknown strategy: {strategy}")
