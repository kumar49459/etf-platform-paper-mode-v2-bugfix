import math


class SortinoRatioCalculator:
    """
    Calculates the annualized Sortino Ratio.
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

        downside = [
            min(0, r)
            for r in excess_returns
        ]

        downside_variance = (
            sum(d ** 2 for d in downside)
            / len(downside)
        )

        downside_deviation = math.sqrt(
            downside_variance
        )

        if downside_deviation == 0:
            raise ValueError(
                "Downside deviation is zero"
            )

        sortino = (
            mean_excess /
            downside_deviation
        ) * math.sqrt(periods_per_year)

        return {
            "sortino_ratio": sortino,
            "risk_free_rate": risk_free_rate,
        }
