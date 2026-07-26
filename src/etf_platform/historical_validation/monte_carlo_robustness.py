"""Monte Carlo ROBUSTNESS testing (Milestone 5A, requirement 6) -- a
genuinely different question from Phase 4's frozen MonteCarloSimulator
(validation/monte_carlo.py), not a duplicate of it.

The frozen simulator block-bootstraps the RETURN SEQUENCE of one already-
completed backtest: "how much could outcomes have varied if this same
underlying process had realized its returns in a different order?"

THIS module asks a different question: "how sensitive is the strategy to
realistic variation in the ASSUMPTIONS FEEDING the backtest itself" -- SIP
date, rebalance timing, small price noise, transaction cost rates,
slippage -- by actually RE-RUNNING the real, frozen BacktestEngine many
times with each run's inputs perturbed, not by resampling one run's
output. Both questions are valuable and complementary; this module answers
the one nothing existing already answers.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from etf_platform.backtesting.engine import BacktestEngine
from etf_platform.backtesting.models import BacktestConfig
from etf_platform.cost_tax_engine import CostTaxEngine, IndiaEquityCostConfig


@dataclass(frozen=True)
class RobustnessPerturbation:
    sip_day_of_month: int
    price_noise_factor: float
    slippage_bps: float
    stt_pct_buy: float
    brokerage_flat_per_order: float


@dataclass(frozen=True)
class RobustnessOutcome:
    perturbation: RobustnessPerturbation
    final_value: float
    cagr_pct: float
    max_drawdown_pct: float


@dataclass
class RobustnessReport:
    n_simulations: int
    seed: int
    outcomes: list = field(default_factory=list)

    def percentile(self, attribute, pct):
        import numpy as np

        values = sorted(getattr(o, attribute) for o in self.outcomes)
        if not values:
            return None
        return float(np.percentile(values, pct))

    def probability_of_loss(self):
        if not self.outcomes:
            return None
        losing = sum(1 for o in self.outcomes if o.cagr_pct < 0)
        return losing / len(self.outcomes)


def run_robustness_simulation(
    strategy_factory, price_history, symbols, start_date, end_date, initial_capital,
    n_simulations, seed,
    sip_day_range=(1, 10), price_noise_range=(-0.005, 0.005), slippage_bps_range=(2.0, 15.0),
    stt_pct_range=(0.0009, 0.0011), brokerage_range=(0.0, 20.0),
):
    rng = random.Random(seed)
    report = RobustnessReport(n_simulations=n_simulations, seed=seed)

    for _ in range(n_simulations):
        perturbation = RobustnessPerturbation(
            sip_day_of_month=rng.randint(*sip_day_range),
            price_noise_factor=1 + rng.uniform(*price_noise_range),
            slippage_bps=rng.uniform(*slippage_bps_range),
            stt_pct_buy=rng.uniform(*stt_pct_range),
            brokerage_flat_per_order=rng.uniform(*brokerage_range),
        )

        perturbed_history = _apply_price_noise(price_history, perturbation.price_noise_factor)
        strategy = strategy_factory(perturbation.sip_day_of_month)
        cost_config = IndiaEquityCostConfig(
            stt_pct_buy=perturbation.stt_pct_buy, stt_pct_sell=perturbation.stt_pct_buy,
            brokerage_flat_per_order=perturbation.brokerage_flat_per_order,
            slippage_bps=perturbation.slippage_bps,
        )
        config = BacktestConfig(
            start_date=start_date, end_date=end_date, initial_capital=initial_capital, symbols=tuple(symbols),
        )
        engine = BacktestEngine(config, strategy, cost_tax_engine=CostTaxEngine(cost_config))
        result = engine.run(perturbed_history)

        final_value = result.equity_curve[-1].total_value if result.equity_curve else initial_capital
        years = max((end_date - start_date).days / 365.25, 1 / 365.25)
        cagr_pct = ((final_value / initial_capital) ** (1 / years) - 1) * 100 if final_value > 0 else -100.0
        from etf_platform.performance_analytics.metrics import max_drawdown_from_equity_curve

        equity_values = [p.total_value for p in result.equity_curve]
        max_dd = max_drawdown_from_equity_curve(equity_values) or 0.0

        report.outcomes.append(RobustnessOutcome(
            perturbation=perturbation, final_value=final_value, cagr_pct=cagr_pct, max_drawdown_pct=max_dd * 100,
        ))

    return report


def _apply_price_noise(price_history, noise_factor):
    import dataclasses

    noisy = {}
    for symbol, bars in price_history.items():
        noisy[symbol] = [
            dataclasses.replace(
                bar, close=bar.close * noise_factor, open=bar.open * noise_factor,
                high=bar.high * noise_factor, low=bar.low * noise_factor,
            )
            for bar in bars
        ]
    return noisy
