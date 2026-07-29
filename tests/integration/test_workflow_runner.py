from pathlib import Path

from etf_platform.workflow.workflow_runner import WorkflowRunner


class DummyStrategyEngine:
    def run(self, context):
        return {
            "analytics_input": {
                "initial_value": 100000,
                "final_value": 120000,
                "start_date": "2025-01-01",
                "end_date": "2026-01-01",
                "cashflows": [],
                "portfolio_values": [100000, 110000, 120000],
                "returns": [0.01, 0.02],
            }
        }


class DummyAnalyticsEngine:
    def analyze(self, **kwargs):
        return {
            "summary": {
                "return_percent": 20.0,
            }
        }


class DummyAIEngine:
    def generate(self, analytics):
        return {
            "summary": {
                "recommended_strategy": "HOLD"
            }
        }


class DummyDashboard:
    def generate(self, analytics):
        return Path("reports/dashboard.html")


def test_complete_workflow():
    runner = WorkflowRunner(
        strategy_engine=DummyStrategyEngine(),
        paper_engine=None,
        analytics_engine=DummyAnalyticsEngine(),
        ai_engine=DummyAIEngine(),
        dashboard=DummyDashboard(),
    )

    result = runner.run({})

    assert "strategy" in result
    assert "analytics" in result
    assert "ai" in result
    assert "dashboard" in result
