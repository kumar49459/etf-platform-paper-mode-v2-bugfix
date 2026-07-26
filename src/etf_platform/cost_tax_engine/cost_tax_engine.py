"""Cost & Tax Engine (Phase 1 Module 18).

Computes realistic per-transaction costs and capital-gains tax for Indian
equity-delivery trades (ETFs are taxed identically to equity delivery
trades under Indian tax law — this is not a simplification, it's the actual
rule).

RATE SOURCES (verified via web search at build time, July 2026 — re-verify
before live use, since these are regulatory/exchange rates that change):
- STT (Securities Transaction Tax) on equity delivery: 0.1% on BOTH buy and
  sell sides. Confirmed unchanged by Budget 2026 (only F&O STT rates
  changed in that budget). Source: multiple tax-advisory sites cross-
  referenced, e.g. Zerodha's own support documentation.
- Stamp duty: 0.015% on the BUY side only, uniform nationwide since the
  Finance Act 2019 amendment (effective 1 July 2020) centralized stamp duty
  collection through exchanges/clearing corporations rather than
  state-by-state rates. Source: NSE's official investor-guidance page.
- GST: 18%, applied to (brokerage + exchange transaction charge + SEBI
  turnover fee) — NOT applied to STT or stamp duty, since those are
  government taxes, not a service fee. Source: cross-referenced across
  multiple current sources.
- LTCG (long-term capital gains, equity/ETF held > 365 days): 12.5% flat
  (post the 2024 rate change referenced in current sources).
- STCG (short-term capital gains, held <= 365 days): 20% flat.
- Exchange transaction charge and SEBI turnover fee: set to commonly-cited
  approximate figures (NSE cash segment ~0.00297% and SEBI's ~Rs.10/crore
  respectively) — flagged as APPROXIMATE, not individually re-confirmed
  against a live current circular in this build. These are minor line
  items relative to STT/stamp duty/GST, but should be checked against
  NSE's/SEBI's current fee schedule before relying on backtest cost
  totals for a real capital decision.
- Brokerage: defaults to 0 (many Indian discount brokers, including
  Zerodha, charge zero brokerage on equity delivery trades specifically —
  this is a real, current, well-known industry practice, not an
  optimistic assumption). Override via config if your broker charges
  differently.
- Slippage: NOT a regulatory rate — a modeling assumption. Default 5 bps,
  configurable, and should be tuned per-ETF based on typical bid-ask
  spread and your actual order size relative to average daily volume
  (Phase 3's `average_daily_turnover_inr` metric is a reasonable input for
  calibrating this per-symbol later; Phase 4 uses one flat default).

FIFO tax-lot tracking: Indian tax law does not mandate FIFO for equity
(specific-lot identification is technically permitted), but FIFO is the
conservative, auditable, universally-accepted default absent a specific
tax-planning reason to do otherwise — and matches how most brokers report
gains on your contract note / capital gains statement.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import date

from etf_platform.common.logging_setup import get_logger
from etf_platform.cost_tax_engine.exceptions import InsufficientLotsError
from etf_platform.cost_tax_engine.models import CostBreakdown, GainType, RealizedGain, Side, TaxLot

logger = get_logger("cost_tax_engine")

LONG_TERM_THRESHOLD_DAYS = 365


@dataclass(frozen=True)
class IndiaEquityCostConfig:
    """See module docstring for the source and confidence level of each rate."""

    brokerage_pct: float = 0.0
    brokerage_flat_per_order: float = 0.0
    stt_pct_buy: float = 0.001          # 0.1% CITED, current as of Budget 2026
    stt_pct_sell: float = 0.001         # 0.1% CITED, current as of Budget 2026
    stamp_duty_pct_buy: float = 0.00015  # 0.015%, buy side only, CITED
    exchange_txn_charge_pct: float = 0.0000297  # APPROXIMATE, verify against current NSE fee schedule
    sebi_turnover_fee_pct: float = 0.000001     # APPROXIMATE, verify against current SEBI fee schedule
    gst_pct: float = 0.18               # CITED, applies to brokerage + exchange charge + SEBI fee only
    slippage_bps: float = 5.0           # ASSUMPTION, not a regulatory rate, tune per use case
    stcg_tax_rate: float = 0.20         # CITED, equity/ETF held <= 365 days
    ltcg_tax_rate: float = 0.125        # CITED, equity/ETF held > 365 days
    long_term_threshold_days: int = LONG_TERM_THRESHOLD_DAYS


class CostTaxEngine:
    """Stateful per-symbol FIFO tax-lot tracker plus stateless cost
    computation. One instance should be used for the lifetime of a single
    backtest or a single live trading account — mixing tax-lot state across
    unrelated portfolios would corrupt the FIFO matching."""

    def __init__(self, config: IndiaEquityCostConfig | None = None) -> None:
        self._config = config or IndiaEquityCostConfig()
        self._lots: dict[str, deque[TaxLot]] = {}

    def compute_transaction_cost(self, side: Side, price: float, quantity: float) -> CostBreakdown:
        """Compute the full itemized cost of one transaction. Does not touch
        tax-lot state — call this for both buys and sells; call
        `record_buy`/`match_sell` separately to update FIFO lot tracking."""
        gross_amount = price * quantity
        cfg = self._config

        brokerage = cfg.brokerage_flat_per_order + gross_amount * cfg.brokerage_pct
        stt_rate = cfg.stt_pct_buy if side == Side.BUY else cfg.stt_pct_sell
        stt = gross_amount * stt_rate
        stamp_duty = gross_amount * cfg.stamp_duty_pct_buy if side == Side.BUY else 0.0
        exchange_txn_charge = gross_amount * cfg.exchange_txn_charge_pct
        sebi_turnover_fee = gross_amount * cfg.sebi_turnover_fee_pct
        gst = (brokerage + exchange_txn_charge + sebi_turnover_fee) * cfg.gst_pct
        slippage_cost = gross_amount * (cfg.slippage_bps / 10_000.0)

        return CostBreakdown(
            gross_amount=gross_amount,
            brokerage=brokerage,
            stt=stt,
            stamp_duty=stamp_duty,
            exchange_txn_charge=exchange_txn_charge,
            sebi_turnover_fee=sebi_turnover_fee,
            gst=gst,
            slippage_cost=slippage_cost,
        )

    def record_buy(self, symbol: str, buy_date: date, quantity: float, price: float, cost: CostBreakdown) -> None:
        self._lots.setdefault(symbol, deque()).append(
            TaxLot(symbol=symbol, buy_date=buy_date, quantity=quantity, buy_price=price, buy_cost_breakdown=cost)
        )

    def match_sell(self, symbol: str, sell_date: date, quantity: float, sell_price: float) -> list[RealizedGain]:
        """Match a sell against FIFO lots, splitting across multiple lots if
        the sell quantity spans more than one buy lot. Raises
        InsufficientLotsError if trying to sell more than is held — this
        should never happen if the Backtesting Engine's Portfolio correctly
        prevents over-selling, so hitting this is a real bug signal, not an
        expected edge case to silently paper over."""
        lots = self._lots.get(symbol, deque())
        remaining = quantity
        realized: list[RealizedGain] = []

        while remaining > 1e-9:
            if not lots:
                raise InsufficientLotsError(
                    f"Attempted to sell {quantity} units of {symbol} on {sell_date}, but no FIFO tax "
                    f"lots remain to match against (short {remaining:.4f} units). This indicates a "
                    "portfolio state bug upstream, not a normal tax-tracking condition."
                )
            lot = lots[0]
            matched_qty = min(lot.quantity, remaining)
            holding_days = (sell_date - lot.buy_date).days
            gain_type = (
                GainType.LONG_TERM if holding_days > self._config.long_term_threshold_days else GainType.SHORT_TERM
            )
            tax_rate = self._config.ltcg_tax_rate if gain_type == GainType.LONG_TERM else self._config.stcg_tax_rate
            gross_gain = (sell_price - lot.buy_price) * matched_qty
            estimated_tax = max(0.0, gross_gain) * tax_rate

            realized.append(
                RealizedGain(
                    symbol=symbol, sell_date=sell_date, quantity=matched_qty, buy_date=lot.buy_date,
                    buy_price=lot.buy_price, sell_price=sell_price, gain_type=gain_type,
                    gross_gain=gross_gain, tax_rate=tax_rate, estimated_tax=estimated_tax,
                    holding_period_days=holding_days,
                )
            )

            if matched_qty >= lot.quantity - 1e-9:
                lots.popleft()
            else:
                lots[0] = TaxLot(
                    symbol=lot.symbol, buy_date=lot.buy_date, quantity=lot.quantity - matched_qty,
                    buy_price=lot.buy_price, buy_cost_breakdown=lot.buy_cost_breakdown,
                )
            remaining -= matched_qty

        return realized

    def open_lots(self, symbol: str) -> list[TaxLot]:
        return list(self._lots.get(symbol, deque()))

    def total_open_quantity(self, symbol: str) -> float:
        return sum(lot.quantity for lot in self._lots.get(symbol, deque()))

    def apply_split(self, symbol: str, ratio: float) -> None:
        """Adjust every open FIFO lot for `symbol` by a split/bonus `ratio`
        (e.g. 2.0 for a 1-for-1 bonus doubling units; 0.5 for a 1-for-2
        reverse split). Quantity scales by `ratio`, buy_price scales by
        `1/ratio` (total cost basis of each lot is unchanged — a split
        doesn't create or destroy value). `buy_date` on every lot is left
        exactly as-is: under Indian tax law, bonus/split-adjusted units
        retain their ORIGINAL acquisition date for capital-gains holding-
        period purposes — a split must never reset the STCG/LTCG clock.
        This was a specific finding during the Phase 4 adversarial review;
        get it wrong and every post-split sale would be misclassified.
        """
        if ratio <= 0:
            raise ValueError(f"Split ratio must be > 0, got {ratio}.")
        lots = self._lots.get(symbol)
        if not lots:
            return
        adjusted = deque(
            TaxLot(
                symbol=lot.symbol,
                buy_date=lot.buy_date,  # UNCHANGED — see docstring
                quantity=lot.quantity * ratio,
                buy_price=lot.buy_price / ratio,
                buy_cost_breakdown=lot.buy_cost_breakdown,
            )
            for lot in lots
        )
        self._lots[symbol] = adjusted
        logger.info(
            "Applied split/bonus ratio %.4f to %s: %d lot(s) adjusted, acquisition dates preserved.",
            ratio, symbol, len(adjusted),
        )
