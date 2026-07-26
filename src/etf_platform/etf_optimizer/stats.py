"""Statistical validation for replacement recommendations (Phase 3).

Per your explicit requirement: "Do not recommend replacing an ETF unless
there is statistically validated evidence." This module is that gate.

Method: **block bootstrap** on the paired daily return difference
(candidate - incumbent), over their full overlapping history.

Why block bootstrap and not a simple t-test on the mean difference:
financial daily returns are autocorrelated (volatility clustering, at
minimum) — a plain t-test assumes i.i.d. observations and understates the
true uncertainty of the mean difference when that assumption is violated,
which would make it too easy to claim significance. Block bootstrap
resamples contiguous blocks of the paired-difference series (not individual
days), which preserves short-range autocorrelation structure in the
resampled data and gives a more honest confidence interval.

Why paired (candidate - incumbent) rather than two independent
distributions: the two ETFs share market conditions on any given day — an
independent-samples test would treat "both ETFs had a bad day because the
whole market fell" as if it were informative about their *relative*
difference, when it isn't. Differencing first removes common market
movement and isolates the actual relative signal.

Significance rule: the confidence interval for the mean difference must
exclude zero. This is a standard, conservative bar — a CI that straddles
zero means "we cannot rule out that there's no real difference," which is
exactly the case where no recommendation should be made.
"""

from __future__ import annotations

import numpy as np

from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine.models import OHLCVBar
from etf_platform.etf_optimizer import price_metrics
from etf_platform.etf_optimizer.exceptions import InsufficientDataError
from etf_platform.etf_optimizer.models import BootstrapTestResult

logger = get_logger("etf_optimizer.stats")

TRADING_DAYS_PER_YEAR = 252
MIN_OVERLAPPING_OBSERVATIONS = 60  # ~3 months of trading days; below this, "not enough
                                    # data to know" is the honest answer, not "no effect"


def block_bootstrap_mean_diff(
    candidate_returns: np.ndarray,
    incumbent_returns: np.ndarray,
    *,
    n_bootstrap: int = 2000,
    block_size: int = 20,
    confidence_level: float = 0.95,
    rng: np.random.Generator | None = None,
) -> tuple[float, float, float]:
    """Block-bootstrap confidence interval for the mean of
    (candidate_returns - incumbent_returns), annualized.

    Returns (observed_annualized_diff, ci_low, ci_high).

    `block_size=20` (~1 trading month) is a documented default balancing two
    failure modes: too small a block under-corrects for autocorrelation
    (converges toward the naive i.i.d. bootstrap this method exists to
    avoid); too large a block leaves too few independent blocks to resample
    meaningfully given typical history lengths here (a few years of daily
    data). 20 trading days is a conventional choice in the block-bootstrap
    literature for daily financial return series and is treated as a fixed
    methodology choice, not a free parameter tuned per comparison — tuning
    it per comparison would itself be a form of overfitting the validation
    method to get a desired answer.
    """
    if len(candidate_returns) != len(incumbent_returns):
        raise ValueError("candidate_returns and incumbent_returns must be the same length (paired).")
    n = len(candidate_returns)
    if n < MIN_OVERLAPPING_OBSERVATIONS:
        raise InsufficientDataError(
            f"Only {n} overlapping return observations; need >= {MIN_OVERLAPPING_OBSERVATIONS} "
            "for a block bootstrap test to be meaningful."
        )

    rng = rng or np.random.default_rng()
    diff = candidate_returns - incumbent_returns
    observed_mean_diff = float(np.mean(diff)) * TRADING_DAYS_PER_YEAR

    n_blocks_needed = int(np.ceil(n / block_size))
    max_start = n - block_size
    if max_start < 0:
        # Series shorter than one block — fall back to the whole series as
        # a single block (still valid, just less "block-y").
        block_size = n
        max_start = 0
        n_blocks_needed = 1

    bootstrap_means = np.empty(n_bootstrap)
    for i in range(n_bootstrap):
        starts = rng.integers(0, max_start + 1, size=n_blocks_needed)
        resampled = np.concatenate([diff[s : s + block_size] for s in starts])[:n]
        bootstrap_means[i] = np.mean(resampled) * TRADING_DAYS_PER_YEAR

    alpha = 1.0 - confidence_level
    ci_low = float(np.percentile(bootstrap_means, 100 * (alpha / 2)))
    ci_high = float(np.percentile(bootstrap_means, 100 * (1 - alpha / 2)))
    return observed_mean_diff, ci_low, ci_high


def validate_replacement(
    candidate_symbol: str,
    incumbent_symbol: str,
    candidate_bars: list[OHLCVBar],
    incumbent_bars: list[OHLCVBar],
    *,
    confidence_level: float = 0.95,
    n_bootstrap: int = 2000,
    rng: np.random.Generator | None = None,
) -> BootstrapTestResult:
    """Full statistical validation for a candidate-replaces-incumbent
    comparison: block bootstrap on annualized mean return difference, plus
    a max-drawdown comparison reported alongside (per PHASE1 §12.2 —
    drawdown trade-offs must always be visible, never hidden behind a return
    number, even in an automated evidence report)."""
    candidate_returns, incumbent_returns = price_metrics.aligned_returns(candidate_bars, incumbent_bars)
    n_obs = len(candidate_returns)

    if n_obs < MIN_OVERLAPPING_OBSERVATIONS:
        raise InsufficientDataError(
            f"Cannot validate {candidate_symbol} as a replacement for {incumbent_symbol}: only "
            f"{n_obs} overlapping trading days, need >= {MIN_OVERLAPPING_OBSERVATIONS}."
        )

    observed_diff, ci_low, ci_high = block_bootstrap_mean_diff(
        candidate_returns, incumbent_returns,
        n_bootstrap=n_bootstrap, confidence_level=confidence_level, rng=rng,
    )
    is_significant = ci_low > 0 or ci_high < 0
    favors_candidate = is_significant and observed_diff > 0

    candidate_dd = price_metrics.max_drawdown(candidate_bars)
    incumbent_dd = price_metrics.max_drawdown(incumbent_bars)
    drawdown_worse = (
        candidate_dd is not None and incumbent_dd is not None and candidate_dd > incumbent_dd
    )

    result = BootstrapTestResult(
        candidate_symbol=candidate_symbol,
        incumbent_symbol=incumbent_symbol,
        n_observations=n_obs,
        metric_name="annualized_mean_return_diff",
        observed_diff=observed_diff,
        confidence_level=confidence_level,
        ci_low=ci_low,
        ci_high=ci_high,
        n_bootstrap=n_bootstrap,
        is_significant=is_significant,
        favors_candidate=favors_candidate,
        candidate_max_drawdown=candidate_dd if candidate_dd is not None else float("nan"),
        incumbent_max_drawdown=incumbent_dd if incumbent_dd is not None else float("nan"),
        drawdown_worse=drawdown_worse,
    )
    logger.info(
        "Validated %s vs %s: n=%d, diff=%.4f, CI=[%.4f, %.4f], significant=%s, favors_candidate=%s, "
        "drawdown_worse=%s",
        candidate_symbol, incumbent_symbol, n_obs, observed_diff, ci_low, ci_high,
        is_significant, favors_candidate, drawdown_worse,
    )
    return result
