from datetime import datetime


class PerformanceReport:
    """
    Generates a structured performance report from analytics results.
    """

    def generate(self, analytics):
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "summary": {
                "cagr_percent":
                    analytics["cagr"]["cagr_percent"],
                "xirr_percent":
                    analytics["xirr"]["xirr_percent"],
                "max_drawdown_percent":
                    analytics["drawdown"]["max_drawdown_percent"],
                "annualized_volatility_percent":
                    analytics["volatility"]["annualized_volatility_percent"],
                "sharpe_ratio":
                    round(
                        analytics["sharpe"]["sharpe_ratio"],
                        4,
                    ),
                "sortino_ratio":
                    round(
                        analytics["sortino"]["sortino_ratio"],
                        4,
                    ),
                "calmar_ratio":
                    analytics["calmar"]["calmar_ratio"],
            },
            "details": analytics,
        }
