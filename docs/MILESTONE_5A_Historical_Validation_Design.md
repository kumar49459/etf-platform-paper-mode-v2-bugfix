# Milestone 5A — Historical Validation: Design and Validation Planning

**Status: PROPOSED. Design only, no code. Per your instruction, implementation does not begin until this document is reviewed and approved.**

## 0. Scope clarification (read first)

This milestone has no relationship to Module 28. The last four milestones built the *execution* layer (Paper/Live Trading lifecycle, reconciliation, stress-tested to 100,000 cycles). Milestone 5A is a *historical backtesting* exercise - the relevant frozen infrastructure is Phase 4's `BacktestEngine` and `validation` package (`WalkForwardValidator`, `MonteCarloSimulator`) and Phase 6's `StrategyEngine`, none of which Module 28 touches or is touched by. I'm flagging this explicitly because the "Milestone" numbering continues from Module 28's sequence, but the actual dependency graph resets to Phase 4/5/6.

**The single biggest finding of this design pass:** most of the financial math this milestone needs already exists, frozen, tested, and unmodifiable. XIRR, CAGR, Sharpe, Sortino, Calmar, max drawdown, 1-year rolling returns, and a complete walk-forward train/test framework are already in `performance_analytics/` and `validation/` (Phase 4, frozen). Milestone 5A is substantially an *orchestration and reporting* exercise - running the frozen engine across historical windows and regimes, and building the reports and metrics that don't exist yet - not a new-math exercise. This changes the risk profile of this milestone: the hard part isn't computing Sharpe ratios, it's getting real historical data deep enough to run them against.

---

## 1. Historical Data Requirements

### 1.1 What's actually available - stated honestly, not optimistically

I do not have live data access in this environment, so every date and figure below is from general knowledge, not verified against a live source. This is the first and most important risk in this entire document (see section 8).

| Symbol | ETF inception (approx., unverified) | Underlying index history available |
|---|---|---|
| NIFTYBEES (Nifty 50) | Dec 2001 | Nifty 50 index computed since Nov 1995, back-computed to 1990 base |
| BANKBEES (Nifty Bank) | May 2004 | Nifty Bank index since 2000 |
| JUNIORBEES (Nifty Next 50) | Feb 2003 | Nifty Next 50 index since 1997 (back-computed) |
| GOLDBEES (Gold) | Mar 2007 | Domestic gold price series available well before this (bullion spot/MCX) |
| LIQUIDBEES (Liquid/money market) | Jul 2003 | N/A - money-market proxy (e.g. overnight MIBOR/T-bill) usable pre-inception |

**Every one of these ETFs has meaningfully less history than the regimes you asked me to cover.** The Dot-com crash (2000-2001) predates every ETF above. The GFC (2007-2009) predates GOLDBEES and barely overlaps BANKBEES/JUNIORBEES's early, thin-liquidity years.

### 1.2 Proxy methodology (your requirement: document every assumption)

For any date range before an ETF's actual inception, the design uses the **underlying index's total-return series** as a proxy, with an explicit, disclosed haircut applied to approximate real ETF costs the index itself doesn't carry:

- **Tracking error / expense ratio haircut**: a fixed annualized drag (proposed default: 25-50 bps/year, a realistic range for a well-run passive Indian ETF, but *provisional and disclosed*, same honesty standard as every other assumption in this platform) subtracted from the index's daily return series before computing any metric.
- **Pre-inception periods are explicitly labeled as "index-proxy" in every report**, never presented as if they were real ETF-level returns. This labeling is a hard requirement, not a footnote - any report showing pre-inception performance must visually and textually distinguish it from post-inception real-ETF performance.
- **Liquid BeES has no meaningful index to proxy** - it tracks overnight money-market returns, not a published benchmark index in the usual sense. Proposed proxy: an overnight MIBOR-linked or T-Bill total-return series, clearly labeled as an approximation of a fundamentally different (near-riskless, low-volatility) instrument.

### 1.3 Corporate actions and adjustments required

- **Splits and bonus issues**: rare for ETFs directly, but the underlying constituents' corporate actions are already reflected in the index level itself (a total-return index absorbs this) - no separate adjustment needed for index-proxy periods.
- **Dividends**: must use **total-return (TRI)** index series, not price-return (PRI), for any index-proxy period, and must use the ETF's actual dividend distribution history where using real ETF-level data. Phase 4's frozen dividend handling (DIVIDEND events crediting cash) is directly reusable for the ETF-level portion of the timeline.
- **AMC/scheme changes**: some of these ETFs have changed fund houses or been renamed over their history (e.g. the "BeES" family changing hands over the years) - the underlying fund and its track record is continuous through these changes, but this needs to be verified per-symbol against real fund-house disclosures, not assumed.

---

## 2. Market Regime Coverage

### 2.1 Regime table - Indian-market-specific dates, not US-market dates

| Regime | Proposed date range | Notes |
|---|---|---|
| Dot-com crash | Feb 2000 - Sep 2001 | Index-proxy only for every symbol above - no ETF existed yet |
| 2003-2007 bull run | Apr 2003 - Jan 2008 | Strong secular bull market, mostly index-proxy for GOLDBEES |
| Global Financial Crisis | Jan 2008 - Mar 2009 | Nifty fell roughly 60% peak-to-trough (approximate, unverified) - index-proxy for GOLDBEES, thin-liquidity ETF data for others |
| 2009-2013 recovery/sideways | Apr 2009 - Aug 2013 | Real ETF data available for all five symbols |
| 2013 Taper Tantrum | May 2013 - Sep 2013 | Not in your original list - recommending it be added. A short but sharp, India-specific stress event (Fed tapering triggered an INR/capital-flight crisis) distinct in character from the other listed regimes; real ETF data available throughout |
| 2014-2017 bull run | 2014 - Dec 2017 | Real ETF data, all symbols |
| 2018 correction | Jan 2018 - Oct 2018 | IL&FS crisis, NBFC stress - a genuine distinct regime, smaller than GFC/COVID |
| COVID crash | Feb 2020 - Mar 2020 | Approximately 38% decline in ~5 weeks (unverified figure) |
| COVID recovery | Apr 2020 - Dec 2021 | V-shaped, real ETF data throughout |
| 2022 bear/correction | Jan 2022 - Jun 2022 | FII outflows, rate hikes, Ukraine war - real ETF data |
| Rising-rate/inflationary period | 2022 - 2023 | RBI hiking cycle - overlaps the 2022 bear regime above, listed separately because your requirement names it as its own category; needs a decision on whether to treat it as distinct or a sub-period of 2022 bear (see section 8) |
| Recent recovery | 2023 - present | Real ETF data throughout |

**All dates above are approximate and unverified against a live source - this entire table needs confirmation against real historical data before it's used as ground truth for any report.**

### 2.2 High/low volatility classification

Rather than fixed date ranges, proposing this be computed *dynamically* from the historical data itself (rolling realized volatility above/below a percentile threshold) rather than hand-picked periods - this avoids the circularity of defining "high volatility" by eye and then being unsurprised when the strategy's Sharpe ratio looks different during periods selected for having been volatile.

---

## 3. Cost Model

**No new cost model needed - Phase 4's frozen CostTaxEngine / IndiaEquityCostConfig already has every component you listed, with each rate's confidence level already disclosed in the frozen source:**

| Component | Frozen default | Disclosed confidence |
|---|---|---|
| Brokerage | 0.0% (assumes a discount broker with zero delivery brokerage) | Assumption, matches a specific real broker's current pricing |
| STT (buy + sell) | 0.1% each side | CITED |
| Stamp duty | 0.015%, buy side only | CITED |
| Exchange transaction charge | 0.00297% | APPROXIMATE, flagged for verification against current NSE schedule |
| SEBI turnover fee | 0.0001% | APPROXIMATE, flagged for verification |
| GST | 18%, on brokerage + exchange charge + SEBI fee only | CITED |
| Slippage | 5 bps | ASSUMPTION, explicitly tunable |
| STCG / LTCG | 20% / 12.5% | CITED |

**Liquidity constraints**: Phase 4's FillSimulator already supports opt-in max_volume_participation_pct for partial fills - reusable directly for historical validation, simulating realistic execution against historical volume rather than assuming infinite liquidity.

**Nothing here requires new code.** The only work is wiring CostTaxEngine and FillSimulator's existing capabilities into the historical validation run, which BacktestEngine already does by default.

---

## 4. Validation Metrics - Design

| Metric | Status |
|---|---|
| CAGR | Exists (performance_analytics.metrics.cagr) |
| XIRR | Exists (metrics.xirr) |
| Sharpe / Sortino / Calmar | Exist |
| Max drawdown | Exists |
| Rolling 1-year returns | Exists - needs extension to 3/5/10-year (a parameter change to the existing rolling_returns function, not new logic) |
| Annual returns (year-by-year table) | New - a thin aggregation over the existing equity curve, no new financial math |
| Monthly returns | New - same, monthly granularity |
| Volatility | New as a standalone reported metric - the underlying daily-returns computation already exists, just needs to be surfaced directly rather than only as a Sharpe/Sortino input |
| Recovery time | New - time from a drawdown's trough back to the prior peak; needs new logic walking the equity curve, distinct from max_drawdown_from_equity_curve's single-number output |
| Portfolio turnover | New - (value bought + value sold) / average portfolio value over a period; computable directly from BacktestResult.trades, no new backtesting logic needed |
| Transaction costs | New as an aggregated report - the per-trade cost data already exists via CostTaxEngine; needs summing/reporting across a run, not new cost computation |
| Cash utilization | New - average deployed-vs-idle cash over the run, computable from the existing equity curve's cash component |

**Net new code required: a historical_validation reporting layer that consumes BacktestResult (frozen, unmodified) and produces the six "New" metrics above plus year/month tables - no changes to BacktestEngine, CostTaxEngine, or performance_analytics themselves.**

---

## 5. Walk-Forward Validation Methodology

**Also substantially already built.** WalkForwardValidator (Phase 4, frozen) already implements rolling training/testing windows and generates a WalkForwardSummary. What Milestone 5A adds:

- **Explicit train/validate/test three-way split**, not just the existing train/test two-way split - WalkForwardValidator's current design generates windows and evaluates directly; adding a middle "validation" window (used for any parameter tuning) before the final untouched "test" window is a real, new design decision, not just reuse. Proposing: an outer walk-forward loop (reusing the existing validator) where each window is itself split 60/20/20 train/validate/test, with strategy parameters frozen based on train+validate only, and test-window performance never used to adjust anything - the literal definition of preventing look-ahead bias.
- **Regime-aware window boundaries**: aligning walk-forward window boundaries to regime transitions (section 2) where practical, so a single window doesn't straddle, say, the COVID crash and mask its effect by averaging it with calm periods on either side. This is a genuine design tension worth naming: regime-aligned windows give cleaner regime-specific insight; fixed-length rolling windows (the validator's current default) give more statistically comparable windows to each other. Recommending both be run and reported separately, not choosing one - they answer different questions.

---

## 6. Failure Analysis - Report Design

- **Worst-performing periods**: derived directly from the annual/monthly return tables (section 4) - no new computation, just sorting.
- **Largest drawdowns**: max_drawdown_from_equity_curve already computes the single worst drawdown; a new drawdown-episode detector is needed to enumerate all significant drawdown episodes (not just the single worst), each with its own recovery time (section 4).
- **Allocation changes**: Strategy Engine's own rationale text (Phase 6, frozen) already narrates why each trade happened - a report that walks the trade history chronologically, annotated with the regime each trade fell into, is new reporting logic over existing, unmodified data.
- **Execution statistics**: trade count, average fill quality, partial-fill frequency - all derivable from BacktestResult.trades (frozen), no new backtesting logic.

---

## 7. Reproducibility

**Already partially built** - Phase 4's reproducibility.py (get_code_version, git commit hash + dirty-state tracking) already covers "strategy version" and "configuration version" in the sense of "what code produced this."

**New for this milestone: a data version.** Nothing in this platform currently versions the historical dataset itself - necessary because the same code run against a dataset that's been corrected, extended, or re-sourced later would silently produce different results. Proposing a DataManifest: a hash of the exact historical data file(s) used for a given run, stored alongside the report, so "reproduce this exact result" means "same code + same data + same seed," not just the first two.

**Random seed**: MonteCarloSimulator (frozen) already accepts an explicit rng - any run using it must record the seed used. WalkForwardValidator and BacktestEngine are otherwise deterministic given identical inputs, so "seed" only matters for the Monte Carlo layer specifically.

**Report version**: a simple, new schema-version field on whatever report format Milestone 5A produces, so future changes to the report structure don't silently break comparability with older archived reports.

---

## 8. Risks and Assumptions (stated plainly, not buried)

1. **This environment has no live data access.** Every date, price level, and drawdown figure in section 2 is from general knowledge, unverified. This is the largest single risk to this milestone's credibility - the design can be approved, but the actual numbers in any report are only as good as the real data source eventually used.
2. **Historical data depth is a genuine open question, not a solved problem.** Phase 2's existing DataProvider abstraction (NSE/Kite) was built for recent history and live quotes - Kite Connect's historical API typically does not provide multi-decade depth. Getting data back to 2000 likely requires a different source entirely (NSE's own historical bhavcopy archives, AMFI for older mutual-fund-era NAV proxies, or a paid historical data vendor) - this needs a real decision before implementation, not an assumption that the existing data pipeline just has more history than it's been asked for yet.
3. **The tracking-error/expense-ratio haircut (section 1.2) is a provisional guess**, not a researched number - needs real ETF expense-ratio disclosures to tune properly.
4. **Regime date boundaries (section 2) are approximate and drawn from general knowledge** - precise turning points need verification against real index data, not asserted from memory.
5. **The rising-rate/inflationary period overlaps the 2022 bear regime** - needs your decision on whether these are one regime or two (section 2.1).
6. **None of this required inventing new financial math** - which is a genuinely good sign for implementation risk, but means the actual engineering risk in this milestone is almost entirely about data sourcing and reporting design, not backtesting correctness (Phase 4's engine is already adversarially reviewed and frozen).

---

## 9. Implementation Roadmap (proposed, pending approval)

1. **Data sourcing** - resolve risk #2 above first; nothing else can proceed without real historical data of adequate depth.
2. **Data proxy/adjustment pipeline** - implement the index-proxy + haircut methodology (section 1.2) as a new, isolated module, never modifying Phase 2's frozen DataProvider.
3. **New metrics module** - the six "New" metrics from section 4, built against BacktestResult (frozen), unit-tested against hand-computed reference values.
4. **Regime-segmented and full-history report generation** - orchestration over the existing BacktestEngine/WalkForwardValidator, no changes to either.
5. **Train/validate/test three-way walk-forward extension** (section 5) - the one genuinely new methodological piece, built as a wrapper around the existing validator, not a modification to it.
6. **Reproducibility manifest** (section 7) - data hashing + report versioning.
7. **Adversarial review of the new reporting layer** - same discipline as every prior phase, applied to the new code only (the reused frozen engine has already been through this).

---

## 10. Summary of Items Requiring Your Explicit Decision

- **Section 1.2**: confirm the index-proxy + tracking-error-haircut methodology for pre-ETF-inception periods, and the proposed haircut range (25-50 bps/year, provisional).
- **Section 2.1**: confirm the addition of the 2013 Taper Tantrum as a regime not in your original list, and confirm/adjust the proposed date ranges once verified against real data.
- **Section 2.1**: decide whether the rising-rate/inflationary period is its own regime or a sub-period of the 2022 bear market.
- **Section 5**: confirm the proposed 60/20/20 train/validate/test split methodology, and confirm running both regime-aligned and fixed-length walk-forward windows rather than choosing one.
- **Section 8, risk #2**: this is the one that actually blocks implementation - a real decision is needed on the historical data source before any code is written, since the entire milestone depends on it.

Everything else in this document is a design decision with stated reasoning, not an open question.
