from dataclasses import dataclass, field


@dataclass
class Holding:
    symbol: str
    quantity: int
    average_price: float
    current_price: float = 0.0

    @property
    def invested_value(self):
        return self.quantity * self.average_price

    @property
    def market_value(self):
        return self.quantity * self.current_price

    @property
    def unrealized_pl(self):
        return self.market_value - self.invested_value

    @property
    def return_percent(self):
        if self.invested_value == 0:
            return 0.0
        return (self.unrealized_pl / self.invested_value) * 100


@dataclass
class Portfolio:
    cash: float = 1_000_000.00
    holdings: dict[str, Holding] = field(default_factory=dict)

    def total_holdings(self):
        return len(self.holdings)

    def invested_value(self):
        return sum(h.invested_value for h in self.holdings.values())

    def market_value(self):
        return sum(h.market_value for h in self.holdings.values())

    def portfolio_value(self):
        return self.cash + self.market_value()

    def today_pl(self):
        return sum(h.unrealized_pl for h in self.holdings.values())

    def allocation(self):
        total = self.market_value()
        if total == 0:
            return {}

        return {
            symbol: (holding.market_value / total) * 100
            for symbol, holding in self.holdings.items()
        }

    def add_holding(
        self,
        symbol: str,
        quantity: int,
        average_price: float,
        current_price: float,
    ):
        self.holdings[symbol] = Holding(
            symbol=symbol,
            quantity=quantity,
            average_price=average_price,
            current_price=current_price,
        )

    def update_price(self, symbol: str, current_price: float):
        if symbol in self.holdings:
            self.holdings[symbol].current_price = current_price
