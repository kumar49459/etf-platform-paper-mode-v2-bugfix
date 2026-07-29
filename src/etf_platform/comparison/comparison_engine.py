from etf_platform.strategy.strategy_engine import StrategyEngine


class ComparisonEngine:
    """
    Compare multiple investment strategies.

    Version 1.0
    """

    def __init__(self):
        self._engine = StrategyEngine()

    def compare(self, strategies):
        """
        strategies = [
            {
                "strategy": "BUY_AND_HOLD",
                "symbol": "NIFTYBEES"
            },
            {
                "strategy": "SIP",
                "symbol": "NIFTYBEES",
                "investment_per_period": 20000
            }
        ]
        """

        results = []

        for config in strategies:
            config = dict(config)

            strategy = config.pop("strategy")

            result = self._engine.run(
                strategy=strategy,
                **config
            )

            results.append({
                "strategy": strategy,
                "result": result
            })

        return results
