class RiskScoringEngine:
    """
    Evaluates portfolio risk.
    """

    def evaluate(self, summary):
        score = 0
        reasons = []

        cash = summary["cash"]
        portfolio = summary["portfolio_value"]
        trades = summary["trade_count"]

        total = cash + portfolio

        if total == 0:
            return {
                "risk_score": 100,
                "risk_level": "HIGH",
                "reasons": ["Portfolio has no capital."],
            }

        invested_ratio = portfolio / total

        if invested_ratio > 0.90:
            score += 40
            reasons.append("Very high market exposure.")

        elif invested_ratio > 0.70:
            score += 20
            reasons.append("High market exposure.")

        if trades < 3:
            score += 15
            reasons.append("Limited diversification.")

        if summary["return_percent"] < 0:
            score += 20
            reasons.append("Portfolio currently in loss.")

        if score <= 20:
            level = "LOW"
        elif score <= 50:
            level = "MEDIUM"
        else:
            level = "HIGH"

        return {
            "risk_score": score,
            "risk_level": level,
            "reasons": reasons,
        }
