from etf_platform.strategy.strategy_engine import StrategyEngine
from etf_platform.reporting.report_engine import ReportEngine


class BacktestSession:
    """
    Executes a complete backtest workflow.

    Flow:
        Strategy Engine
              ↓
        Performance Analytics
              ↓
        Report Engine
    """

    def __init__(self):
        self._strategy_engine = StrategyEngine()
        self._report_engine = ReportEngine()

    def run(self, strategy, **kwargs):
        result = self._strategy_engine.run(
            strategy=strategy,
            **kwargs
        )

        report = self._report_engine.generate(
            strategy=strategy,
            result=result
        )

        return report
