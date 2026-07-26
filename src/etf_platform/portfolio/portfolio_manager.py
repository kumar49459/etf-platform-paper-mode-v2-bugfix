from dataclasses import dataclass, field


@dataclass
class Holding:
    symbol: str
    quantity: int
    average_price: float

    @property
    def market_value(self):
        return self.quantity * self.average_price


@dataclass
class Portfolio:
    cash: float = 1_000_000.00
    holdings: dict[str, Holding] = field(default_factory=dict)

    def total_holdings(self):
        return len(self.holdings)

    def invested_value(self):
        return sum(h.market_value for h in self.holdings.values())

    def portfolio_value(self):
        return self.cash + self.invested_value()

    def today_pl(self):
        return 0.00