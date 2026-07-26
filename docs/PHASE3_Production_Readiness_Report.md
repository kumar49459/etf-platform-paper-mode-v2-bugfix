# Phase 3 — Production-Readiness Report

**Scope:** ETF Metadata Manager, Universe Screening Engine, ETF Universe Optimizer (scoring), Portfolio Candidate Generator.
**Test suite: 205 tests total, all passing** (134 Phase 2 + 71 new Phase 3 tests).
**Status: production-ready for its actual scope, with disclosed data-completeness limitations that block real-capital use until closed — see below.**

## What was verified

**Architecture compliance.** Reuses `HistoricalDataEngine` and the provider abstraction unmodified, as required — `etf_optimizer` never imports NSE/Kite clients directly. Correctly placed as research-side (numpy dependency, per Phase 1 §12.1); the live trading process does not and must not import this package.

**Explainability.** Every `ETFScore` carries a full `MetricScore` breakdown per dimension (raw value, z-score, weight, contribution, direction, and — for excluded metrics — why). Every `ScreeningResult` carries every individual check's outcome, not just a pass/fail boolean. This was a hard requirement ("produce explainable rankings") and is structurally enforced, not just documented — there is no code path that returns a composite score without its breakdown.

**Statistical rigor.** The replacement-evidence gate (`stats.py`) was the highest-priority module to get right, given your explicit instruction. It was tested against three specific scenarios: a clearly superior candidate (correctly flagged significant), a noise-only difference between two draws of the same process (correctly not flagged), and a candidate with better mean return but an engineered severe drawdown (correctly flagged as return-favorable *and* drawdown-worse, with both facts surfaced rather than one hiding the other). The single-best-peer-only testing policy (see design doc) was a deliberate anti-p-hacking choice, not an oversight — tested implicitly by `test_no_recommendation_across_different_asset_classes`, which confirms a dramatically "better-scoring" ETF in a *different* category is never even proposed as a candidate for testing.

**No unhelpfully manufactured recommendations.** The end-to-end smoke test against the real six named ETFs produced **zero** replacement recommendations — not because the code failed, but because a six-ETF universe with five distinct asset-class categories genuinely has almost no same-category peers to compare. This is the conservative behavior the design requires: no peer, no evidence, no recommendation. A broader universe (more ETFs per category) is needed before this module can realistically flag anything.

## What blocks real-capital use (disclosed, not hidden)

1. **`expense_ratio`, `tracking_error_pct`, and `aum_crores` are `null` for all six named ETFs** in the shipped overrides file. This is not a bug — it's the honest state of a sandbox with no network access to AMFI/fund-house factsheets. Screening thresholds involving these fields will return `UNKNOWN` (excluded, not silently passed) until populated. **Action required before real use:** populate these fields from a real feed.
2. **`MOMIDMTM`'s exact tracked index could not be confidently verified** during this build (see the overrides file's `needs_verification` flag and notes). It is functionally excluded from replacement-evidence comparisons as a result (`asset_class: null`) — this is the *safe* default, not a data loss, but it means Phase 3 currently cannot evaluate this specific holding at all. **Action required:** verify the symbol against the NSE instrument master or Motilal Oswal's factsheet.
3. **"Complete Indian ETF universe" support is structural, not populated.** `ETFUniverseOptimizer.optimize()` accepts an arbitrary-length `universe_symbols` list and will score however many symbols you give it — but this phase does not include a maintained source of "every NSE-listed ETF symbol." Per the design doc's rationale, building that list from a wrong or incomplete auto-discovery mechanism would be worse than requiring an explicit, correct list. **Action required before "complete universe" screening is meaningful:** supply a real, maintained ETF symbol list (e.g. from AMFI's ETF category listing) as `universe_symbols`.
4. **A minimum-observations gate (60 trading days) exists for statistical validation**, and screening's default `min_trading_days_history=60` — both are conservative floors, not tuned recommendations; revisit if your actual holding-period assumptions differ.

None of these four items are code defects — the code handles every one of them safely (explicit `UNKNOWN`/exclusion, never a silent guess). They are data-completeness gaps that exist because this build environment has no network access, exactly as disclosed throughout Phase 2 as well.

## Test coverage breakdown

| Module | Tests | Notably covers |
|---|---|---|
| `price_metrics.py` | 16 | Return/volatility/drawdown/correlation math, including edge cases (constant series, no date overlap) |
| `metadata_manager.py` | 10 | Merge policy (all 4 source combinations), malformed file handling, sanity check against the real shipped overrides file |
| `screening_engine.py` | 13 | Every check's PASS/FAIL/UNKNOWN paths, FAIL-over-UNKNOWN priority, optional-check omission when no threshold set |
| `scoring.py` | 12 | Direction correctness for 3 of 8 metrics explicitly (liquidity, expense ratio, volatility), missing-data-is-neutral policy, single-candidate edge case, correlation/diversification against a reference portfolio |
| `stats.py` | 11 | Bootstrap correctness across superior/noise/mismatched-length/insufficient-length scenarios, confidence-level-affects-width sanity check |
| `universe_optimizer.py` | 3 | End-to-end screening+scoring wiring |
| `candidate_generator.py` | 6 | End-to-end pipeline including the two most important guardrails: cross-category rejection and drawdown-tradeoff surfacing |

**Not separately unit-tested (disclosed):** the remaining 5 of 8 scoring dimensions (AUM, tracking error, trading volume, correlation, diversification) are exercised in integration tests but don't each have a dedicated direction-correctness test like liquidity/expense-ratio/volatility do. They share the same z-score/direction machinery already tested for the other three, so the risk is low, but this is a real gap if you want to extend or re-weight the scoring formula later — add direction tests for the remaining 5 before doing so.

## Recommendation

Phase 3 is ready to move forward as designed, with the explicit understanding that its output quality is currently bounded by the data-completeness gaps above — the code is trustworthy about what it does and doesn't know, but what it knows is still incomplete. Populate the overrides file and supply a real universe symbol list before using this module's rankings or recommendations to inform actual capital allocation.
