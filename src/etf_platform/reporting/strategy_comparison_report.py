class StrategyComparisonReport:
    """
    Compare multiple strategy performance reports.
    """

    METRICS = [
        "cagr_percent",
        "xirr_percent",
        "max_drawdown_percent",
        "annualized_volatility_percent",
        "sharpe_ratio",
        "sortino_ratio",
        "calmar_ratio",
    ]

    def compare(self, reports):
        """
        reports:
            {
                "Buy & Hold": report,
                "Smart SIP": report,
                ...
            }
        """

        comparison = {}

        for metric in self.METRICS:
            comparison[metric] = {}

            for name, report in reports.items():
                comparison[metric][name] = report["summary"][metric]

        return comparison
