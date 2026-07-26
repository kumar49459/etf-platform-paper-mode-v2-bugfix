"""Price-derived metric calculations, shared by screening_engine.py,
scoring.py, and stats.py so the definitions (e.g. "what counts as a daily
return") are computed exactly once and can't drift between modules.

Uses numpy — this package is research-side per Phase 1 §12.1 (see
etf_optimizer/__init__.py), so numpy/pandas are an acceptable dependency
here, unlike in the live-instance-facing Phase 2 modules.
"""

from __future__ import annotations

import numpy as np

from etf_platform.data_engine.models import OHLCVBar

TRADING_DAYS_PER_YEAR = 252


def daily_returns(bars: list[OHLCVBar]) -> np.ndarray:
    """Simple daily returns from close-to-close, in chronological order.
    Returns an empty array if fewer than 2 bars are given (no return is
    computable from a single price point)."""
    if len(bars) < 2:
        return np.array([])
    sorted_bars = sorted(bars, key=lambda b: b.trade_date)
    closes = np.array([b.close for b in sorted_bars], dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        returns = (closes[1:] - closes[:-1]) / closes[:-1]
    return returns[np.isfinite(returns)]


def annualized_volatility(bars: list[OHLCVBar]) -> float | None:
    returns = daily_returns(bars)
    if len(returns) < 2:
        return None
    return float(np.std(returns, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def average_daily_turnover_inr(bars: list[OHLCVBar]) -> float | None:
    """Average daily traded value (price * volume) — the standard liquidity
    proxy: how much capital can move through this ETF in a day without
    materially moving the price."""
    if not bars:
        return None
    turnovers = [b.close * b.volume for b in bars]
    return float(np.mean(turnovers))


def average_daily_volume(bars: list[OHLCVBar]) -> float | None:
    """Average daily traded quantity (units) — distinct from turnover: two
    ETFs can have identical turnover with very different unit prices and
    volumes. Kept as a separate metric per Phase 3's 8-dimension scope
    rather than folded into liquidity."""
    if not bars:
        return None
    return float(np.mean([b.volume for b in bars]))


def max_drawdown(bars: list[OHLCVBar]) -> float | None:
    """Maximum peak-to-trough decline over the given price history, as a
    positive fraction (0.15 = 15% drawdown)."""
    if len(bars) < 2:
        return None
    sorted_bars = sorted(bars, key=lambda b: b.trade_date)
    closes = np.array([b.close for b in sorted_bars], dtype=float)
    running_max = np.maximum.accumulate(closes)
    drawdowns = (running_max - closes) / running_max
    return float(np.max(drawdowns))


def return_correlation(bars_a: list[OHLCVBar], bars_b: list[OHLCVBar]) -> float | None:
    """Pearson correlation of daily returns between two symbols, aligned on
    overlapping trade dates only. Returns None if fewer than 2 overlapping
    return observations exist."""
    dates_a = {b.trade_date: b.close for b in bars_a}
    dates_b = {b.trade_date: b.close for b in bars_b}
    common_dates = sorted(set(dates_a) & set(dates_b))
    if len(common_dates) < 3:  # need >=3 prices for >=2 aligned returns
        return None

    closes_a = np.array([dates_a[d] for d in common_dates], dtype=float)
    closes_b = np.array([dates_b[d] for d in common_dates], dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        returns_a = (closes_a[1:] - closes_a[:-1]) / closes_a[:-1]
        returns_b = (closes_b[1:] - closes_b[:-1]) / closes_b[:-1]

    mask = np.isfinite(returns_a) & np.isfinite(returns_b)
    if mask.sum() < 2:
        return None
    returns_a, returns_b = returns_a[mask], returns_b[mask]
    if np.std(returns_a) == 0 or np.std(returns_b) == 0:
        return None  # a constant series has undefined correlation, not zero
    corr = np.corrcoef(returns_a, returns_b)[0, 1]
    return float(corr) if np.isfinite(corr) else None


def aligned_returns(bars_a: list[OHLCVBar], bars_b: list[OHLCVBar]) -> tuple[np.ndarray, np.ndarray]:
    """Daily returns for two symbols, aligned on overlapping trade dates.
    Used by stats.py's paired bootstrap, which needs the two return series
    date-matched, not just independently computed.

    Requires only 2 common price dates (yielding 1 return pair) — unlike
    return_correlation's stricter 3-date minimum, this is a low-level
    alignment utility; callers (e.g. stats.py's MIN_OVERLAPPING_OBSERVATIONS
    gate) impose their own, more meaningful minimum sample size for whatever
    statistical test they're about to run.
    """
    dates_a = {b.trade_date: b.close for b in bars_a}
    dates_b = {b.trade_date: b.close for b in bars_b}
    common_dates = sorted(set(dates_a) & set(dates_b))
    if len(common_dates) < 2:
        return np.array([]), np.array([])

    closes_a = np.array([dates_a[d] for d in common_dates], dtype=float)
    closes_b = np.array([dates_b[d] for d in common_dates], dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        returns_a = (closes_a[1:] - closes_a[:-1]) / closes_a[:-1]
        returns_b = (closes_b[1:] - closes_b[:-1]) / closes_b[:-1]
    mask = np.isfinite(returns_a) & np.isfinite(returns_b)
    return returns_a[mask], returns_b[mask]
