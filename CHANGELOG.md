# Changelog

All notable changes to this project are documented here. Format loosely follows [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] - Bug Fix: Paper Mode required ETF_PLATFORM_MASTER_KEY

Real bug, found via real execution evidence from a user's actual environment (a genuine startup crash: `SecretsBackendUnavailableError: Environment variable 'ETF_PLATFORM_MASTER_KEY' is not set`), not a hypothetical.

**Root cause**: `PaperRunner.startup()` constructed `SecretsManager` unconditionally, and `LocalEncryptedFileProvider` (frozen, `secrets_manager` package) loads and validates the Fernet key at *construction* time, not on first `get_secret()` call. So paper mode failed before it ever reached the point of treating Telegram as optional.

**Fix, confined entirely to `paper_runner.py`** (`secrets_manager` package and `ProductionRunner` both untouched, confirmed via `git diff HEAD`): `SecretsManager` construction moved inside `_build_notifier()`'s existing try/except, which now catches the base `SecretsProviderError` (covering both a missing individual secret and a missing/unusable backend) instead of only `SecretNotFoundError`. Both failure modes now degrade the same way in paper mode: no Telegram this session, not a startup failure.

**Verified by real execution**: reproduced the exact reported scenario (`ETF_PLATFORM_MASTER_KEY` genuinely unset via `os.environ.pop`) and confirmed startup now succeeds. Separately confirmed `ProductionRunner` still fails immediately and identically without the master key -- Live Mode's security is completely unweakened, proven by execution, not just by "the file wasn't touched."

5 new regression tests (`TestPaperRunnerStartsWithoutMasterKey`, `TestLiveModeSecurityUnweakened`), including one that submits and fills a real order with the master key absent, and one that positively re-confirms `ProductionRunner`'s fail-fast behavior is unchanged.

832 tests total (827 prior + 5 new), all passing.

## [Unreleased] - Paper Trading Mode

Authorized as new development work, explicitly outside the code freeze that had been in effect since Milestone 8 -- not a production hotfix. Lets the platform start and run without any Kite Connect credentials, for local development.

**New files:**
- `src/etf_platform/paper_trading_operations/paper_runner.py` -- `PaperRunner`, mirroring `ProductionRunner`'s startup/shutdown sequence (config loading, logging, SecretsManager, Telegram, mandatory reconciliation, signal handling) but using the existing, already-frozen, 100,000-cycle-tested `PaperBrokerPort`/`PaperQuoteProvider` instead of `KiteAuthManager`/`KiteBrokerPort`. Built as its own class rather than a flag inside `ProductionRunner`, so `ProductionRunner` itself remains completely untouched and "does this path ever touch a real broker" stays answerable by which class was instantiated. Telegram is optional in this runner (a warning, not a startup failure) -- deliberately different from `ProductionRunner`, where a missing notification channel is refused outright.
- `src/etf_platform/main.py` -- the project's first entry point (`python -m etf_platform.main`). Resolves `ETF_PLATFORM_ENV` (`paper`/`dev` -> `PaperRunner`, `live`/`production` -> `ProductionRunner`, anything else -> explicit `ValueError`, never a silent guess). Selection logic only, no duplicated startup or signal-handling behavior.
- `tests/unit/test_paper_runner.py` -- 15 new tests, including a real-signal shutdown test (`os.kill`, not simulated) and an AST-based check (not a blanket string search) confirming no `KiteAuthManager`/`KiteBrokerPort` import exists anywhere in the paper-mode path.

**Real defect caught before it shipped**: the first draft had `main.py` register its own SIGTERM/SIGINT handlers, which would have conflicted with `ProductionRunner`'s existing ones and left `PaperRunner`'s shutdown loop hanging forever, since `PaperRunner` didn't yet have signal handling of its own. Fixed by adding `_register_signal_handlers()` to `PaperRunner`, mirroring `ProductionRunner`'s exact pattern, and removing the duplicate from `main.py` entirely.

**Verified by real subprocess execution**, not just unit tests: `python -m etf_platform.main` run as an actual background process with `ETF_PLATFORM_ENV=paper` and zero Kite secrets configured -- confirmed startup, confirmed it stayed running, sent a real `SIGTERM`, confirmed clean exit, full log output captured as evidence.

**Startup command:**
```bash
export ETF_PLATFORM_ENV=paper
export ETF_PLATFORM_MASTER_KEY=<your Fernet key>
python -m etf_platform.main
```

**Verification commands:**
```bash
python -m unittest tests.unit.test_paper_runner -v
python -m unittest discover -s tests
```

**Separately flagged, not fixed here (outside this change's scope)**: while confirming `ProductionRunner`'s wiring, found that `KiteBrokerPort` does not implement `get_last_traded_price`/`get_market_depth`, yet `ProductionRunner` passes the same `KiteBrokerPort` instance for both the `broker_port` and `live_quote_provider` roles `SubmissionOrchestrator` expects. If `VerificationService`'s liquidity checks ever call a quote-provider method during a real order, this would raise `AttributeError`. Worth deliberate verification before live order placement resumes -- not touched as part of this paper-mode work, since it's inside the frozen live-trading path.

827 tests total (812 prior + 15 new), all passing. Zero previously-frozen files modified -- confirmed via `git diff HEAD`, not assumed.

## [Unreleased] - Production Verification

Final gate before live Kite Connect integration. 2-year continuous operation validation (chosen as representative of this platform's multi-year SIP horizon, not an arbitrary duration), a disaster recovery exercise, and complete audit-trail reconstruction proof.

- 2-year run (2,190 cycle attempts): 40 restarts, 100% recovery, 0 duplicate submissions, 0 invariant violations, 15,834 total reconstructable events.
- `disaster_recovery.py`: deliberately concentrated (15% daily) severe failure injection across 5 disaster kinds, with immediate post-disaster recovery verification. Honest disclosure: 3 of the 5 kinds (unexpected shutdown, process termination, database interruption) share one implementation, since they manifest identically at the boundary Module 28 sees -- stated explicitly, not left to look like 3 independently-verified paths.
- Real defect found and fixed while building the exercise: the first version of recovery verification treated any exception (including a coincidental, unrelated injected failure hitting the verification call itself) as "recovery failed" -- produced 3 false-negative results in the first run despite the system having actually recovered correctly (confirmed by 0 final orphans/duplicates in that same run). Fixed with a retry; 33/33 recoveries confirmed afterward.
- Complete audit reconstruction proven for every order in a run, not a sample -- zero unreconstructable orders found, plus a cross-check that cycle-log status never regresses relative to the execution store's current truth.
- Refined, not repeated, the Milestone 5B memory-growth finding: doubling duration (1yr to 2yr) roughly doubled absolute growth (~50KB to ~94KB) rather than staying flat -- inconsistent with pure noise. Every tracked structure remains individually small and bounded; none explains it. Now characterized as a small (~45-50KB/year), operationally negligible, not-yet-identified growth, rather than repeating the earlier "possibly noise" hedge now that better evidence exists.
- New `docs/OPERATIONAL_RUNBOOK.md` and `docs/PRODUCTION_DEPLOYMENT_CHECKLIST.md` (BLOCKING items explicitly marked, most critically: unverified idempotency key support at the real Kite API).
- 6 new tests. 716 total.

## [Unreleased] - Milestone 5B: Extended Paper Trading

Continuous-operation validation over Module 28's existing, already-stress-tested execution machinery. Deliberately introduces no new execution-layer architecture, per instruction -- `paper_trading_operations/` is operational framing and reporting only.

- `ExtendedPaperTradingSession`: distributes cycles across many simulated days (not bunched together, unlike Module 28's own stress harness), schedules reconciliation on a realistic daily cadence, and periodically purges the broker's terminal orders -- all reusing `SubmissionOrchestrator`/`ReconciliationService`/`PaperBrokerPort`/`FailureInjector` unmodified.
- **Real defect found and fixed during this milestone's own long-duration run**: `sweep_outstanding()` -- records that didn't reach a terminal state within their own cycle's poll budget were previously never revisited, permanently orphaned, growing `open_orders_count` without bound. Fixed by tracking every non-terminal `execution_id` and giving it further chances across many days.
- **Second real defect found via the definitive 365-day validation run**: the live event recorder was found completely empty at the end of a run -- periodic `events.clear()` (for memory-boundedness) had nothing durable archived first, violating "the complete execution history must be reconstructable." Fixed with `EventArchive` (append-only JSONL, archive-before-clear, reconstructable by `cycle_id` merging archived + live).
- **Third and fourth real defects, same 365-day run, same root cause pattern**: `SessionState.cycle_log` and `resource_snapshots` were both append-only with no pruning, unlike events and the broker's own order dict. Fixed with `CycleLogArchive` (same archive-before-trim pattern, with `get_full_cycle_log()` merging live + archived so report generation for already-archived periods still works) and a bounded rolling window for `resource_snapshots` (no archive needed -- nothing requires its full history, only recent trend data).
- `reports.py`: daily/weekly/monthly operational reports, re-querying each order's CURRENT state at report-generation time rather than trusting a frozen log snapshot (an order logged mid-flight may resolve later). Found and fixed: an injected transient database failure was originally reported identically to a genuinely missing record -- now retried before being classified, distinguishing "read failed, retry" from "confirmed missing" (a materially different, more serious situation).
- `resource_trends.py`: first-half/second-half growth-ratio trend analysis (not just start/end comparison, which would miss a leak that plateaus late). Deliberately excludes intentionally-monotonic counters from leak detection (they would always classify as "growing," which says nothing about whether anything is actually leaking).
- 365-day (1,095-cycle) definitive validation run: 0 invariant violations, 0 duplicate submissions, 16 restarts all recovering cleanly, 362 reconciliation runs. Honest finding, not glossed over: a residual small-magnitude memory-growth signal (~50KB absolute over the full simulated year) remained after fixing the two confirmed structural leaks -- every known structure was individually measured and ruled out as the cause; consistent with allocator/GC noise at this scale, not proven to be one. Database growth was investigated and found proportional to (and actually declining per-unit relative to) genuine trading activity -- expected, healthy audit-trail behavior, not a leak, revealing a real limitation in `resource_trends.py`'s binary growth-ratio verdict for a store designed to retain history.
- 17 new tests. 704 total.

## [Unreleased] - Project Readiness Audit

Full repository audit, Phase 1 through the Historical Data Acquisition Module. Audit only -- zero source files modified.

- Verified mechanically (not from memory): frozen module integrity across the entire history since each freeze (v0.4/v0.5/v0.6, not just the latest milestone), one-directional dependency flow, provider equivalence (two different `CSVDataProvider` instances with identical data produce byte-identical `BacktestEngine` results).
- Real finding: `CHANGELOG.md` had not been updated since Phase 6 -- this entry and the ones below are the fix.
- Minor, disclosed, unfixed-by-policy: ~15-20 unused imports (cosmetic, not a functional defect); the deliberate "buy-only diff"/"affordable quantity" logic quadruplication across `strategy_engine` (x2), `portfolio_optimizer`, and `execution_manager` remains consistent and cross-referenced, not silently drifted.
- Delivered `docs/PROJECT_READINESS_REPORT.md`: subsystem-by-subsystem readiness (backtesting core ~90%, paper trading execution ~80%, live trading ~15-20%, real historical validation ~10%), remaining risks, technical debt, recommendations.
- 687 tests, all passing.

## [Unreleased] - Historical Data Acquisition Module

- Reused Phase 2's frozen `DataProvider(ABC)` interface directly rather than inventing a parallel `HistoricalDataProvider` interface -- confirmed the frozen interface was explicitly designed since Phase 1 §12.6 for exactly this extensibility.
- New: `CSVDataProvider`, `IndexProxyDataProvider` (decorator, relabels proxy data), `ValidatedDataProvider` (decorator, enforces the mandatory data-quality gate on every fetch, no bypass exposed), `HistoricalDataAcquisitionService` (registration + provenance-overlap rejection at register time, before any provider I/O).
- Proved, not assumed: `BacktestEngine` (frozen) consumes CSV-sourced data with zero code changes.
- Real bug found and fixed via testing: overlap detection originally only ran at `fetch()` time, after both providers had already been called; moved to `register()` time.
- Self-caught tool error: a malformed edit emptied `historical_validation/__init__.py`; caught by reading the file directly after the edit rather than trusting the tool's reported success, and rewritten correctly.
- New `docs/DATA_SOURCE_INTEGRATION_GUIDE.md`: formats, required columns, timezone/calendar assumptions, corporate-action handling (informational for validation, not automatic price adjustment -- disclosed gap), benchmark mapping.
- 17 new tests. 687 total.

## [Unreleased] - Milestone 5A: Historical Validation Framework

Design-first (`docs/MILESTONE_5A_Historical_Validation_Design.md`), then implementation, over two rounds of your review and correction.

- Key finding before writing code: Phase 4's frozen `performance_analytics`/`validation` packages already contain XIRR, CAGR, Sharpe, Sortino, Calmar, max drawdown, 1-year rolling returns, and a walk-forward validator -- this milestone is orchestration and reporting over existing, tested financial math, not new math.
- New: `provenance.py` (ETF-actual/index-proxy/synthetic as a structural property, overlapping/out-of-order segments raise), `tracking_difference.py` (measures REAL overlapping-period tracking difference, refuses to apply an unmeasured or unreliable adjustment -- replacing an earlier, rejected guessed-haircut proposal), `regimes.py` (6 mandatory regimes, dates explicitly flagged unverified), `extended_metrics.py` (annual/monthly returns, standalone volatility, drawdown-episode recovery time, turnover, cash utilization), `monte_carlo_robustness.py` (parameter-variation Monte Carlo re-running the real `BacktestEngine`, distinct from the frozen return-resampling simulator), `walk_forward_report.py` (in-sample vs out-of-sample, a comparison the frozen validator alone doesn't produce), `report_builder.py` (mandatory, structurally-enforced disclosure banner whenever synthetic data is present), `reproducibility_manifest.py` (data-hash-based dataset versioning, the one piece Phase 4's reproducibility.py didn't cover), `synthetic_data.py` (clearly-labeled, framework-validation only).
- Capability correction, stated directly rather than left standing: an earlier blanket claim of "no live data access" was wrong. `web_search`/`web_fetch` reach real external sources; `bash_tool` alone has no network. Verified all 5 mandatory ETFs' inception dates and benchmarks against 2-6 independent sources each (`verified_etf_records.py`), finding and disclosing 3 real cross-source conflicts (2 resolved via corroboration, 1 -- LIQUIDBEES -- genuinely unresolved). Bulk historical daily price data remains unavailable regardless.
- Real finding via the end-to-end demonstration itself: the mandatory data-quality gate correctly ABORTED the first full run attempt, catching a genuine price discontinuity caused by a bug in the demonstration script's own regime-stitching logic -- the framework's abort-on-integrity-failure requirement working against a real defect, not a staged example.
- Two adversarial-review gaps found and disclosed in-code: the walk-forward wrapper does not itself prevent look-ahead bias (depends on the caller's strategy factory); survivorship bias is structurally unaddressed.
- 37 new tests. 670 total at milestone close.

## [Unreleased] - AI Allocation Architecture Hook

- `AIAllocationPort`: interface-only preparation for a future AI Dynamic Allocation Engine, per your explicit instruction not to implement it yet.
- Key finding: required zero changes to frozen Strategy Engine (v0.6). `StrategyEngine` already accepts `target_weights` as an external input -- the AI integration point already existed implicitly in the frozen design as a caller-side seam.
- `DisabledAIAllocationPort` (the default) proven, not just asserted, to produce byte-identical `StrategyEngine` output versus bypassing the hook entirely.
- Lives in its own new package (`ai_allocation/`), deliberately not inside `strategy_engine` or `execution_manager` -- Module 28 confirmed structurally independent of AI via source-scanning tests, re-verified in every subsequent milestone.
- 9 new tests.

## [Unreleased] - Module 28: Portfolio Cash & Execution Manager (Paper Trading)

Four milestones, ~130+ new tests, 100,000+ stress-tested execution cycles across the milestone set, zero frozen files modified at any point (re-verified after every individual fix, not just at milestone boundaries).

**Milestone 1 - Foundation:** 9-state order lifecycle (`OrderLifecycleState` + `validate_transition`), `BrokerPort`/`LiveQuoteProvider`/`ComplianceCheckPort` abstract interfaces, `ExecutionStateStore` (crash-safe SQLite persistence, `PRAGMA synchronous=FULL`, DB-corruption detection via integrity_check), UTC-internal/IST-at-boundary timezone discipline. 549 tests.

**Milestone 2 - PaperBrokerPort:** deterministic, seeded, scenario-driven broker simulator supporting all 10 required scenarios (immediate/partial/delayed fill, rejection, cancellation, expiry, network timeout, API error, connection loss, quote unavailable). Real bug found via a 2,000-simulated-day long-duration run: `QUOTE_UNAVAILABLE` was silently unhandled in `get_order_status()`, permanently hanging affected orders -- fixed, plus a fail-loud catch-all added so no future unhandled scenario can hang silently again. 587 tests.

**Milestone 3 - VerificationService, SubmissionOrchestrator, ReconciliationService:** pure validation / orchestration-only / broker-is-authoritative-reconciliation, cleanly separated. Mandatory 50,000-cycle stress test, zero invariant violations. Seven real defects found and fixed via testing during this milestone alone, including a missing `SUBMITTED` intermediate transition and the crash-recovery path for "broker accepted an order but the process crashed before recording it locally" (solved via `client_reference` matching against the broker's own open-orders list). 619 tests.

**Milestone 4 - expanded failure injection, mutation testing, chaos restart testing:** generic `FailureInjector` covering database/storage, notification, quote-provider, and config-loading failures (beyond Milestone 2's broker-specific scenarios). Mandatory 100,000-cycle endurance test, zero violations. Mutation testing found 3/4 injected mutations caught by existing tests; the 4th was proven mathematically inert, not a coverage gap. Chaos restart testing at arbitrary (not just named) instruction boundaries. 633 tests.

### Not changed

No frozen module was modified across any Module 28 milestone. Every fix, however deep, was made in new Module 28 code -- verified via `git diff v0.6 --stat` after every individual change, not assumed.

## [0.6.0] - Phase 6 FROZEN: Strategy Engine

**Frozen.** Three verification passes complete: initial implementation, operational adversarial review, and a final production verification against ten specific criteria. 491 tests passing (86 new). See RELEASE_POLICY.md for the release criteria this version was verified against before tagging.

### Release Record (per RELEASE_POLICY.md)

```
Version: v0.6 (Frozen)
Frozen date: 2026-07-17
Git commit: see `git rev-parse v0.6^{commit}` (tag applied to the commit containing this record)
Test suite: 491 tests, all passing
Regression tests: every defect found across all three Phase 6 review passes has a dedicated
                  regression test -- see this entry's itemized findings and
                  docs/PHASE6_Production_Readiness_Report.md for the full list
Critical/High issues open: 0
Frozen interfaces from prior phases modified: 0 (verified via `git diff v0.5 --stat` against
                                                   every package frozen at v0.5 -- empty result,
                                                   re-checked after every fix across all three passes)
Documentation consistency: verified (PHASE6_Objectives.md, PHASE6_Production_Readiness_Report.md,
                                      CHANGELOG.md cross-checked for matching test counts and
                                      status language)
EC2 Micro compatibility: verified (no numpy/scipy loaded, checked in a fresh subprocess)
```

### Final production verification findings (third pass)

Two more real gaps found and fixed, on top of the four from the operational review:

- **Power-failure durability gap.** `StrategyStateStore` inherited WAL mode's default `synchronous=NORMAL`, which guards against corruption but not against losing the most recent commit in an actual power-loss event (distinct from a process crash). Fixed with `PRAGMA synchronous=FULL`, scoped to `StrategyStateStore` specifically -- not a change to the frozen `common/db.py` shared by every other registry.
- **Side-effect ordering was still not fully structural.** The operational review's fix shrank the crash window for the reminder-duplication risk but didn't eliminate the underlying issue: `notification_port.send()` was still called *inside* the policy, before the resulting state was returned for persistence. Fixed properly: `ExecutionPolicy.run_cycle()` no longer performs any external write -- it returns a `PendingSideEffects` value, and `strategy.py` persists state first, then performs the effects. Verified by recording the actual `save()`/`send()` call sequence and asserting the order in the literal event log, not inferring it from the code.

### Verified against all ten production criteria, each with a dedicated test

No duplicate orders across restart/crash/power-failure/retry/network-interruption; no silent skip (a stuck cycle stays `EXECUTING`, never silently completes); no duplicate reminder (behaviorally and now structurally); order quantity reserves for all seven `CostBreakdown` components (brokerage, STT, stamp duty, exchange charges, SEBI charges, GST, slippage); quantity is always a Python `int`; `OrderType.MARKET` does not appear in `strategy.py`; zero Kite/Module 27/Module 28/exchange-calendar/execution-layer coupling (verified by source scan, not assumed); state persisted before every external side effect (verified structurally); no numpy/scipy loaded (verified in a fresh subprocess); zero frozen files modified (re-checked after every fix).

### Test count

**491 total** (405 through v0.5, +49 initial implementation, +24 operational review, +18 final verification net of test-file updates). Zero frozen files modified throughout all three passes.

---

### Operational adversarial review findings (second pass, focused exclusively on production failure modes)

Four real gaps found and fixed, confirmed by adversarial test before any fix was written:

- **Order-duplication risk on crash.** The funding state advanced to IDLE automatically the moment orders were computed, before any caller had confirmed those orders were actually submitted downstream. A crash between "orders computed" and "caller successfully hands them to Module 28" would leave state at IDLE with nothing actually invested -- silently losing that month's investment with no further checks performed. Fixed by splitting completion into two explicit phases: `run_daily_cycle()` now leaves state at EXECUTING when orders are produced, and a new `confirm_cycle_outcome()` method -- called only after the caller confirms real submission -- is what advances to IDLE. Retrying `run_daily_cycle()` after a crash (state still EXECUTING) recomputes the identical proposal rather than a different one, since `_build_buy_orders` is a pure function of its inputs. `CycleResult.cycle_id` was added as a stable idempotency key for the downstream submission step.
- **Reminder-duplication risk on crash.** The Telegram reminder was sent before the single end-of-cycle state save -- a crash between send and save would revert `reminder_sent_this_month` to its prior value on restart, causing a resend. Fixed by checkpointing state immediately after the funding-check decision (including the reminder flag), before any order-building work begins -- shrinking the crash window from "the whole rest of the cycle" to effectively nothing.
- **Proposed quantity did not reserve room for real transaction costs.** `int(budget / price)` could propose spending up to 100% of available cash on the gross purchase alone, leaving nothing for the brokerage/STT/stamp duty/GST that are always due on top -- confirmed with a real example (Rs.50,000 budget, proposal left short by Rs.33.63 once real costs were added). Every proposal was systematically oversized in the same direction. Fixed with `_affordable_quantity()`, which uses `CostTaxEngine` (not a guessed buffer) to find the largest quantity whose gross cost plus real fees actually fits the budget.
- **No trading-day awareness at all.** `run_daily_cycle()` had no way to defer order generation on an exchange holiday or non-trading day. Added an `is_trading_day` parameter (Strategy Engine doesn't own the exchange calendar itself, per dependency inversion -- the caller supplies this) -- when `False`, order generation is deferred with the funding state left at EXECUTING so the next trading day's invocation retries against the same confirmed pool.

### Verified with real adversarial tests, no gap found

- **Kite API failure exception-safety was already correct**, confirmed rather than assumed: network timeout, auth expiry, rate limiting, and server-error exceptions all propagate cleanly (never swallowed) and never leave partially-written state, since `state_store.save()` is only ever reached after a step fully succeeds.
- **Liquidity protection**: confirmed by source inspection that `OrderType.MARKET` does not appear anywhere in `strategy.py` -- structurally impossible for this module to construct a market order, not merely avoided by convention.
- **Execution independence**: confirmed `strategy_engine` has zero coupling to `Fill`/`Trade`/execution-status types -- partial-fill handling belongs entirely to Module 28 / the Live Trading Engine, reusing Phase 4's already-adversarially-tested `FillSimulator`, not reimplemented here.
- **Same-day and cross-day idempotency**: confirmed a crash-and-retry (same day or next day) before confirmation never produces duplicate orders, and after confirmation correctly produces zero further orders.

### Regression tests added this pass

24 new tests (`test_strategy_engine_operational_adversarial.py`) covering all ten review areas, plus the existing regression baseline updated to reflect the corrected (safer) cost-aware quantities and the new two-phase completion flow.

### Test count

**478 total** (405 through v0.5, +49 initial Phase 6 implementation, +24 this operational review). Zero frozen files modified throughout (verified via `git diff v0.5 --stat` after every change in this pass, not just once at the end).

### Added (from initial Phase 6 implementation)

- `strategy_engine` package: `StrategyEngine` (implements Phase 4's frozen `Strategy` interface for backtesting, plus `run_daily_cycle()` for live/paper), `AvailableInvestmentPool` (implemented for the first time -- specified in PHASE1_Architecture_SRS.md section 15 but never needed in code until now), the Monthly Funding Policy state machine (`RecurringMonthlyPolicy`, `LumpSumPolicy`), buy-only priority ordering (`priority.py`), limit-order pricing policy (`limit_pricing.py`), the abstract port layer (`CashLedgerPort`, `NotificationPort`, `OperationalEventPort`, `MarketIntelligencePort` + `NullMarketIntelligencePort`), and `StrategyStateStore` (SQLite WAL+lock persistence for the state machine, reusing the exact pattern from every prior phase's registries).
- `docs/PHASE6_Production_Readiness_Report.md`.

### Findings from adversarial testing performed this pass

- A real documentation gap, not a code defect: `CycleResult.orders` are proposals only, never verified against Module 28's future `verify_and_finalize()` veto. Nothing said this loudly enough. Fixed by strengthening the docstring and adding a permanent regression test confirming Strategy Engine never calls `verify_and_finalize()` on its own.
- Idempotency, concurrent state-store access (10 threads x 20 cycles, zero errors), the structural sell-guard (confirmed by source inspection, not just behavior), negative-balance rejection, and the "insufficient capital stays AWAITING_FUNDS rather than IDLE" edge case were all deliberately attacked and held.

### Deliberate, disclosed design decisions

- Buy-only diff logic in `priority.py` is a deliberate duplication of Phase 5's private `_buy_only_diff` -- reusing it would mean either a fragile private cross-package import or modifying frozen code with no defect justifying it. Disclosed in the module docstring, not silently duplicated.
- `generate_orders()` (backtesting) and `run_daily_cycle()` (live/paper) share one core allocation method (`_build_buy_orders`) -- the same code validated in backtest is what would run live, verified by literally running `StrategyEngine` through Phase 4's actual frozen `BacktestEngine`.
- Market Intelligence absence is a verified guarantee: the test suite asserts identical `OrderIntent` output whether `MarketIntelligencePort` is null or populated, not just a stated intention.

### Known limitations / Production Risks Remaining

Limit-price buffer (0.3%) is a provisional disclosed parameter. All four ports are abstract contracts only -- no real Module 13/26/27/28 implementation exists yet, by design. Real Kite balance-query and Telegram delivery behavior are unverified (no network access in this build environment). See the production-readiness report's "Production Risks Remaining" section for the full, separated list of operational vs. software concerns.

---

## [0.5.0] - Phase 5 frozen: Portfolio Optimizer, Risk Management Engine

**Frozen.** Implementation complete, adversarially reviewed, four real defects found and fixed (see below). 405 tests passing. See RELEASE_POLICY.md for the release criteria this version was verified against before tagging.

### Release Record (per RELEASE_POLICY.md)

```
Version: v0.5 (Frozen)
Frozen date: 2026-07-17
Git commit: see `git rev-parse v0.5` (tag applied to the commit containing this record)
Test suite: 405 tests, all passing
Regression tests: every defect found during implementation or adversarial review across Phases 2-5
                  has a dedicated regression test -- see each phase's CHANGELOG entry and
                  production-readiness report for the itemized list; not restated as a single
                  aggregate number here to avoid a claim this document can't re-verify precisely
                  against every prior phase's exact wording
Critical/High issues open: 0
Frozen interfaces from prior phases modified: 0 (verified via `git diff v0.4 --stat` against every
                                                   package frozen at v0.4 -- empty result)
Documentation consistency: verified (PHASE5_Objectives.md, PHASE5_Production_Readiness_Report.md,
                                      CHANGELOG.md, and PHASE1_Architecture_SRS.md §15/§16
                                      cross-checked for matching test counts, module references,
                                      and status language)
```

### Adversarial review findings (aggressive pass, post-implementation)

A dedicated "try to break it" review across architecture, risk, edge cases, execution safety, performance, reliability, and security. Four real defects found and fixed, all in code paths that matter for capital protection:

- **Manual-selling guard missed common word inflections.** The trigger-word list only matched exact "sell"/"liquidate" — a completely natural sentence like "Consider selling this position" or "The position was sold without authorization" passed through undetected. Fixed by adding "selling," "sold," "sells," "liquidating," "liquidated," "liquidates" to the trigger set.
- **Manual-selling guard's negation window was too wide, allowing an unrelated negation word to shield an actual sell instruction.** "no no no you should actually sell this" passed through because a "no" fell within the 4-word lookback, even though it had nothing to do with the actual instruction. Narrowed the window from 4 to 2 words — every legitimate disclosure phrase this platform actually generates places its negation word immediately adjacent to the trigger, so this costs nothing in false positives while closing the gap. Verified against both the original attack sentence and all existing legitimate disclosure phrasings.
- **Portfolio Optimizer had no defense against corrupted price data.** A zero-price or low > high bar that somehow bypassed the Data Quality Validator was silently tolerated — the resulting non-finite return was just dropped by the volatility calculation with no signal anything was wrong. Phase 4's `BacktestEngine` already guards against exactly this (`_validate_bars_sanity`, added during its own adversarial review); Portfolio Optimizer had no equivalent. Fixed by excluding the affected symbol with a clear reason (not raising for the whole batch, since one bad symbol in a multi-symbol universe shouldn't invalidate the rest).
- **`drift_tolerance_pct` was a bare, unvalidated constructor parameter.** A negative value produced false-positive drift alerts with zero actual drift; a value above 1.0 silently disabled drift detection entirely (no real-world drift could ever exceed a >100% threshold). Fixed by moving it into `HardConstraints`, where it now goes through the same `validate()` as every other numeric constraint — this also corrects an architectural inconsistency (F8 requires constraints to be "structured, versioned config, not scattered magic numbers," and this parameter had been living outside that structure).

### Extensively probed, no defect found (real adversarial tests run, not just reasoning)

Degenerate universe sizes (single ETF, duplicate candidates, all-excluded universes); constraint boundary values (zero, negative, NaN, Inf, exactly-equal per-ETF/per-asset-class caps); extremely tight caps forcing heavy cash reserve; extreme volatility (200% daily swings); perfectly correlated ETFs; 200-ETF and 500-ETF universe scaling (0.02s and 0.05s respectively, both with tight caps forcing many capping iterations); 300-ETF x 750-day memory usage (62MB delta — trivial even outside the research-side placement that means this never runs on the EC2 micro anyway); real concurrent-write stress test against `RiskEventRegistry` (8 threads x 15 events, zero errors, zero lost writes); SQL injection safety (parameterized queries throughout, verified by source scan); no unsafe `eval`/`exec`/bare-`except` patterns; negative/out-of-range weights and drawdown values passed directly to `RiskManagementEngine.evaluate()`/`check_drawdown_constraint()` (both degrade gracefully, and neither path can touch money regardless); hidden coupling (zero cross-package private-attribute access found by source scan; `risk_management` confirmed to have zero code dependency on `portfolio_optimizer`, one-way as designed).

### Disclosed, non-blocking hardening opportunity (not fixed, not a currently-exploitable bug)

The manual-selling guard validates `RiskEvent.recommended_action` only, not `description`. Today this is safe — every call site in `engine.py` uses `description` exclusively for factual/diagnostic text ("X is 130% of the portfolio") and `recommended_action` exclusively for the actionable text, verified by reading every construction site. But if a future change accidentally swapped which field carried actionable text, `description` would not be guarded. Not fixed now because doing so risks false positives on legitimate factual text that happens to mention market "selling" (e.g. "dropped after broad market selling pressure") — a real design trade-off, not an oversight, and worth flagging for whoever touches this code next rather than silently deciding it either way.

### Regression tests added this pass

17 new tests (`test_phase5_adversarial_review.py`) locking in all four fixes plus the concurrency stress test. **405 tests total, all passing.**

### Added (implementation, from earlier in this release)

- `risk_management` package: `RiskManagementEngine`, `HardConstraints`/`SoftPreferences` (explicit hard/soft constraint split), `RiskEvent`/`RiskEventType`/`Severity`, `RiskEventRegistry` (first real implementation of the `risk_events` table sketched but never built in Phase 1 section 6). Structural, negation-aware enforcement of the manual-selling rule at `RiskEvent` construction time.
- `portfolio_optimizer` package: `PortfolioOptimizer`, pluggable `AllocationMethod` interface + registry (`InverseVolatilityMethod` as the only registered implementation, per the approved default), the hard-constraint capping ("water-filling") algorithm, `build_proposal()` producing the full Approval-Console-ready proposal artifact.
- `docs/PHASE5_Objectives.md`, `docs/PHASE5_Production_Readiness_Report.md`.

### Fixed (found via adversarial-style testing during normal development, not the separate review still to come)

- **Asset-class cap could be silently violated when interacting with the per-ETF cap.** A symbol locked at its per-ETF maximum was excluded from further asset-class-driven scale-down, on the incorrect assumption that "locked" meant "cannot be touched again" rather than "cannot be pushed higher." Three ETFs each within a 15% per-ETF cap could sum to 45% in one asset class against a 35% cap with nothing catching it. Fixed: asset-class scaling now applies to all members of an over-cap class regardless of per-ETF-lock status; the final defensive clamp (the last line of defense) now checks both constraint types, having previously only checked the per-ETF one.

### Verified, not assumed

- Zero frozen Phase 1-4 files modified (`git diff v0.4 --stat` against every frozen package: empty).
- Zero new dependencies (both new packages import stdlib only, directly).
- Capital-agnostic requirement verified structurally (AST scan for hardcoded amounts) and behaviorally (proportional-scaling proof across seven capital levels from Rs.1,000 to Rs.5,00,000).
- Performance target exceeded by ~700x (0.0073s vs. a 5s target for a 40-ETF universe).

### Known limitations (see production-readiness report for full detail)

Drift tolerance and hard-constraint defaults are disclosed provisional placeholders, not researched-optimal values. Proposal builder's comparative backtest and confidence score are intentionally simplified relative to Phase 3's full statistical methodology. F11's "present both options" rule detects and flags the materially-worse-drawdown case but does not auto-solve the alternative. Only inverse-volatility is implemented; Risk Parity/Minimum Variance/Black-Litterman/HRP remain registry slots — exactly as scoped, not a gap.

---

## [0.4.0] - Phase 4 frozen: Backtesting Engine, adversarial-reviewed

### Adversarial review findings (this release)

A full adversarial review was performed against 24 specific risk categories before freezing this version, per an explicit audit request. Nine real weaknesses were found by deliberately trying to break the implementation (not by re-reading code), each with a fix, a regression test, and documentation. None were catastrophic, but several would have produced silently wrong or silently incomplete results if left unfixed, which is worse than a crash.

**Fixed:**

- **Stale price silently carried forward with no ongoing warning** - a held position whose price data stopped updating (data gap or delisting) was valued at the last known price forever after the first occurrence, with no recurring signal anything was wrong. Now: `BacktestResult.warnings` records an explicit warning the first time staleness crosses `stale_price_warning_days` (default 5), and again every ~30 days it persists. (engine.py, `_check_stale_prices`)
- **Silent early termination with no warning** - if data for every symbol ran out before `config.end_date`, the backtest silently ended early with no error, no warning, and no way for a caller to know the requested period wasn't fully covered. Now: `BacktestResult.actual_end_date` and an explicit warning surface this. (engine.py)
- **Fractional ETF order quantities accepted** - `OrderIntent` accepted quantities like 3.7 units, which aren't tradable on NSE (ETFs trade in whole units only). Now rejected at construction with `InvalidOrderError`. (models.py)
- **No dividend handling at all** - dividend income was completely unmodeled, understating returns for any dividend-paying ETF. Now: `BacktestEngine.run()` accepts `corporate_actions_by_symbol`; DIVIDEND events credit cash based on quantity held on the ex-date, recorded as `DividendReceipt` with a human-readable explanation. (portfolio.py, engine.py, models.py)
- **No corporate action (split/bonus) handling at all, with a latent tax-correctness risk** - splits/bonus issues were unmodeled. Implemented `CostTaxEngine.apply_split()` and `Portfolio.apply_split()`, with the critical correctness property verified by a dedicated test: the original tax-lot acquisition date is preserved exactly - a split must never reset the STCG/LTCG holding-period clock under Indian tax law. Getting this wrong would have silently misclassified capital gains on every post-split sale. (cost_tax_engine.py, portfolio.py, engine.py)
- **No partial-fill / volume-participation modeling** - every order filled in full regardless of the execution bar's actual traded volume, unrealistic for large orders relative to a thin market. Added opt-in `BacktestConfig.max_volume_participation_pct`; when set, a fill is capped at that fraction of the bar's volume and the remainder is re-queued for subsequent days. Opt-in, defaults to `None` (unlimited, prior behavior) - the locked regression baseline from the original Phase 4 delivery is unaffected unless a caller explicitly enables this. (fill_simulator.py, engine.py)
- **Two bugs found while testing the partial-fill fix above, in the same review pass:** (a) the rationale string grew by one appended phrase per partial fill, unboundedly, across many days of continuation; (b) `expiry_date` was silently ignored for partial-fill continuations, so an order could keep trickling in indefinitely regardless of its configured expiry. Both fixed: rationale text is now reused unchanged across continuations, and expiry is checked before every re-queue. (engine.py)
- **No defense against obviously invalid price data reaching the engine** - a caller who bypassed the Data Quality Validator (Phase 2) could feed negative prices or `low > high` bars directly into `BacktestEngine.run()`, producing nonsensical cost/tax/equity numbers with no error anywhere. Added a lightweight sanity gate at the start of `run()` - not a replacement for full validation, just a guard against the specific case of unvalidated data slipping through. (engine.py, `_validate_bars_sanity`)
- **No guaranteed failure-recovery path for `backtest_runs`** - if `engine.run()` raised partway through, a run registered via `BacktestRunRegistry.register_run_start()` could be left permanently stuck in `'running'` state unless the caller remembered to wrap it in try/except. Added `run_and_register()`, which guarantees the run is finalized (success or failure, with the error recorded) regardless of what `engine.run()` does. (registry.py)

**Fixed floating-point/numerical/performance issue found during this same pass:**

- `_build_history_view` re-sliced each symbol's entire observed bar history on every single day of a backtest (O(elapsed_days) per call), unnecessary CPU cost for long backtests. Replaced with `bisect`-based windowing (O(log n + window_size) per call). Verified with a 10-year, 6-symbol, 586-trade backtest completing in 0.09 seconds.

**Verified (no bug found, confirmed via adversarial test and locked in with a regression test):**

- Numerical stability of FIFO tax-lot matching under repeated fractional-quantity partial sells (no floating-point "dust" residue).
- XIRR correctness at extreme boundaries: 30-year and 50-year holding periods, a 99% loss scenario, and a 100% total loss (correctly returns `None` rather than asserting a definitionally ill-posed exact -100% root).
- Multi-order sequencing: a SELL decided immediately after a BUY (same underlying symbol, dependent on the BUY having already filled) correctly sees post-fill portfolio state, because fills are always processed before the Strategy is called each day.
- Thread safety model explicitly documented: `BacktestEngine`/`Portfolio`/`CostTaxEngine` are single-threaded by design (no locking, not needed for a sequential simulation); `BacktestRunRegistry` reuses Phase 2's WAL+lock pattern and is safe for concurrent use.

### Also fixed (found during initial Phase 4 self-review, prior to this adversarial pass - restated here for a complete record)

- Floating-point false-positive in the Sharpe/Sortino "zero volatility" guard (`== 0` replaced with `< 1e-10`, since binary floating-point rarely produces an exact zero variance even for a truly constant return series).
- A test asserting an unverified "commonly cited" XIRR example was corrected to self-verify against IRR's actual defining property (NPV close to 0 at the computed rate) rather than trusting an unconfirmed external figure.

### Added

- `BacktestResult.warnings`, `.actual_end_date`, `.dividend_receipts`, `.corporate_action_events` fields.
- `DividendReceipt` and `CorporateActionEvent` models, each with a human-readable `.explanation` property (consistent with the trade-explanation requirement).
- `CostTaxEngine.apply_split()`.
- `Portfolio.apply_dividend()`, `Portfolio.apply_split()`.
- `BacktestConfig.max_volume_participation_pct`, `.stale_price_warning_days`.
- `run_and_register()` convenience function in `backtesting/registry.py`.
- `BacktestEngine.config` public property.
- 44 new regression tests (`tests/unit/test_phase4_adversarial_review.py` and additions to `test_backtest_registry.py`) covering every finding above.

### Test suite

327 tests total, all passing (was 308 before this review).

### Not changed

No architecture changes. Every module built or modified in this release was already approved in the frozen 26-module inventory (`PHASE1_Architecture_SRS.md`). The no-look-ahead guarantee, cost/tax rate sourcing, and reproducibility mechanism from the original Phase 4 delivery are unchanged and remain correct - this release adds missing functionality and closes silent-failure gaps, it does not alter the core simulation logic that was already verified.

---

## [0.3.0] - Phase 3: ETF Universe Optimizer

- `ETFMetadataManager`, `UniverseScreeningEngine`, `ETFUniverseOptimizer` (8-dimension explainable scoring), `PortfolioCandidateGenerator` (block-bootstrap statistically validated replacement recommendations only).
- 71 new tests (205 total).

## [0.2.0] - Phase 2: Historical Data Engine, Data Quality Validator, Configuration Manager, Secrets Manager

- Provider abstraction (NSE primary, Kite secondary), rate limiting with exponential backoff, SQLite WAL-mode snapshot registry, Fernet-encrypted local secrets + AWS Secrets Manager provider, three-severity data quality validation pipeline.
- Post-approval production-readiness review found and fixed: SQLite thread-safety default, two resource leaks (unclosed HTTP sessions, unclosed log file handlers), missing retry/backoff logic.
- 134 tests.

## [0.1.0] - Phase 1: Architecture & SRS

- Full software requirements specification, 26-module architecture (17 originally specified + 9 recommended/approved additions including the Approval Console and Autonomous Operations & Self-Healing Framework), AWS split architecture (micro-anchored live instance + on-demand research compute), database schema, development roadmap.
