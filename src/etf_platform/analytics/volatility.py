import math


class VolatilityCalculator:
    """
    Calculates annualized volatility from periodic returns.
    """

    def calculate(self, returns, periods_per_year=252):
        if len(returns) < 2:
            raise ValueError(
                "At least two return values are required"
            )

        mean = sum(returns) / len(returns)

        variance = sum(
            (r - mean) ** 2
            for r in returns
        ) / (len(returns) - 1)

        std_dev = math.sqrt(variance)

        annualized = std_dev * math.sqrt(periods_per_year)

        return {
            "mean_return": mean,
            "daily_volatility": std_dev,
            "annualized_volatility": annualized,
            "annualized_volatility_percent": round(
                annualized * 100,
                2,
            ),
        }
