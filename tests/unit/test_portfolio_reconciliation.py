from datetime import UTC, datetime

from etf_platform.execution_manager.models import (
    ExecutionRecord,
    OrderLifecycleState,
)
from etf_platform.portfolio.portfolio_reconciliation import (
    PortfolioReconciliation,
)


class FakeStore:

    def load_reconciled_records(self):
        return [
            ExecutionRecord(
                execution_id="1",
                queue_id="Q1",
                cycle_id="C1",
                symbol="MON100",
                quantity_proposed=10,
                quantity_final=10,
                limit_price=340.0,
                order_status=OrderLifecycleState.RECONCILED,
                broker_order_id="B1",
                executed_price=342.0,
                executed_quantity=10,
                is_paper_trade=True,
                created_at=datetime.now(UTC),
                last_status_check=None,
                priority_rank=1,
                notes=(),
            )
        ]


def test_build_portfolio():

    portfolio = PortfolioReconciliation(FakeStore()).build_portfolio()

    assert portfolio.total_holdings() == 1

    holding = portfolio.holdings["MON100"]

    assert holding.quantity == 10
    assert holding.average_price == 342.0
    assert holding.current_price == 342.0
