# Phase 6 - Production-Readiness Report

**Scope:** Strategy Engine (Module 4 per the original 17-module list) - implementation of the fully-approved PHASE6_Objectives.md design, including the Monthly Funding Policy state machine, buy-only priority allocation, limit-order pricing, Pause/Resume/Discontinue handling, and the abstract port layer for Modules 13/26/27/28.

**Status: FROZEN as v0.6.** Three adversarial/verification passes completed: initial delivery, operational adversarial review, and a final production verification against ten specific criteria. 491 tests passing (86 new across all three passes). See "Final Production Verification" section below for the item-by-item result, and RELEASE_POLICY.md for the release criteria this version was verified against before tagging.

## What was verified against the actual frozen source, not assumed

- **Zero frozen files modified.** `git diff v0.5 --stat` against every package frozen through Phases 1-5 (including `portfolio_optimizer` and `risk_management`, frozen at v0.5) returns empty.
- **Zero new dependencies.** `strategy_engine` imports only the Python standard library, directly. This matters more here than in any prior phase - Strategy Engine is the first Phase 6+ component intended to actually run on the EC2 micro instance (PHASE6_Objectives.md section 0.4/section 15), not the research-side instance.
- **Runs through Phase 4's actual frozen `BacktestEngine`, unmodified.** `StrategyEngine.generate_orders()` satisfies the exact `Strategy` ABC interface from `backtesting/strategy.py` - verified by literally instantiating `BacktestEngine` with a `StrategyEngine` and running a full backtest, not just checking method signatures match.
- **`AvailableInvestmentPool` is implemented for the first time.** It was specified in PHASE1_Architecture_SRS.md section 15 but no prior phase needed a concrete capital amount - Portfolio Optimizer and Risk Management Engine both correctly stayed weight-only. Phase 6 is genuinely the first phase that needed this class to exist in code.

## The Monthly Funding Policy state machine, verified end-to-end

Every transition in PHASE6_Objectives.md section 3.2 has a dedicated test, not just the happy path: zero orders while `AWAITING_FUNDS`, immediate execution the moment funds are detected, exactly one reminder at day 8 (not zero, not repeated), the reminder framed explicitly as "not a cancellation," month-rollover correctly resetting both funding state and the reminder flag independently, `IDLE` performing zero further checks, Pause/Discontinue short-circuiting the funding check entirely, and - the one genuinely subtle design decision in this phase - insufficient capital (can't fund even the highest-priority opportunity) correctly staying in `AWAITING_FUNDS` rather than being marked "complete," since the money was never actually deployed.

## Findings from adversarial testing performed this pass

Per this phase's explicit instruction (comprehensive adversarial tests as part of the delivery, not a deferred separate step), I tried to break this before calling it done:

- **Idempotency confirmed:** invoking `run_daily_cycle()` twice on the same day produces zero duplicate orders on the second call - the state machine's own `IDLE` transition after a successful cycle makes this safe by construction, not by an explicit "already ran today" guard, which is arguably more robust (nothing to forget to check).
- **Concurrent `StrategyStateStore` access verified under real load** (10 threads x 20 read/write cycles) - zero errors, same WAL+lock pattern proven safe in every prior phase's registry.
- **Structural sell-guard confirmed by direct code inspection, not just behavioral testing** - `Side.SELL` does not appear anywhere in `_build_buy_orders`'s source; `Side.BUY` is the only literal ever passed to `OrderIntent`'s constructor in that method.
- **A real documentation gap found and fixed, not a code defect:** `CycleResult.orders` are proposals only - they have not passed Module 28's `verify_and_finalize()` (the final-authority veto from PHASE1_Architecture_SRS.md section 0.1a). Nothing prevented a future caller from mistaking `CycleResult.orders` for execution-ready orders, since Strategy Engine itself never calls `verify_and_finalize()` (correctly - that's the downstream orchestrator's job) and nothing said so loudly enough. Fixed by strengthening `CycleResult`'s docstring explicitly, and added a permanent regression test confirming Strategy Engine never calls `verify_and_finalize()` on its own - a structural guarantee, not just a comment.
- **Negative Kite balance rejected at construction** (`AvailableInvestmentPool.__post_init__`), extreme reminder-day boundary values validated, zero-price bars mid-history produce zero orders rather than garbage, leap-year February reminder-day edge case behaves correctly.
- **Tiny capital insufficient for even one unit correctly stays `AWAITING_FUNDS`**, not `IDLE` - confirmed the subtle "was anything actually deployed" distinction (PHASE6_Objectives.md section 16's edge case) behaves as designed under a real adversarial scenario (a very expensive ETF against a very small transfer).

## Deliberate design decisions worth restating here

- **Buy-only diff logic is deliberately duplicated from Phase 5**, not imported. Phase 5's `_buy_only_diff` is a private function inside a frozen package; reusing it would require either a fragile private-attribute cross-package import or modifying frozen code with no defect justifying it. A small, simple, heavily-tested function was reimplemented instead - disclosed explicitly in `priority.py`'s module docstring, not silently duplicated.
- **Two entry points, one shared core.** `generate_orders()` (backtesting) and `run_daily_cycle()` (live/paper) both funnel into `_build_buy_orders()` - this is what makes Phase 1 section 4's "what's validated in backtest is what actually runs live" literally true for this module, not just a stated goal.
- **Market Intelligence absence is a verified guarantee, not a stated intention.** The test suite runs the same scenario against `NullMarketIntelligencePort` and a populated fake, and asserts identical `OrderIntent` output (symbol, quantity, side, limit price) - the only thing permitted to differ is advisory text in the rationale.

## Known limitations (disclosed)

1. **Limit price buffer (0.3%) is a provisional, disclosed parameter**, same honesty standard as Phase 4's slippage assumption and Phase 5's drift tolerance - needs real paper-trading fill data before being trusted as tuned.
2. **`generate_orders()`'s backtesting path uses a static `target_weights` dict** supplied at construction, not a live re-optimization loop calling Portfolio Optimizer mid-backtest. This was a deliberate scoping choice (PHASE6_Objectives.md section 6) - orchestrating fresh weight computation on a schedule is a Phase 9 Scheduler concern, not Strategy Engine's constructor's.
3. **Ports (`CashLedgerPort`, `NotificationPort`, `OperationalEventPort`, `MarketIntelligencePort`) are abstract contracts only** - no real implementation exists yet for any of them, by design, per the approved sequencing (Module 28: Phase 10/12; Module 13: not yet scheduled; Module 26: sequencing still open at PHASE1_Architecture_SRS.md section 14.6; Module 27: separate later phase). This phase's job was the contract, not the implementation.
4. **`verify_and_finalize()`'s real semantics (may only reduce, never increase risk) are enforced by nothing in Phase 6's own code** - they can't be, since no real implementation exists to enforce them against yet. This is recorded as a binding requirement on Module 28's future implementation (PHASE1_Architecture_SRS.md section 0.1a), not something Phase 6 can verify today.

## Production Risks Remaining (operational, not software defects - same distinction established at Phase 5's freeze)

- Real Kite balance-query behavior (rate limits, field semantics for available-vs-margin cash) is unverified against the live API - this platform still has no network access to NSE/Kite in this build environment.
- The reminder message's actual Telegram delivery behavior is unverified - `NotificationPort` has no real implementation yet.
- Whether the 8th-EOD reminder milestone should ever escalate beyond one message (PHASE6_Objectives.md section 3.5) remains an open judgment call, deliberately not decided unilaterally.

## Test coverage (49 new tests, 454 total)

| Area | Tests |
|---|---|
| Buy-only diff and priority ordering | 10 |
| Execution policy state machine (Recurring Monthly + Lump Sum) | 16 |
| StrategyEngine core (backtest compatibility, structural sell guard) | 9 |
| State store, capital-agnostic verification, command handling, Market Intelligence absence guarantee | 11 |
| Regression baseline (including verify_and_finalize non-invocation) | 3 |

## Recommendation

Ready for your review. Per your instruction, no tag or freeze applied - this delivery awaits your explicit approval, consistent with RELEASE_POLICY.md's criteria and the same pattern followed at every prior phase.

---

## Operational Adversarial Review (second pass, focused exclusively on production failure modes)

You asked me not to assume correctness and to try to break this specifically around EC2 restart recovery, idempotency, Kite API failures, exchange holidays, final cash validation, liquidity protection, partial fills, duplicate reminders, and state persistence. Here is the honest account, area by area.

### 1. EC2 restart recovery -- real gap found and fixed

Confirmed by test before fixing: a crash between "Strategy Engine computed proposed orders" and "the caller successfully submitted them downstream" would leave `funding_state` at IDLE with nothing actually invested, since the state transition to IDLE happened automatically the moment orders existed in memory -- not when they were confirmed acted upon. **Fixed** by splitting completion into two phases. `run_daily_cycle()` now leaves state at `EXECUTING` whenever it produces orders; a new `confirm_cycle_outcome()` method, called only after the caller confirms real submission, is the sole path to `IDLE`. A restart with state still `EXECUTING` safely recomputes the identical proposal (verified by test) rather than risking a silently-skipped month.

### 2. Idempotency -- verified, and one gap closed as a side effect of the fix above

Same-day and cross-day retries before confirmation now provably produce identical proposals (`cycle_id` added as a stable idempotency key for whatever downstream system submits to Module 28). After confirmation, retries correctly produce zero further orders. Verified by direct test, not inferred from the design.

### 3. Kite API failures -- verified already correct, not assumed

Simulated `TimeoutError`, `PermissionError` (token expiry), `ConnectionError` (rate limiting), and `RuntimeError` (server error) all propagate cleanly through `run_daily_cycle()` without being swallowed, and none leave partially-written state -- confirmed by checking `state_store.load()` returns exactly what it did before the failed call. This was already correct by construction (state is only ever saved after a step fully completes) and I verified it rather than taking that construction argument on faith.

### 4. Exchange holidays and non-trading days -- real gap found and fixed

There was no way to tell `run_daily_cycle()` that a given date wasn't a trading day at all. **Fixed** with an `is_trading_day` parameter -- Strategy Engine doesn't own the exchange calendar (that's external data, supplied by the caller per the dependency-inversion pattern used throughout this phase), but it now correctly defers order generation when told today isn't tradable, leaving the funding state at `EXECUTING` so the next trading day's invocation retries against the same already-confirmed pool rather than re-running the funding check from scratch.

### 5. Final cash validation -- real gap found and fixed

Confirmed with a concrete number before fixing: a Rs.50,000 budget produced a proposal that, once real brokerage/STT/stamp duty/GST were added on top, actually needed Rs.50,033.63 -- short by Rs.33.63. The naive `int(budget / price)` sizing left no room for costs that are always due. **Fixed** with `_affordable_quantity()`, using `CostTaxEngine` (the same cost engine already validated in Phase 4) rather than a guessed buffer, so the proposal itself is approximately correct before Module 28 ever sees it -- Module 28's final authoritative check (per Decision 1) still applies on top, but it should now rarely need to reduce quantity for cost reasons alone.

### 6. Liquidity protection -- verified, structurally guaranteed

Confirmed by reading the actual source, not just running tests against it: `OrderType.MARKET` does not appear anywhere in `strategy.py`. There is no code path, including the new deferred/retry logic added this pass, that can produce anything but a `LIMIT` order. Real liquidity signals (no suitable sellers, wide bid-ask spread) require order-book depth data Strategy Engine doesn't have access to and was never meant to -- that's correctly Module 28/Live Trading Engine's concern, reusing Phase 4's already-adversarially-tested `FillSimulator`.

### 7. Partial fills -- verified, Strategy Engine remains execution-independent

Confirmed `strategy_engine` imports nothing from `Fill`/`Trade`/execution-status types, and `CycleResult` carries no fill-price, filled-quantity, execution-status, or broker-order-id field. Partial-fill handling is entirely Module 28's and the Live Trading Engine's responsibility, using Phase 4's frozen `FillSimulator` -- not reimplemented here, and structurally impossible to accidentally couple to given this phase's own design.

### 8. Duplicate reminder prevention -- real gap found and fixed

The same class of gap as finding 1: the reminder was sent before the single end-of-cycle save, so a crash in that window would cause a resend on restart. **Fixed** by the same two-checkpoint save that fixed finding 1 -- state (including `reminder_sent_this_month`) is now persisted immediately after the funding-check decision, before any order-building work, shrinking the crash window to effectively nothing. Verified with a repeated-invocation test (3 retries on the reminder day) confirming exactly one message sent regardless.

### 9. State persistence -- verified end-to-end across a full simulated month

A single test now exercises the entire lifecycle across four separate `StrategyStateStore` instances (simulating four restarts): fresh start on the 1st, reminder on the 8th, funds detected and orders produced on the 15th, confirmation advancing to `IDLE` -- each step reloaded from a fresh store instance pointed at the same file, not held in memory across the test.

### 10. No frozen phase modified

`git diff v0.5 --stat` against every package frozen through Phase 5 (including `portfolio_optimizer` and `risk_management`) returns empty, checked again after all fixes in this pass, not just once before starting.

## Final Production Readiness Assessment

Four real, meaningful gaps were found in this pass by actually trying to break the implementation against realistic operational failure modes -- not by re-reading code and confirming it looked fine. All four are fixed, each with a regression test that fails without the fix and passes with it (verified by running the test against the pre-fix code path for the crash-window findings specifically). Six additional review areas were verified rather than assumed and found already correct. I believe this phase is now sound against the specific operational failure modes you asked me to attack. I have not found a way to produce a duplicate order or a duplicate reminder through any restart, retry, or failure sequence I could construct.

**Updated test count: 478 total (73 new across both adversarial passes).**

---

## Final Production Verification (third pass, against ten specific production criteria)

Two more real gaps were found in this pass, on top of the four from the operational review -- verifying against your ten criteria surfaced things the operational review's framing hadn't specifically targeted.

### 1. No duplicate order after EC2 restart / process crash / power failure / retry / network interruption

**Restart, crash, retry, network interruption:** covered by the two-phase completion fix from the operational review, re-verified here. **Power failure specifically -- a real gap found and fixed:** `StrategyStateStore` never set `PRAGMA synchronous`, inheriting WAL mode's default of `NORMAL`. `NORMAL` protects against database *corruption* but does not guarantee the most recently committed transaction survives an actual power loss (as distinct from a process crash) -- a power cut immediately after a commit could, in rare cases, revert to the prior state if the OS hadn't flushed to physical disk yet. **Fixed** by setting `PRAGMA synchronous=FULL` in `StrategyStateStore` specifically (not a change to the frozen `common/db.py`, which every other registry also depends on) -- scoped to this store because its writes are low-frequency and gate real investment decisions, where the fsync cost is negligible and the durability guarantee matters more than it does for a higher-frequency registry. Verified by test that the PRAGMA is actually set, not just documented as intended.

### 2. No monthly investment can be silently skipped

Covered by the same two-phase completion design -- a month only ever reaches `IDLE` via an explicit `confirm_cycle_outcome()` call. A cycle that never gets confirmed stays at `EXECUTING` indefinitely rather than silently completing -- "stuck" rather than "skipped." Detecting a cycle that's been stuck too long is legitimately Module 26's future job (operational monitoring), not something Phase 6 needs to solve now; I'm noting the distinction explicitly rather than either overclaiming this phase prevents staleness or leaving the boundary unstated.

### 3. No monthly reminder can be duplicated

Verified two ways this pass: behaviorally (repeated invocations on the reminder day send exactly one message, already covered) and now **structurally**, by recording the actual sequence of `save()` vs `send()` calls across a real `run_daily_cycle()` invocation and asserting persistence occurs first in the literal call order -- not inferred from reading the code.

### 4. Order quantity always reserves sufficient funds for brokerage, STT, GST, stamp duty, exchange charges, SEBI charges, and any mandatory statutory charge

Verified against `CostBreakdown`'s actual seven fields (`brokerage`, `stt`, `stamp_duty`, `exchange_txn_charge`, `sebi_turnover_fee`, `gst`, `slippage_cost`) -- confirmed `_affordable_quantity()` reserves against the full breakdown via `total_cost`, not a subset, and confirmed by test that all seven fields exist and are actually summed, not just that some total is nonzero.

### 5. Quantity is always a whole ETF unit

Verified with explicit tests at multiple non-round price and budget combinations (e.g. price=33.33, budget=50000.99) -- `_affordable_quantity()` always returns a Python `int`, and every generated `OrderIntent` carries an integer quantity end to end.

### 6. No Market Order exists anywhere in production code

Re-confirmed by source inspection (already verified in the operational review): `OrderType.MARKET` does not appear in `strategy.py`. Scope clarification: Phase 4's general-purpose `FillSimulator` (frozen, backtesting infrastructure) supports market orders as a platform capability -- that's expected and correct, since Phase 4 isn't specific to Strategy Engine's policy. What matters, and what's verified, is that **Strategy Engine's own decision logic** never constructs one.

### 7. Strategy Engine remains completely independent of Kite API, Module 27, Module 28, the exchange calendar implementation, and the execution layer

Verified by source-scanning every file in the package for any reference to a Kite library, confirming `ports.py` contains no concrete implementation of any port besides the deliberate `NullMarketIntelligencePort` default, confirming no exchange-calendar library is imported anywhere, and confirming `is_trading_day` is a plain caller-supplied boolean rather than requiring any calendar implementation to construct.

### 8. Every state transition is persisted before any external side effect

**A real gap found and fixed, going beyond the operational review's fix.** The previous fix (checkpointing state immediately after the funding decision) shrank the crash window but did not eliminate an ordering violation: `notification_port.send()` was still called *inside* the policy, before the resulting state was ever returned to the caller for persistence. **Fixed properly this time**: `ExecutionPolicy.run_cycle()` no longer performs any external write itself -- it returns a `PendingSideEffects` value describing what's needed (a reminder message, an expected-contribution notification), and `strategy.py`'s `run_daily_cycle()` now persists state *first*, then performs those effects afterward. This makes the ordering a structural property of the call sequence, not a narrow window -- verified by recording the actual `save()`/`send()` call sequence during a real invocation and asserting `save` precedes `send` in the literal event log, for both the reminder and the expected-contribution notification.

### 9. Phase 6 continues to satisfy the low-memory EC2 Micro architecture

Re-verified after all fixes in this pass, including the new `CostTaxEngine` dependency for cost-aware sizing: importing `strategy_engine` still loads neither `numpy` nor `scipy` (checked in a fresh subprocess, not just the current process's already-warm import cache).

### 10. No frozen phase or frozen source file has been modified

`git diff v0.5 --stat` against every package frozen through Phase 5 (including `portfolio_optimizer` and `risk_management`) returns empty -- re-checked after every fix in this pass, including the `PRAGMA synchronous=FULL` change, which was deliberately scoped to `StrategyStateStore` specifically rather than the frozen `common/db.py` to avoid this exact risk.

## Final Production Readiness Assessment

Two more real, meaningful gaps were found in this pass on top of the four from the operational review -- the power-failure durability setting, and the reminder/notification ordering violation that the previous fix had narrowed but not eliminated. Both are fixed, both have regression tests that verify the actual mechanism (a real PRAGMA value, a real recorded call sequence) rather than the design intent. All ten criteria in your verification request now pass, checked individually with dedicated tests, not inferred from the design holding together.

**Phase 6: FROZEN as v0.6.** Tagged per RELEASE_POLICY.md after your explicit approval.

**Final test count: 491 total (86 new across all three passes: 49 initial + 24 operational review + 18 final verification, accounting for updates to existing tests within that count).**

