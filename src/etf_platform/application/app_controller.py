class AppController:
    """
    Main application controller.

    This class coordinates the complete ETF platform workflow.
    """

    def __init__(
        self,
        strategy_engine,
        analytics_engine,
        report_engine,
    ):
        self.strategy_engine = strategy_engine
        self.analytics_engine = analytics_engine
        self.report_engine = report_engine

    def run(self, context):
        """
        Execute the complete workflow.

        Returns a structured application result.
        """

        strategy_result = self.strategy_engine.run(context)

        analytics = self.analytics_engine.analyze(
            **strategy_result["analytics_input"]
        )

        report = self.report_engine.generate(
            analytics
        )

        return {
            "strategy": strategy_result,
            "analytics": analytics,
            "report": report,
        }
