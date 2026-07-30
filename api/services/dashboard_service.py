from api.repositories.holding_repository import HoldingRepository


class DashboardService:
    CASH_BALANCE = 184320.0

    def __init__(self, db):
        self.repository = HoldingRepository(db)

    def get_summary(self):
        holdings = self.repository.get_all()

        invested = sum(h.units * h.avg_cost for h in holdings)
        market = sum(h.units * h.current_price for h in holdings)
        day_change = sum(
            (h.current_price - h.previous_close) * h.units
            for h in holdings
        )

        total_return = market - invested

        return {
            "totalValue": market + self.CASH_BALANCE,
            "investedValue": invested,
            "cash": self.CASH_BALANCE,
            "todayPnL": day_change,
            "todayPnLPercent": (
                (day_change / (market - day_change)) * 100
                if market > day_change else 0
            ),
            "totalReturn": total_return,
            "totalReturnPercent": (
                (total_return / invested) * 100
                if invested else 0
            ),
            "xirr": 16.82,
            "cagr": 14.37,
            "healthScore": 78,
            "aiConfidence": 71,
        }
