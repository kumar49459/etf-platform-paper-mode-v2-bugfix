class PortfolioAdvisor:
    """
    Provides simple portfolio health analysis and recommendations.
    """

    def analyze(self, summary):
        score = 100
        recommendations = []

        if summary["return_percent"] < 0:
            score -= 25
            recommendations.append(
                "Portfolio is in loss. Review current positions."
            )

        if summary["cash"] > summary["portfolio_value"]:
            score -= 10
            recommendations.append(
                "Large cash allocation. Consider investing available cash."
            )

        if summary["trade_count"] == 0:
            score -= 20
            recommendations.append(
                "No trades executed yet."
            )

        score = max(score, 0)

        return {
            "health_score": score,
            "recommendations": recommendations,
        }
