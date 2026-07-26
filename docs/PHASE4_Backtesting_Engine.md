# Phase 4 — Backtesting Engine: Design & Usage

**Modules delivered:** Backtesting Engine (Module 6), Cost & Tax Engine (Module 18), Performance Analytics (Module 8), Walk-Forward Validation Framework (Module 21), Monte Carlo Simulation Engine (Module 19).

All five are modules already approved in the frozen 26-module architecture — this phase implements them, it does not introduce anything new to the architecture. Per your instruction, I made no architecture changes.

## Why four "extra" modules got built in a phase called "Backtesting Engine"

Objectives #6, #7, #8, and #10 explicitly require integration with the Cost & Tax Engine, walk-forward validation, Monte Carlo hooks, and a full performance report — none of which existed in code yet. Rather than stub these out or build ad hoc logic inside the backtesting package itself (which would have meant the Cost & Tax Engine's logic living in two places once it's "really" built later, or the Backtesting Engine silently taking on responsibilities that belong to Performance Analytics per the module boundaries already agreed in Phase 1), I implemented each as its own package with a clean interface, exactly as the frozen architecture already specified their responsibilities. The Backtesting Engine composes them; it doesn't reimplement them.

## The no-look-ahead guarantee (objectives #2, #4) — how it actually works

This is the property most likely to be silently violated in a hand-rolled backtester, so it's structural, not a coding convention:

1. `Strategy.generate_orders(as_of_date, history, portfolio)` — `history[symbol]` contains bars with `trade_date <= as_of_date` only. Enforced by the engine's bar-pointer bookkeeping, with a defensive runtime assertion (`LookAheadViolationError`) that fires if this invariant is ever violated — not just documented, checked on every call.
2. Orders returned from that call are queued and can fill **no earlier than `as_of_date + 1`**. There is no code path where a decision and its fill reference the same bar.
3. Market orders fill at the **next** bar's open, never the decision bar's close — the classic look-ahead bug this design exists to prevent.
4. Limit orders fill only if the execution bar's actual range touches the limit price, with a favorable-gap adjustment (fill at the better price if the market gaps through your limit) — not an optimistic "the strategy wanted it, so it happened."

`tests/unit/test_backtest_no_lookahead.py` verifies all of this directly against the engine's actual behavior, including a test that deliberately corrupts internal bookkeeping to prove the defensive assertion is live code, not a docstring claim.

## Realistic execution modeling (objective #5)

Every cost/tax rate in `cost_tax_engine.py` is either **cited** against a source verified via web search at build time (STT 0.1% both sides on equity delivery, unchanged by Budget 2026; stamp duty 0.015% buy-side; GST 18% on brokerage+exchange charges; LTCG 12.5%/STCG 20% at the 365-day boundary) or explicitly flagged **approximate** (exchange transaction charge, SEBI turnover fee — minor line items, not individually re-confirmed against a live circular in this sandbox). Slippage is disclosed as a modeling assumption, not a regulatory rate. See that module's docstring for the full source list — nothing was fabricated or presented with more confidence than it deserves.

FIFO tax-lot tracking feeds both the tax classification (STCG/LTCG) and the win/loss P&L statistics from the same matched-lot data — one mechanism serving two purposes, not two separate implementations that could drift apart.

## Survivorship bias (objective #3) — "where practical," honestly bounded

The engine accepts whatever `bars_by_symbol` you give it — nothing prevents supplying a point-in-time universe (a different symbol list per period, reflecting what was actually tradable then, including since-delisted ETFs). But Phase 2's Data Engine has no source of delisted-ETF historical data, so **today**, any backtest you run will only ever see ETFs that currently exist. This is a data-completeness limitation carried over from Phase 2, not a Phase 4 design gap — the engine is structurally ready for point-in-time universes the moment such data exists; it just doesn't have that data yet.

## Reproducibility (objective #9)

Composed almost entirely from what Phase 2 already built: `config_version` (ConfigManager), `data_snapshot_id` (HistoricalDataEngine), plus one genuinely new piece — a git commit hash captured via `subprocess` at run time, with a `code_is_dirty` flag if uncommitted changes were present. The project's own git repository was initialized as part of this phase specifically so this is a real, meaningful hash going forward, not a permanent "unknown."

## Every trade has an explanation (objective #11)

`OrderIntent.__post_init__` raises `InvalidOrderError` immediately if `rationale` is empty — a Strategy cannot produce an order the engine will accept without one. `Trade.explanation` combines that stated rationale with the concrete outcome (fill price, date, cost, and — for sells — the realized gain and estimated tax), so the explanation reflects what actually happened, not just what was intended.

## Walk-forward validation: per-window statistics, not one spliced curve

See `walk_forward.py`'s module docstring for the full rationale — briefly: concatenating windows into one equity curve (each resetting to initial capital) would produce a curve with jarring resets that represents no real deployment, and would bury the actual question walk-forward validation exists to answer (does this hold up consistently across periods, or did it get lucky once). A distribution of per-window XIRR/Sharpe/drawdown answers that directly.

## Monte Carlo: block bootstrap, consistent with Phase 3

Same methodology as Phase 3's `stats.py` (block resampling to preserve return autocorrelation, not naive i.i.d. resampling) — one validated technique reused, not a second one invented.

## Usage

```python
from etf_platform.backtesting import BacktestEngine, BacktestConfig, OrderIntent, OrderType, Strategy
from etf_platform.cost_tax_engine import CostTaxEngine, IndiaEquityCostConfig, Side
from etf_platform.performance_analytics import build_performance_report
from etf_platform.backtesting.reproducibility import build_reproducibility_record

class MyStrategy(Strategy):
    def generate_orders(self, as_of_date, history, portfolio):
        # decide using history[symbol] (bars <= as_of_date only) and portfolio state
        return [OrderIntent("NIFTYBEES", Side.BUY, OrderType.MARKET, 10, "Why this trade happened.")]

config = BacktestConfig(start_date=..., end_date=..., initial_capital=500000, symbols=("NIFTYBEES",))
engine = BacktestEngine(config, MyStrategy(), CostTaxEngine(IndiaEquityCostConfig()))
result = engine.run(bars_by_symbol)  # from HistoricalDataEngine.get_ohlcv(...)

curve = [(p.as_of_date, p.total_value) for p in result.equity_curve]
realized_pnls = [rg.gross_gain for t in result.trades for rg in t.realized_gains]
report = build_performance_report(curve, realized_pnls, benchmark_curve, "NIFTYBEES")
```

See `docs/PHASE4_Production_Readiness_Report.md` for the full self-review, including the two genuine bugs found and fixed during this phase.

## Adversarial review additions (Version 0.4)

Before freezing, a full adversarial review against 24 risk categories (look-ahead, survivorship bias, position sizing, cash accounting, dividends, corporate actions, numerical stability, and more — see `CHANGELOG.md` [0.4.0]) found and fixed nine real weaknesses. The most consequential additions to the public interface:

- **`BacktestEngine.run()` now accepts `corporate_actions_by_symbol`** — dividends credit cash based on quantity held on the ex-date; splits/bonus issues adjust held quantity and FIFO tax lots while preserving the *original* acquisition date (critical for correct STCG/LTCG classification — a split must never reset the capital-gains holding-period clock).
- **`BacktestResult.warnings`** now surfaces data-quality issues that used to be silent: stale prices on a held position (no data update for `stale_price_warning_days`, default 5), and early termination if data ran out before the configured `end_date` (also exposed as `BacktestResult.actual_end_date`).
- **`BacktestConfig.max_volume_participation_pct`** (opt-in, default `None`) caps any single fill at a fraction of the execution bar's traded volume, modeling that a large order can't realistically fill in full against thin liquidity in one day — the unfilled remainder is re-queued for subsequent days, respecting the order's original expiry.
- **`OrderIntent` now rejects fractional quantities** — ETFs trade in whole units on NSE; a 3.7-unit order was previously silently accepted.
- **`run_and_register()`** in `backtesting/registry.py` guarantees a `backtest_runs` row is always finalized (success or recorded failure), even if `engine.run()` raises partway through.

None of these changes altered the core simulation logic verified in the original Phase 4 delivery — the no-look-ahead guarantee, fill pricing, and cost/tax arithmetic are unchanged. The locked regression baseline (`test_backtest_regression.py`) still passes unmodified, since every new feature above is either purely additive (dividends/corporate actions require explicitly passing new data) or opt-in (the volume participation cap defaults to unlimited, the prior behavior).
