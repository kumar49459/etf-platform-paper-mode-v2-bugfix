"""Portfolio state tracking.

Cash-flow sign handling is deliberately explicit here rather than hidden in
a shared "net_amount" helper: a BUY's cash outflow is `gross_amount +
total_cost` (you pay the price plus costs); a SELL's cash inflow is
`gross_amount - total_cost` (you receive the price minus costs). These are
different formulas, not the same value with a sign flip applied
externally — computing them explicitly per side here, instead of via a
same-named property that means different things depending on who calls it,
removes a class of sign-error bug before it can exist.
"""

from __future__ import annotations

from datetime import date

from etf_platform.backtesting.exceptions import InvalidOrderError
from etf_platform.backtesting.models import EquityCurvePoint, Fill, Trade
from etf_platform.common.logging_setup import get_logger
from etf_platform.cost_tax_engine import CostTaxEngine, Side

logger = get_logger("backtesting.portfolio")


class InsufficientCashError(InvalidOrderError):
    """Raised when a BUY fill would push cash negative. The engine treats
    this as a rejected order (see engine.py), not a crash — a strategy
    proposing more than it can afford is a normal condition to guard
    against, not a bug."""


class OverSellError(InvalidOrderError):
    """Raised when a SELL fill would exceed the currently held quantity."""


class Portfolio:
    def __init__(self, initial_capital: float, cost_tax_engine: CostTaxEngine) -> None:
        self._cash = initial_capital
        self._positions: dict[str, float] = {}
        self._cost_tax_engine = cost_tax_engine

    @property
    def cash(self) -> float:
        return self._cash

    def position(self, symbol: str) -> float:
        return self._positions.get(symbol, 0.0)

    def positions(self) -> dict[str, float]:
        return dict(self._positions)

    def can_afford_buy(self, fill: Fill) -> bool:
        cash_out = fill.cost.gross_amount + fill.cost.total_cost
        return cash_out <= self._cash + 1e-6

    def can_afford_sell(self, fill: Fill) -> bool:
        return fill.quantity <= self.position(fill.symbol) + 1e-6

    def apply_fill(self, fill: Fill) -> Trade:
        if fill.side == Side.BUY:
            cash_out = fill.cost.gross_amount + fill.cost.total_cost
            if cash_out > self._cash + 1e-6:
                raise InsufficientCashError(
                    f"BUY {fill.quantity} {fill.symbol} on {fill.fill_date} requires Rs.{cash_out:.2f} "
                    f"but only Rs.{self._cash:.2f} cash is available."
                )
            self._cash -= cash_out
            self._positions[fill.symbol] = self.position(fill.symbol) + fill.quantity
            self._cost_tax_engine.record_buy(fill.symbol, fill.fill_date, fill.quantity, fill.fill_price, fill.cost)
            logger.debug("Applied BUY fill: %s, cash now Rs.%.2f", fill.symbol, self._cash)
            return Trade(fill=fill, realized_gains=())

        held = self.position(fill.symbol)
        if fill.quantity > held + 1e-6:
            raise OverSellError(
                f"SELL {fill.quantity} {fill.symbol} on {fill.fill_date} exceeds held quantity {held}."
            )
        realized = self._cost_tax_engine.match_sell(fill.symbol, fill.fill_date, fill.quantity, fill.fill_price)
        cash_in = fill.cost.gross_amount - fill.cost.total_cost
        self._cash += cash_in
        self._positions[fill.symbol] = held - fill.quantity
        logger.debug("Applied SELL fill: %s, cash now Rs.%.2f", fill.symbol, self._cash)
        return Trade(fill=fill, realized_gains=tuple(realized))

    def apply_dividend(self, symbol: str, ex_date: date, amount_per_unit: float) -> "DividendReceipt | None":
        """Credit cash for a dividend, based on quantity HELD as of this
        ex_date. Returns None (no-op) if no position is held — a dividend
        announcement for a symbol you don't hold has no cash effect."""
        from etf_platform.backtesting.models import DividendReceipt  # local import avoids a cycle with models.py

        held = self.position(symbol)
        if held <= 1e-9:
            return None
        total_credit = held * amount_per_unit
        self._cash += total_credit
        logger.info(
            "Dividend credited: %s ex-date %s, %.4f units @ Rs.%.4f = Rs.%.2f",
            symbol, ex_date, held, amount_per_unit, total_credit,
        )
        return DividendReceipt(
            symbol=symbol, ex_date=ex_date, quantity_held=held,
            amount_per_unit=amount_per_unit, total_cash_credited=total_credit,
        )

    def apply_split(self, symbol: str, ex_date: date, ratio: float) -> "CorporateActionEvent | None":
        """Adjust held quantity by a split/bonus ratio and propagate the
        same adjustment to the FIFO tax lots (see CostTaxEngine.apply_split
        for why acquisition dates must be preserved). Returns None if no
        position is held."""
        from etf_platform.backtesting.models import CorporateActionEvent  # local import, see apply_dividend

        held = self.position(symbol)
        if held <= 1e-9:
            return None
        new_quantity = held * ratio
        self._positions[symbol] = new_quantity
        self._cost_tax_engine.apply_split(symbol, ratio)
        logger.info("Split/bonus applied: %s ex-date %s, ratio %.4f, %.4f -> %.4f units", symbol, ex_date, ratio, held, new_quantity)
        return CorporateActionEvent(
            symbol=symbol, ex_date=ex_date, action_type="split_or_bonus", ratio=ratio,
            quantity_before=held, quantity_after=new_quantity,
        )

    def equity_curve_point(self, as_of_date: date, latest_prices: dict[str, float]) -> EquityCurvePoint:
        """`latest_prices` must be the close price known as of `as_of_date`
        for every held symbol — the caller (engine.py) is responsible for
        never passing a price dated after `as_of_date`, same no-look-ahead
        discipline as everywhere else in this engine."""
        positions_value = 0.0
        for symbol, qty in self._positions.items():
            if qty <= 1e-9:
                continue
            price = latest_prices.get(symbol)
            if price is None:
                logger.warning(
                    "No price available for held symbol '%s' on %s — valuing this position at 0 for "
                    "this equity curve point. This will distort the equity curve if it persists; check "
                    "for a data gap.",
                    symbol, as_of_date,
                )
                price = 0.0
            positions_value += qty * price
        return EquityCurvePoint(as_of_date=as_of_date, cash=self._cash, positions_value=positions_value)
