class CalmarRatioCalculator:
    """
    Calculates the Calmar Ratio.

    Calmar Ratio = CAGR / Maximum Drawdown
    """

    def calculate(self, cagr, max_drawdown):
        if max_drawdown <= 0:
            raise ValueError(
                "Maximum drawdown must be greater than zero"
            )

        calmar = cagr / max_drawdown

        return {
            "cagr": cagr,
            "max_drawdown": max_drawdown,
            "calmar_ratio": round(calmar, 4),
        }
