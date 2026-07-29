class AIDecisionReport:
    """
    Combines all AI outputs into a single report.
    """

    def generate(
        self,
        health,
        risk,
        allocation,
        market,
        strategy,
    ):
        return {
            "health": health,
            "risk": risk,
            "allocation": allocation,
            "market": market,
            "strategy": strategy,
            "summary": {
                "health_score": health["health_score"],
                "risk_level": risk["risk_level"],
                "market_condition": market["market_condition"],
                "recommended_strategy": strategy["strategy"],
            },
        }
