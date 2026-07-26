"""Monte Carlo Simulation Engine (Phase 1 Module 19).

Block-bootstrap resampling of a backtest's daily return series to answer:
"how much could the outcome have varied due to the specific sequence of
returns realized, even if the underlying strategy/market process were
exactly the same?" This is a different question from Walk-Forward
Validation (does the strategy hold up across different historical
periods) - Monte Carlo asks "does it hold up across alternate plausible
orderings/realizations of similar returns," per Phase 1 section 1.3's original
distinction between these two modules.

Uses the same block-bootstrap methodology as Phase 3's stats.py (block
resampling to preserve short-range autocorrelation, not naive i.i.d.
resampling which would understate real risk) - one validated statistical
technique, reused consistently rather than inventing a second one.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from etf_platform.common.logging_setup import get_logger

logger = get_logger("validation.monte_carlo")

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class MonteCarloResult:
    n_simulations: int
    block_size: int
    starting_value: float
    final_value_percentiles: dict[int, float]     # e.g. {5: ..., 50: ..., 95: ...}
    max_drawdown_percentiles: dict[int, float]
    total_return_percentiles: dict[int, float]
    probability_of_loss: float                     # fraction of simulations ending below starting_value


class MonteCarloSimulator:
    def __init__(self, n_simulations: int = 1000, block_size: int = 20, rng: np.random.Generator | None = None) -> None:
        if n_simulations < 100:
            raise ValueError(
                f"n_simulations={n_simulations} is too low for stable percentile estimates; use >= 100."
            )
        if block_size < 1:
            raise ValueError(f"block_size must be >= 1, got {block_size}.")
        self._n_simulations = n_simulations
        self._block_size = block_size
        self._rng = rng or np.random.default_rng()

    def simulate(self, daily_returns: np.ndarray, starting_value: float) -> MonteCarloResult:
        n = len(daily_returns)
        if n < self._block_size:
            raise ValueError(
                f"Only {n} daily returns provided, fewer than block_size={self._block_size}. "
                "Need a longer backtest history to run a meaningful Monte Carlo simulation."
            )

        final_values = np.empty(self._n_simulations)
        max_drawdowns = np.empty(self._n_simulations)
        total_returns = np.empty(self._n_simulations)

        n_blocks_needed = int(np.ceil(n / self._block_size))
        max_start = n - self._block_size

        for i in range(self._n_simulations):
            starts = self._rng.integers(0, max_start + 1, size=n_blocks_needed)
            resampled_returns = np.concatenate([daily_returns[s : s + self._block_size] for s in starts])[:n]

            path = starting_value * np.cumprod(1 + resampled_returns)
            path_with_start = np.concatenate([[starting_value], path])

            final_values[i] = path_with_start[-1]
            running_max = np.maximum.accumulate(path_with_start)
            drawdowns = (running_max - path_with_start) / running_max
            max_drawdowns[i] = np.max(drawdowns)
            total_returns[i] = (path_with_start[-1] / starting_value) - 1.0

        percentiles = [5, 10, 25, 50, 75, 90, 95]
        result = MonteCarloResult(
            n_simulations=self._n_simulations,
            block_size=self._block_size,
            starting_value=starting_value,
            final_value_percentiles={p: float(np.percentile(final_values, p)) for p in percentiles},
            max_drawdown_percentiles={p: float(np.percentile(max_drawdowns, p)) for p in percentiles},
            total_return_percentiles={p: float(np.percentile(total_returns, p)) for p in percentiles},
            probability_of_loss=float(np.mean(final_values < starting_value)),
        )
        logger.info(
            "Monte Carlo simulation complete: %d runs, median final value=%.2f, P(loss)=%.1f%%",
            self._n_simulations, result.final_value_percentiles[50], result.probability_of_loss * 100,
        )
        return result
