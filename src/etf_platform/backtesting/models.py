"""Core data models for the Backtesting Engine.

Reuses `Side`, `CostBreakdown`, `RealizedGain` from cost_tax_engine rather
than duplicating them — an order's side is the same concept whether you're
computing its cost or executing it, and having two separate BUY/SELL enums
in two packages would be exactly the kind of quiet duplication this
platform has consistently avoided (see the correlation-vs-diversification
and Decision-Explanation-vs-Attribution boundary decisions in earlier
phases).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum

from etf_platform.backtesting.exceptions import InvalidOrderError
from etf_platform.cost_tax_engine import CostBreakdown, RealizedGain, Side


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


@dataclass(frozen=True)
class OrderIntent:
    """What a Strategy emits. `rationale` is mandatory and validated
    non-empty at construction time — this is the structural enforcement of
    Phase 4 objective #11 ("every trade must include a human-readable
    explanation of why it occurred"). A Strategy cannot produce an order
    the engine will accept without one; this isn't a convention, it's
    checked in `__post_init__` and raises immediately if violated.
    """

    symbol: str
    side: Side
    order_type: OrderType
    quantity: float
    rationale: str
    limit_price: float | None = None

    def __post_init__(self) -> None:
        if not self.rationale or not self.rationale.strip():
            raise InvalidOrderError(
                f"OrderIntent for {self.symbol} ({self.side.value}) is missing a rationale. "
                "Every order must explain why it was generated — see Phase 4 objective #11."
            )
        if self.quantity <= 0:
            raise InvalidOrderError(f"OrderIntent quantity must be > 0, got {self.quantity} for {self.symbol}.")
        # ETFs trade in whole units on NSE — there is no fractional-share
        # market for them (unlike some US brokers). A quantity of 3.7 units
        # is not a real order any broker would accept; catching this at
        # construction time (adversarial-review finding) is far better than
        # discovering it as a confusing downstream P&L discrepancy.
        if abs(self.quantity - round(self.quantity)) > 1e-6:
            raise InvalidOrderError(
                f"OrderIntent quantity {self.quantity} for {self.symbol} is not a whole number. "
                "ETFs trade in whole units on NSE — fractional quantities are not tradable."
            )
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise InvalidOrderError(f"LIMIT order for {self.symbol} requires a limit_price.")


@dataclass
class PendingOrder:
    """An accepted OrderIntent, queued for execution on a future bar — never
    the same bar it was decided on. See engine.py's module docstring for
    the exact no-look-ahead sequencing this enforces."""

    intent: OrderIntent
    decided_date: date
    target_fill_date: date
    expiry_date: date


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: Side
    order_type: OrderType
    quantity: float
    fill_price: float
    fill_date: date
    decided_date: date
    cost: CostBreakdown
    rationale: str


@dataclass(frozen=True)
class Trade:
    """One executed fill, recorded in the trade log with a full
    human-readable explanation (Phase 4 objective #11) — combining the
    strategy's stated rationale with the concrete cost/tax outcome, so the
    explanation is grounded in what actually happened, not just intent."""

    fill: Fill
    realized_gains: tuple[RealizedGain, ...] = ()

    @property
    def explanation(self) -> str:
        base = (
            f"{self.fill.side.value.upper()} {self.fill.quantity:.4f} units of {self.fill.symbol} "
            f"on {self.fill.fill_date} at Rs.{self.fill.fill_price:.2f} ({self.fill.order_type.value} order, "
            f"decided on {self.fill.decided_date}). Reason: {self.fill.rationale}. "
            f"Total transaction cost: Rs.{self.fill.cost.total_cost:.2f}."
        )
        if self.realized_gains:
            gain_parts = [
                f"Rs.{rg.gross_gain:.2f} {rg.gain_type.value} gain on the lot bought {rg.buy_date} "
                f"(held {rg.holding_period_days} days, est. tax Rs.{rg.estimated_tax:.2f})"
                for rg in self.realized_gains
            ]
            base += " Realized: " + "; ".join(gain_parts) + "."
        return base


@dataclass(frozen=True)
class EquityCurvePoint:
    as_of_date: date
    cash: float
    positions_value: float

    @property
    def total_value(self) -> float:
        return self.cash + self.positions_value


@dataclass(frozen=True)
class PortfolioSnapshot:
    """Read-only view of portfolio state passed to a Strategy — deliberately
    does not expose the internal Portfolio object, so a Strategy cannot
    mutate state outside the engine's controlled fill/cost pipeline."""

    as_of_date: date
    cash: float
    positions: dict[str, float]
    total_value: float


@dataclass(frozen=True)
class BacktestConfig:
    start_date: date
    end_date: date
    initial_capital: float
    symbols: tuple[str, ...]
    benchmark_symbol: str | None = None
    limit_order_expiry_days: int = 5
    lookback_days_provided_to_strategy: int = 365
    snapshot_id: str | None = None
    max_volume_participation_pct: float | None = None
    stale_price_warning_days: int = 5

    def __post_init__(self) -> None:
        if self.start_date >= self.end_date:
            raise InvalidOrderError(f"start_date {self.start_date} must be before end_date {self.end_date}.")
        if self.initial_capital <= 0:
            raise InvalidOrderError(f"initial_capital must be > 0, got {self.initial_capital}.")
        if not self.symbols:
            raise InvalidOrderError("BacktestConfig.symbols must not be empty.")


@dataclass
class ReproducibilityRecord:
    """Everything needed to answer "exactly what produced this backtest
    result" — Phase 4 objective #9 and Phase 1 §1.4's reproducibility NFR.
    """

    run_id: str
    code_commit_hash: str
    code_is_dirty: bool
    config_version: str
    data_snapshot_id: str
    started_at: str
    finished_at: str | None = None


@dataclass
class BacktestResult:
    config: BacktestConfig
    equity_curve: list[EquityCurvePoint] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    rejected_orders: list[tuple[OrderIntent, str]] = field(default_factory=list)
    reproducibility: ReproducibilityRecord | None = None
    performance_report: object | None = None
    warnings: list[str] = field(default_factory=list)
    actual_end_date: date | None = None
    dividend_receipts: list["DividendReceipt"] = field(default_factory=list)
    corporate_action_events: list["CorporateActionEvent"] = field(default_factory=list)


@dataclass(frozen=True)
class DividendReceipt:
    """A dividend cash credit applied to the portfolio. Recorded separately
    from Trade (it isn't an order/fill), but always explained the same way
    Phase 4 objective #11 requires for trades."""

    symbol: str
    ex_date: date
    quantity_held: float
    amount_per_unit: float
    total_cash_credited: float

    @property
    def explanation(self) -> str:
        return (
            f"Dividend received: {self.quantity_held:.4f} units of {self.symbol} held on ex-date "
            f"{self.ex_date} at Rs.{self.amount_per_unit:.4f}/unit = Rs.{self.total_cash_credited:.2f} "
            "credited to cash."
        )


@dataclass(frozen=True)
class CorporateActionEvent:
    """A split/bonus adjustment applied to a held position. Original
    acquisition dates on the underlying tax lots are preserved exactly —
    Indian tax law does not reset the capital-gains holding period for
    bonus/split-adjusted units (see cost_tax_engine.py's apply_split)."""

    symbol: str
    ex_date: date
    action_type: str
    ratio: float
    quantity_before: float
    quantity_after: float

    @property
    def explanation(self) -> str:
        return (
            f"{self.action_type.title()} applied to {self.symbol} on {self.ex_date}: "
            f"{self.quantity_before:.4f} units -> {self.quantity_after:.4f} units (ratio {self.ratio}). "
            "Original lot acquisition dates preserved for holding-period/tax purposes."
        )
