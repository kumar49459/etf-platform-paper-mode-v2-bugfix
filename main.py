from etf_platform.application.app_controller import AppController


class DummyStrategyEngine:
    def run(self, context):
        return {
            "status": "success",
            "context": context,
            "analytics_input": {
                "initial_value": 100000,
                "final_value": 180000,
                "start_date": "2020-01-01",
                "end_date": "2025-01-01",
                "cashflows": [
                    ("2020-01-01", -100000),
                    ("2021-01-01", -50000),
                    ("2025-01-01", 220000),
                ],
                "portfolio_values": [
                    100000,
                    120000,
                    118000,
                    90000,
                    140000,
                ],
                "returns": [
                    0.010,
                    -0.005,
                    0.012,
                    0.004,
                    -0.003,
                    0.008,
                    0.015,
                ],
            },
        }


from etf_platform.analytics.analytics_engine import AnalyticsEngine
from etf_platform.reporting.performance_report import PerformanceReport


def main():
    app = AppController(
        strategy_engine=DummyStrategyEngine(),
        analytics_engine=AnalyticsEngine(),
        report_engine=PerformanceReport(),
    )

    result = app.run(
        {
            "mode": "paper",
        }
    )

    print("=" * 60)
    print("ETF PLATFORM")
    print("=" * 60)
    print(result["report"]["summary"])


if __name__ == "__main__":
    main()
