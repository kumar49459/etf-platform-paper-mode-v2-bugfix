from datetime import datetime


class ReportEngine:
    """
    Standardized report generator for backtest results.
    """

    def generate(self, strategy, result):
        return {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "strategy": strategy,
            "summary": {
                "investment": result.get("investment", 0),
                "portfolio_value": result.get("portfolio_value", 0),
                "profit": result.get("profit", 0),
                "return_percent": result.get("return_percent", 0),
            },
            "details": result,
        }
