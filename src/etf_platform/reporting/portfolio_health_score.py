class PortfolioHealthScore:
    """
    Calculates an overall portfolio health score (0-100)
    based on performance and risk metrics.
    """

    def calculate(self, summary):
        score = 0

        # CAGR (max 25)
        cagr = summary["cagr_percent"]
        score += min(max(cagr, 0), 25)

        # Sharpe (max 20)
        sharpe = summary["sharpe_ratio"]
        score += min(max(sharpe * 10, 0), 20)

        # Sortino (max 20)
        sortino = summary["sortino_ratio"]
        score += min(max(sortino * 8, 0), 20)

        # Calmar (max 15)
        calmar = summary["calmar_ratio"]
        score += min(max(calmar * 10, 0), 15)

        # Drawdown (max 20)
        drawdown = summary["max_drawdown_percent"]

        if drawdown <= 10:
            score += 20
        elif drawdown <= 20:
            score += 15
        elif drawdown <= 30:
            score += 10
        elif drawdown <= 40:
            score += 5

        return {
            "health_score": round(score, 2),
            "rating": self._rating(score),
        }

    def _rating(self, score):
        if score >= 90:
            return "Excellent"

        if score >= 75:
            return "Very Good"

        if score >= 60:
            return "Good"

        if score >= 40:
            return "Average"

        return "Poor"
