class MarketConditionAnalyzer:
    """
    Classifies overall market condition using trend and volatility.
    """

    def analyze(self, trend, volatility):
        trend = trend.upper()

        if trend == "UP":
            if volatility < 15:
                return {
                    "market_condition": "BULL",
                    "recommendation": "Normal investment can continue.",
                }

            return {
                "market_condition": "VOLATILE_BULL",
                "recommendation": "Invest gradually using SIP or staggered buying.",
            }

        if trend == "DOWN":
            if volatility > 20:
                return {
                    "market_condition": "BEAR",
                    "recommendation": "Protect capital and invest cautiously.",
                }

            return {
                "market_condition": "CORRECTION",
                "recommendation": "Good opportunity for disciplined accumulation.",
            }

        return {
            "market_condition": "SIDEWAYS",
            "recommendation": "Maintain allocation and wait for a clear trend.",
        }
