"""Extended metrics (Milestone 5A, requirement 5) -- the pieces
genuinely new relative to Phase 4's frozen performance_analytics
(XIRR/CAGR/Sharpe/Sortino/Calmar/max_drawdown/1yr rolling already exist
and are reused unmodified, not reimplemented here).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AnnualReturn:
    year: int
    start_value: float
    end_value: float
    return_pct: float


@dataclass(frozen=True)
class MonthlyReturn:
    year: int
    month: int
    start_value: float
    end_value: float
    return_pct: float


@dataclass(frozen=True)
class DrawdownEpisode:
    peak_date: object
    trough_date: object
    recovery_date: object
    drawdown_pct: float
    days_to_trough: int
    days_to_recover: object


def annual_returns(equity_curve):
    if not equity_curve:
        return []
    by_year = {}
    for dt, value in equity_curve:
        by_year.setdefault(dt.year, []).append((dt, value))
    results = []
    for year in sorted(by_year):
        points = sorted(by_year[year])
        start_value, end_value = points[0][1], points[-1][1]
        if start_value <= 0:
            continue
        results.append(AnnualReturn(year, start_value, end_value, (end_value / start_value - 1) * 100))
    return results


def monthly_returns(equity_curve):
    if not equity_curve:
        return []
    by_month = {}
    for dt, value in equity_curve:
        by_month.setdefault((dt.year, dt.month), []).append((dt, value))
    results = []
    for (year, month) in sorted(by_month):
        points = sorted(by_month[(year, month)])
        start_value, end_value = points[0][1], points[-1][1]
        if start_value <= 0:
            continue
        results.append(MonthlyReturn(year, month, start_value, end_value, (end_value / start_value - 1) * 100))
    return results


def best_worst_calendar_year(equity_curve):
    returns = annual_returns(equity_curve)
    if not returns:
        return None, None
    return max(returns, key=lambda r: r.return_pct), min(returns, key=lambda r: r.return_pct)


def standalone_volatility_pct(daily_returns):
    import numpy as np

    arr = np.asarray(list(daily_returns), dtype=float)
    if arr.size < 2:
        return None
    return float(np.std(arr, ddof=1) * np.sqrt(252) * 100)


def drawdown_episodes(equity_curve, min_drawdown_pct=5.0):
    if len(equity_curve) < 2:
        return []
    episodes = []
    peak_date, peak_value = equity_curve[0]
    in_drawdown = False
    trough_date, trough_value = None, None

    for dt, value in equity_curve[1:]:
        if value >= peak_value:
            if in_drawdown:
                drawdown_pct = (peak_value - trough_value) / peak_value * 100
                if drawdown_pct >= min_drawdown_pct:
                    episodes.append(DrawdownEpisode(
                        peak_date=peak_date, trough_date=trough_date, recovery_date=dt,
                        drawdown_pct=drawdown_pct, days_to_trough=(trough_date - peak_date).days,
                        days_to_recover=(dt - trough_date).days,
                    ))
                in_drawdown = False
            peak_date, peak_value = dt, value
        else:
            if not in_drawdown or value < trough_value:
                trough_date, trough_value = dt, value
            in_drawdown = True

    if in_drawdown:
        drawdown_pct = (peak_value - trough_value) / peak_value * 100
        if drawdown_pct >= min_drawdown_pct:
            episodes.append(DrawdownEpisode(
                peak_date=peak_date, trough_date=trough_date, recovery_date=None,
                drawdown_pct=drawdown_pct, days_to_trough=(trough_date - peak_date).days,
                days_to_recover=None,
            ))
    return episodes


def portfolio_turnover_pct(trades, average_portfolio_value, start_date, end_date):
    if average_portfolio_value <= 0:
        return None
    bought = sum(t.fill.quantity * t.fill.fill_price for t in trades if t.fill.side.value == "buy")
    sold = sum(t.fill.quantity * t.fill.fill_price for t in trades if t.fill.side.value == "sell")
    years = max((end_date - start_date).days / 365.25, 1 / 365.25)
    return (bought + sold) / average_portfolio_value / years * 100


def cash_utilization_pct(equity_curve_with_cash):
    if not equity_curve_with_cash:
        return None
    ratios = [1 - (cash / total) for _, total, cash in equity_curve_with_cash if total > 0]
    if not ratios:
        return None
    return sum(ratios) / len(ratios) * 100
