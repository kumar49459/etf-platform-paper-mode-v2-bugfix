class WorkflowRunner:
    """
    Executes the complete ETF platform workflow.
    """

    def __init__(
        self,
        strategy_engine,
        paper_engine,
        analytics_engine,
        ai_engine,
        dashboard,
    ):
        self.strategy_engine = strategy_engine
        self.paper_engine = paper_engine
        self.analytics_engine = analytics_engine
        self.ai_engine = ai_engine
        self.dashboard = dashboard

    def run(self, context):
        strategy = self.strategy_engine.run(context)

        analytics = self.analytics_engine.analyze(
            **strategy["analytics_input"]
        )

        ai = self.ai_engine.generate(analytics)

        dashboard = self.dashboard.generate(
            analytics
        )

        return {
            "strategy": strategy,
            "analytics": analytics,
            "ai": ai,
            "dashboard": dashboard,
        }
