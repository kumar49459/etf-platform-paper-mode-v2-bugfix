from etf_platform.analytics.cagr import CAGRCalculator
from etf_platform.analytics.xirr import XIRRCalculator
from etf_platform.analytics.max_drawdown import MaximumDrawdownCalculator
from etf_platform.analytics.volatility import VolatilityCalculator
from etf_platform.analytics.sharpe_ratio import SharpeRatioCalculator
from etf_platform.analytics.sortino_ratio import SortinoRatioCalculator
from etf_platform.analytics.calmar_ratio import CalmarRatioCalculator


class AnalyticsEngine:
    """
    Unified analytics engine for portfolio performance analysis.
    """

    def __init__(self):
        self.cagr = CAGRCalculator()
        self.xirr = XIRRCalculator()
        self.drawdown = MaximumDrawdownCalculator()
        self.volatility = VolatilityCalculator()
        self.sharpe = SharpeRatioCalculator()
        self.sortino = SortinoRatioCalculator()
        self.calmar = CalmarRatioCalculator()

    def analyze(
        self,
        *,
        initial_value,
        final_value,
        start_date,
        end_date,
        cashflows,
        portfolio_values,
        returns,
        risk_free_rate=0.06,
    ):
        cagr_result = self.cagr.calculate(
            initial_value,
            final_value,
            start_date,
            end_date,
        )

        xirr_result = self.xirr.calculate(
            cashflows
        )

        drawdown_result = self.drawdown.calculate(
            portfolio_values
        )

        volatility_result = self.volatility.calculate(
            returns
        )

        sharpe_result = self.sharpe.calculate(
            returns,
            risk_free_rate=risk_free_rate,
        )

        sortino_result = self.sortino.calculate(
            returns,
            risk_free_rate=risk_free_rate,
        )

        calmar_result = self.calmar.calculate(
            cagr=cagr_result["cagr"],
            max_drawdown=drawdown_result["max_drawdown"],
        )

        return {
            "cagr": cagr_result,
            "xirr": xirr_result,
            "drawdown": drawdown_result,
            "volatility": volatility_result,
            "sharpe": sharpe_result,
            "sortino": sortino_result,
            "calmar": calmar_result,
        }
