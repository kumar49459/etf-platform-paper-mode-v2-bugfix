from dataclasses import dataclass, asdict
from typing import List


@dataclass
class Trade:
    symbol: str
    side: str
    quantity: float
    price: float
    timestamp: str


class TradeLedger:
    """
    Stores executed paper trades.
    """

    def __init__(self):
        self._trades: List[Trade] = []

    def add_trade(
        self,
        symbol,
        side,
        quantity,
        price,
        timestamp,
    ):
        trade = Trade(
            symbol=symbol,
            side=side.upper(),
            quantity=quantity,
            price=price,
            timestamp=timestamp,
        )

        self._trades.append(trade)

    def all_trades(self):
        return [asdict(t) for t in self._trades]

    def count(self):
        return len(self._trades)
