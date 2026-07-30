from api.repositories.holding_repository import HoldingRepository


class HoldingService:
    def __init__(self, db):
        self.repository = HoldingRepository(db)

    def get_all_holdings(self):
        holdings = self.repository.get_all()

        if not holdings:
            return []

        total_market_value = sum(h.units * h.current_price for h in holdings)

        result = []

        for h in holdings:
            market_value = h.units * h.current_price
            invested_value = h.units * h.avg_cost
            gain = market_value - invested_value
            gain_percent = (
                (gain / invested_value) * 100
                if invested_value else 0
            )
            day_change = (h.current_price - h.previous_close) * h.units
            day_change_percent = (
                ((h.current_price - h.previous_close) / h.previous_close) * 100
                if h.previous_close else 0
            )
            weight = (
                (market_value / total_market_value) * 100
                if total_market_value else 0
            )

            result.append({
                "symbol": h.symbol,
                "name": h.name,
                "sector": h.sector,
                "country": h.country,
                "units": h.units,
                "avgCost": h.avg_cost,
                "currentPrice": h.current_price,
                "previousClose": h.previous_close,
                "dividendYield": h.dividend_yield,
                "marketValue": market_value,
                "investedValue": invested_value,
                "gain": gain,
                "gainPercent": gain_percent,
                "dayChange": day_change,
                "dayChangePercent": day_change_percent,
                "weight": weight,
            })

        return result
