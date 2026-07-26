# Phase 3 — ETF Universe Optimizer: Design & Usage

**Modules:** `ETFMetadataManager`, `UniverseScreeningEngine`, `ETFUniverseOptimizer` (scoring), `PortfolioCandidateGenerator`.
**Package:** `src/etf_platform/etf_optimizer/` — research-side only (see below).

## Architectural placement

Per `PHASE1_Architecture_SRS.md` §12.1, this package depends on `numpy` (statistics, z-scoring, bootstrap resampling) and belongs on the **on-demand/research instance**, not the always-on live micro. It reuses Phase 2's `HistoricalDataEngine` unchanged — no provider-abstraction code was modified, exactly as required. The live trading process must never import from `etf_optimizer`.

## Pipeline

```
ETFMetadataManager          — merges Data Engine provider metadata + overrides file
        |
UniverseScreeningEngine     — explainable PASS/FAIL/UNKNOWN gate (never silently promotes missing data)
        |
ETFUniverseOptimizer        — z-score composite ranking across 8 dimensions, explainable per-metric breakdown
        |
PortfolioCandidateGenerator — statistically validated (block bootstrap) replacement evidence, or explicit "no evidence"
```

## Key design decisions (with rejected alternatives)

### Metadata: overrides file, not invented numbers
NSE/Kite give price and volume; AUM and expense ratio come from AMFI/fund-house factsheets, a source Phase 2 doesn't touch. `config/etf_metadata_overrides.yaml` supplies `asset_class`, `index_tracked`, `issuer` (populated, stable facts) and leaves `expense_ratio`/`aum_crores`/`tracking_error_pct` as `null` (need a live feed, honestly disclosed rather than faked). **Rejected:** seeding plausible-looking placeholder numbers — this would look more complete while being actively misleading, since a screening/scoring decision made against a fabricated AUM figure is worse than one made against a visible gap.

### Screening before scoring, not folded together
Z-score normalization only means something relative to the comparison set. An unscreened universe still containing near-abandoned ETFs would compress every real candidate's score toward the mean. **Rejected:** scoring the full universe and filtering by score afterward — this lets illiquid outliers distort the very statistics used to rank the ETFs that matter.

### Three-state screening (PASS/FAIL/UNKNOWN), not boolean
A missing AUM figure is not evidence of low AUM. Every check that depends on possibly-missing data returns `UNKNOWN`, not a silent `PASS` or `FAIL`. `UNKNOWN` currently sorts to "excluded from scoring" alongside `FAIL` (conservative default per Phase 1's fail-safe NFR), but is reported separately so you can tell "we checked and it failed" apart from "we don't know."

### Equal weights (1/8) by default, explicitly disclosed as a choice
Nobody can defend a specific unequal weighting across liquidity/AUM/expense ratio/tracking error/volume/volatility/correlation/diversification without it being an opinion about investment philosophy. Equal weighting is the only default that doesn't silently embed an opinion as fact. Override via `ETFScorer(weights={...})`.

### Missing metric data contributes exactly 0, never a penalty or a reweighted exclusion
**Rejected — exclude and renormalize remaining weights per ETF:** this makes scores incomparable across ETFs with different missing-data patterns (an ETF missing 3 metrics would have its remaining 5 weighted more heavily than one missing 0), which is methodologically worse than a metric being neutral. A 0-contribution is transparent and uniform.

### Correlation vs. diversification: two genuinely different signals, not duplicates
- **Correlation** = Pearson correlation of daily returns against the current holdings' equal-weighted aggregate return series. Statistical, price-based.
- **Diversification** = fraction of current holdings with a *different* `asset_class` category. Categorical, not price-based.
Two ETFs can be uncorrelated by chance in a short sample while tracking economically similar things, or correlated by market-wide co-movement while being genuinely different asset classes (equity beta bleeding into everything during a crash, for example). Neither signal alone is sufficient; both are reported.

### Replacement evidence: block bootstrap, not a raw score comparison
See `stats.py`'s module docstring for the full statistical rationale (paired differencing to cancel common market movement; block resampling to respect return autocorrelation, which a plain t-test would ignore). Significance requires the confidence interval for the annualized mean return difference to exclude zero. **Rejected — "candidate scores higher, therefore recommend":** a composite score gap can be noise; only a validated statistical test counts as the evidence you explicitly required.

### Test only the single best-ranked peer per category, not every higher-ranked one
Testing multiple candidates and reporting whichever comes back significant is p-hacking — at 95% confidence, testing 5 independent candidates gives roughly a 23% chance of at least one false positive by chance alone. Testing exactly one candidate (the best-ranked peer in the same `asset_class`) avoids this without a Bonferroni correction whose severity would otherwise depend on how large the universe happens to be — not a property a good evidence bar should have.

### A significant, favorable result with worse drawdown is still reported — never suppressed
Per Phase 1 §12.2 (capital preservation priority, "present both options"), a `ReplacementRecommendation` with `drawdown_worse=True` carries an explicit `drawdown_tradeoff_note` requiring manual review, rather than either auto-approving the return improvement or hiding it because of the drawdown. The algorithm surfaces the trade-off; a human (eventually, the Approval Console) decides.

## Known limitation: category granularity determines what counts as a "peer"

In the shipped overrides file, NIFTYBEES (`equity_large_cap`) and JUNIORBEES (`equity_large_cap_extended`) are deliberately **different** categories — they track different indices (Nifty 50 vs. Nifty Next 50) with different risk/return characteristics, so they are not treated as substitutes for each other. This is a real classification choice, not an oversight — verify the `asset_class` taxonomy in `config/etf_metadata_overrides.yaml` matches your own view of what counts as a genuine substitute before relying on the absence of a recommendation as "no better option exists." With a small universe (six ETFs, five distinct categories), most incumbents will have zero same-category peers to test against at all — the smoke test in this delivery demonstrates exactly that, and it's the system behaving conservatively as designed, not a bug.

## Usage

```python
from etf_platform.etf_optimizer import ETFMetadataManager, PortfolioCandidateGenerator
from etf_platform.etf_optimizer.models import ScreeningThresholds

metadata_manager = ETFMetadataManager(data_engine, "config/etf_metadata_overrides.yaml")
generator = PortfolioCandidateGenerator(
    data_engine, metadata_manager,
    thresholds=ScreeningThresholds(min_trading_days_history=200),
)
report = generator.generate(
    universe_symbols=[...],            # full candidate universe
    current_holdings=["NIFTYBEES", "JUNIORBEES", "GOLDBEES", "MON100", "HDFCSML250", "MOMIDMTM"],
)

for score in report.universe_report.ranked_scores:
    print(score.rank, score.symbol, score.composite_score)
for rec in report.replacement_recommendations:
    print(rec.rationale)
```
