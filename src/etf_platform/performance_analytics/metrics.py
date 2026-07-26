"""Core performance metric calculations.

Uses numpy/scipy — this package is research-side (backtesting output),
consistent with the same placement rationale already established for
etf_optimizer in Phase 3 (Phase 1 §12.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
from scipy import optimize

TRADING_DAYS_PER_YEAR = 252


def xirr(cashflows: list[tuple[date, float]], guess: float = 0.1) -> float | None:
    """Extended Internal Rate of Return for irregular cash flows.

    `cashflows` is a list of (date, amount) — negative for money going out
    (a buy/investment), positive for money coming in (a sell/withdrawal or
    the final portfolio value treated as a terminal inflow). This is the
    standard definition institutional platforms use, matching what a
    spreadsheet's XIRR function computes.

    Returns None if no solution converges (e.g. all cash flows have the
    same sign, which has no valid IRR) rather than raising — a
    non-convergent XIRR is a legitimate, reportable outcome for a
    performance report, not an error condition that should halt reporting.
    """
    if len(cashflows) < 2:
        return None
    dates = [d for d, _ in cashflows]
    amounts = np.array([a for _, a in cashflows])
    if np.all(amounts >= 0) or np.all(amounts <= 0):
        return None  # no sign change -> no valid IRR

    t0 = dates[0]
    years = np.array([(d - t0).days / 365.0 for d in dates])

    def npv(rate: float) -> float:
        if rate <= -1.0:
            return np.inf  # avoid (1+rate)**years going complex/undefined
        return float(np.sum(amounts / (1.0 + rate) ** years))

    try:
        result = optimize.brentq(npv, -0.9999, 100.0, xtol=1e-8, maxiter=200)
        return float(result)
    except (ValueError, RuntimeError):
        # brentq requires a sign change in npv() across the bracket; if the
        # cash flow pattern doesn't produce one (or fails to converge),
        # report "could not compute" rather than crash the whole report.
        return None


def cagr(start_value: float, end_value: float, years: float) -> float | None:
    """Compound Annual Growth Rate. None if inputs are degenerate (zero/negative
    start value, or non-positive duration) — a CAGR isn't meaningfully
    defined in those cases."""
    if start_value <= 0 or years <= 0:
        return None
    return float((end_value / start_value) ** (1.0 / years) - 1.0)


def _annualized_return_series(daily_returns: np.ndarray) -> tuple[float, float]:
    mean = float(np.mean(daily_returns)) * TRADING_DAYS_PER_YEAR
    std = float(np.std(daily_returns, ddof=1)) * np.sqrt(TRADING_DAYS_PER_YEAR)
    return mean, std


def sharpe_ratio(daily_returns: np.ndarray, risk_free_rate: float = 0.0) -> float | None:
    """Annualized Sharpe ratio. `risk_free_rate` is annualized (e.g. 0.07
    for a 7% risk-free rate) — converted to a daily excess-return basis
    internally. None if fewer than 2 return observations or zero
    volatility (division by zero is undefined, not zero)."""
    if len(daily_returns) < 2:
        return None
    daily_rf = risk_free_rate / TRADING_DAYS_PER_YEAR
    excess = daily_returns - daily_rf
    mean_annual, std_annual = _annualized_return_series(excess)
    if std_annual < 1e-10:
        # Floating-point representation of "identical" decimal values (e.g.
        # 0.001 repeated) rarely produces an EXACT 0.0 variance due to
        # binary floating-point rounding, even though the true underlying
        # volatility is zero. A small epsilon threshold (not ==0) correctly
        # treats near-zero as zero without needing bit-exact equality.
        return None
    return mean_annual / std_annual


def sortino_ratio(daily_returns: np.ndarray, target_return: float = 0.0) -> float | None:
    """Annualized Sortino ratio — like Sharpe, but only penalizes downside
    deviation (returns below `target_return`), not upside volatility. This
    matters for this platform specifically because the stated objective
    (Phase 1 §0) is validated returns *with drawdown control*, not
    volatility-minimization in general — Sortino is the more relevant risk
    measure for that objective than Sharpe alone."""
    if len(daily_returns) < 2:
        return None
    daily_target = target_return / TRADING_DAYS_PER_YEAR
    excess = daily_returns - daily_target
    downside = excess[excess < 0]
    if len(downside) == 0:
        return None  # no downside observations -> undefined, not infinite
    downside_std = float(np.sqrt(np.mean(downside**2))) * np.sqrt(TRADING_DAYS_PER_YEAR)
    if downside_std < 1e-10:
        return None
    mean_annual = float(np.mean(excess)) * TRADING_DAYS_PER_YEAR
    return mean_annual / downside_std


def max_drawdown_from_equity_curve(equity_values: list[float]) -> float | None:
    """Maximum peak-to-trough decline over an equity curve, as a positive
    fraction. Same definition as etf_optimizer.price_metrics.max_drawdown,
    reimplemented here (not imported) to keep performance_analytics
    independent of etf_optimizer — they're used together in a backtest
    report but shouldn't have a hard package dependency on each other."""
    if len(equity_values) < 2:
        return None
    values = np.array(equity_values, dtype=float)
    running_max = np.maximum.accumulate(values)
    drawdowns = (running_max - values) / running_max
    return float(np.max(drawdowns))


def calmar_ratio(cagr_value: float | None, max_dd: float | None) -> float | None:
    """CAGR divided by max drawdown — return earned per unit of worst
    historical pain endured. None if either input is unavailable or max_dd
    is zero (undefined, not infinite reward-for-zero-risk)."""
    if cagr_value is None or max_dd is None or max_dd == 0:
        return None
    return cagr_value / max_dd


def rolling_returns(
    equity_curve: list[tuple[date, float]], window_days: int
) -> list[tuple[date, float]]:
    """Annualized rolling return ending on each date, computed over the
    trailing `window_days` calendar days. Returns (date, annualized_return)
    pairs only for dates where a full window of history exists — no
    partial-window results, since a partial-window "rolling 1yr return"
    computed over 3 months of data would be misleading."""
    if len(equity_curve) < 2:
        return []
    dates = [d for d, _ in equity_curve]
    values = [v for _, v in equity_curve]
    results: list[tuple[date, float]] = []

    start_idx = 0
    for i, current_date in enumerate(dates):
        window_start_date = current_date - timedelta(days=window_days)
        while start_idx < i and dates[start_idx] < window_start_date:
            start_idx += 1
        if dates[start_idx] > window_start_date or start_idx == i:
            continue  # not enough history yet for a full window
        years = (current_date - dates[start_idx]).days / 365.0
        if years <= 0 or values[start_idx] <= 0:
            continue
        ann_return = (values[i] / values[start_idx]) ** (1.0 / years) - 1.0
        results.append((current_date, float(ann_return)))

    return results


@dataclass(frozen=True)
class WinLossStats:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float | None
    average_win: float | None
    average_loss: float | None
    largest_win: float | None
    largest_loss: float | None
    profit_factor: float | None  # gross profit / gross loss; None if no losses (undefined, not infinite)


def win_loss_stats(realized_pnls: list[float]) -> WinLossStats:
    """Win/loss statistics from a list of realized P&L amounts (one per
    closed round-trip trade, net of costs — the caller is responsible for
    passing net figures, this function doesn't know about costs)."""
    if not realized_pnls:
        return WinLossStats(0, 0, 0, None, None, None, None, None, None)

    wins = [p for p in realized_pnls if p > 0]
    losses = [p for p in realized_pnls if p < 0]
    total = len(realized_pnls)

    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))

    return WinLossStats(
        total_trades=total,
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=len(wins) / total if total else None,
        average_win=float(np.mean(wins)) if wins else None,
        average_loss=float(np.mean(losses)) if losses else None,
        largest_win=max(wins) if wins else None,
        largest_loss=min(losses) if losses else None,
        profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else None,
    )
