import math


class SharpeRatioCalculator:
    """
    Calculates the annualized Sharpe Ratio.
    """

    def calculate(
        self,
        returns,
        risk_free_rate=0.06,
        periods_per_year=252,
    ):
        if len(returns) < 2:
            raise ValueError(
                "At least two return values are required"
            )

        risk_free_per_period = (
            (1 + risk_free_rate) **
            (1 / periods_per_year)
            - 1
        )

        excess_returns = [
            r - risk_free_per_period
            for r in returns
        ]

        mean_excess = (
            sum(excess_returns) /
            len(excess_returns)
        )

        variance = sum(
            (r - mean_excess) ** 2
            for r in excess_returns
        ) / (len(excess_returns) - 1)

        std_dev = math.sqrt(variance)

        if std_dev == 0:
            raise ValueError(
                "Standard deviation is zero"
            )

        sharpe = (
            mean_excess /
            std_dev
        ) * math.sqrt(periods_per_year)

        return {
            "sharpe_ratio": sharpe,
            "risk_free_rate": risk_free_rate,
        }
