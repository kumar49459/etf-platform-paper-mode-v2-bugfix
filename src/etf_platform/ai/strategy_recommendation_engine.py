class StrategyRecommendationEngine:
    """
    Combines AI outputs into a single strategy recommendation.
    """

    def recommend(
        self,
        health,
        risk,
        market,
    ):
        recommendation = "HOLD"

        if (
            market["market_condition"] == "BULL"
            and risk["risk_level"] == "LOW"
            and health["health_score"] >= 90
        ):
            recommendation = "AGGRESSIVE BUY"

        elif (
            market["market_condition"] in (
                "VOLATILE_BULL",
                "CORRECTION",
            )
            and risk["risk_level"] != "HIGH"
        ):
            recommendation = "SYSTEMATIC BUY"

        elif market["market_condition"] == "BEAR":
            recommendation = "DEFENSIVE"

        return {
            "strategy": recommendation,
            "health_score": health["health_score"],
            "risk_level": risk["risk_level"],
            "market_condition": market["market_condition"],
        }
