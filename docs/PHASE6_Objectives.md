# Phase 6 Objectives & Design Document — Strategy Engine

**Status: FROZEN as v0.6.** This design document is now historical record of what was approved and built — see docs/PHASE6_Production_Readiness_Report.md for the full verification detail across all three review passes, and CHANGELOG.md for the Release Record.
**Builds on:** frozen v0.4 (Phases 1-4), frozen v0.5 (Phase 5), and approved amendments §15 (Capital-Agnostic Design), §16 (Module 28). Follows RELEASE_POLICY.md.

---

## 0. Architectural Conflicts — Resolutions Confirmed

### 0.1 Who actually computes order quantity and limit price — RESOLVED (Decision 1)

**Confirmed:** Strategy Engine proposes quantity, price, and execution priority — for backtesting, paper trading, and proposal generation. These are **proposed values only**. Module 28 (Execution Manager), once built, always determines the **final executable** quantity and order values, using: actual available cash in the Kite account, current market prices, Investment Queue state, Risk Management constraints, and Compliance rules.

**New binding rule from Decision 1, carried forward as a hard requirement on Module 28's future implementation (Phase 10):** Module 28 may only ever adjust a proposal **downward or defer it** — reduce quantity, delay execution, or reject outright. **It may never increase risk beyond what the Approval Console already approved** — never a larger quantity, never a different symbol, never relaxed price protection. This is a one-directional veto, not a two-directional negotiation. I'm recording this explicitly in the interface contract below (§0.1a) so it's testable, not just a sentence in this document that could be quietly forgotten by the time Phase 10 is built.

**§0.1a — `CashLedgerPort` contract addition:**
```
CashLedgerPort:
    get_available_pool() -> AvailableInvestmentPool
    get_pending_queue_entries() -> list[QueueEntrySummary]
    notify_expected_contribution(amount, expected_date, source) -> None   # see §2

# Module 28's future verify_and_finalize(proposed_order) -> FinalOrder | None contract
# (implemented in Phase 10, specified here so Phase 6 designs against it correctly):
#   - FinalOrder.quantity <= proposed_order.quantity always
#   - FinalOrder.limit_price is at least as conservative as proposed_order.limit_price
#     (never worse execution-price protection than what was approved)
#   - FinalOrder.symbol == proposed_order.symbol always (never substituted)
#   - Returning None means "deferred" (per §2), not "rejected forever"
```

This confirms and closes §0.2 as well: §15.6's wording will be amended for precision (not weakened) — see §19.

### 0.2 Module 27 — RESOLVED (Decision 3, confirms §0.3's recommendation)

Execution Policy logic (Recurring Monthly, Lump-Sum, and now the funding-wait state machine in §2) stays inside Phase 6 as an internal pluggable component. Module 27 remains reserved and unused. Confirmed, no further discussion needed.

### 0.3 Dependency-inversion via ports — RESOLVED (Decision 3)

Confirmed exactly as proposed: `CashLedgerPort`, `NotificationPort`, `OperationalEventPort` remain abstract interfaces with fake implementations for Phase 6's own testing. No concrete dependency on Module 26, Module 28, or a real Telegram integration is introduced in this phase.

### 0.4 EC2 Micro placement — RESOLVED (Decision 4)

Confirmed: Strategy Engine's core logic stays dependency-light (no numpy/scipy), consumes Portfolio Optimizer's already-computed output rather than recomputing anything statistical, and is invoked on the live micro instance rather than requiring the research-side compute Phase 3-5 needed. This was already §15's design in the original proposal — Decision 4 confirms it as binding rather than a suggestion. (§0.5, below, clarifies "invoked on the live micro" more precisely as short-lived, event-triggered execution, not a continuously-running resident process — the two decisions are consistent once read together, and I've corrected this section's wording so it doesn't read as contradicting §0.5.)

### 0.5 Event-Driven Resource Optimization — NEW (recorded as PHASE1_Architecture_SRS.md §17, cross-cutting)

Your latest instruction adds a permanent principle affecting Strategy Engine directly: no continuous polling, no resident background loops, idle by default, waking only on defined events, with a persist-state/log/release-resources/return-to-idle lifecycle after each invocation. Recorded in full at §17 of the architecture document (cross-cutting across Strategy Engine, Investment Queue, Execution Manager, and the Scheduler — not Phase-6-specific, so it lives at the SRS level, not only here). The concrete implications for Strategy Engine specifically:

- **Strategy Engine is a short-lived process invoked per event, not a resident daemon.** The Scheduler (Phase 9) fires it; it does its work; it exits. This was already implied by §6's "intentionally a pure, stateless computation" design — this decision makes it explicit and binding rather than an implementation detail I could have gotten away with doing differently.
- **"Detection of new cash" is the *outcome* of the daily funding check (§3.2), not a separate listener.** Kite has no balance-change webhook this platform relies on. One clarification, not a new mechanism.
- **The funding workflow's `IDLE (until next 1st)` terminal state (§3.2) now has explicit teeth:** persist state, log, release resources, stop checking entirely until the 1st — not a lighter-weight background check, a full stop.
- **The Live Trading Engine's WebSocket connection (Phase 12) is the one deliberate exception** — it must stay connected continuously; this principle doesn't apply to it in the "process exits between events" sense. Strategy Engine is not that component, so this exception doesn't affect Phase 6's design, but I'm noting it so it isn't misapplied later.

---

## 1. Scope and Responsibilities

Strategy Engine is one of the 17 originally-specified modules (Phase 1 §1.2: *"encodes rebalancing rules, entry/exit logic, tax-aware lot selection"*). Per §0's resolution, its concrete Phase 6 job is:

1. Implement the frozen `Strategy` interface (Phase 4) — `generate_orders(as_of_date, history, portfolio) -> list[OrderIntent]` — so it is directly usable by the Backtesting Engine unchanged.
2. Decide **when** to act (monthly SIP timing before the 8th, lump-sum timing, drift/breach triggers from Risk Management Engine) — orchestration Phase 5 explicitly deferred to this phase.
3. Decide **which** buy-only opportunities to fund and in what order, given a specific `AvailableInvestmentPool` amount that may not cover every underweight position (§6).
4. Convert Portfolio Optimizer's target weights into a **proposed** quantity and limit price per order (§0.1), for the Approval Console to review and, later, for Module 28 to verify.
5. Own the Recurring-Monthly vs. Lump-Sum Execution Policy distinction (§0.2, §3, §15.3).
6. Respond to Telegram Pause/Resume/Discontinue signals (via `NotificationPort`, §10).
7. **Never generate a sell.** This is not new — it's the same binding rule from Phase 5, re-affirmed here because Strategy Engine is the first module whose entire job is generating executable orders, making it the highest-stakes place for that rule to hold.

**Explicitly not Phase 6's job:** re-screening or re-scoring ETFs (Phase 3's job), computing target weights (Phase 5's job), actually spending cash or maintaining the ledger (Module 28's job, Phase 10+), sending real Telegram messages (Module 13's job), actual process supervision (Module 26's job), placing real orders with a broker (Live Trading Engine, Phase 12), **and Module 27 (Market Intelligence Engine) itself — PHASE1_Architecture_SRS.md §18, approved as a separate, later phase.** Phase 6 defines only the interface Strategy Engine will optionally consume it through (`MarketIntelligencePort`, §21) — Module 27's actual implementation (indicator calculation, historical database, research/live compute split) is fully out of scope here. Module 27's exhaustive prohibition list (§18.0) — it may never generate buy or sell signals, trigger portfolio changes, or override Strategy Engine, Risk Management, the Approval Console, or Compliance — makes it structurally impossible for Module 27 to expand into Strategy Engine's territory even by accident, and §21.2 makes the "must function correctly if absent" requirement a testable guarantee rather than an intention.

---

## 2. Module Boundaries (precise table)

| Module | Owns | Does NOT own |
|---|---|---|
| ETF Universe Optimizer (Phase 3) | Which ETFs are eligible, their scores | Weights, orders |
| Portfolio Optimizer (Phase 5) | Target weights (percentages only) | When to act, quantities, order timing |
| Risk Management Engine (Phase 5) | Constraints, breach/drift detection, alerts | Any order or sell proposal (manual-selling rule) |
| **Strategy Engine (Phase 6, this document)** | When to act, which buy-only opportunities to fund and in what priority, proposed quantity/limit price, Recurring/Lump-Sum policy, Pause/Resume/Discontinue response | Actual cash spending, final quantity/price determination, real order placement |
| Approval Console (Module 25, design-only) | Sole human-approval gate | Generating proposals itself |
| Portfolio Cash & Execution Manager (Module 28, Phase 10/12) | Cash ledger, Investment Queue, **final** quantity/limit-price verification, actual spend authority | Deciding *which* ETF or *why* (that's upstream) |
| Live/Paper Trading Engine (Phase 10/12) | Talking to Kite, real fills | Any allocation decision |
| Market Intelligence Engine (Module 27, separate later phase — §21) | Observation, indicator calculation, historical database (once built) | Any decision, trigger, or override — Strategy Engine consumes it only through `MarketIntelligencePort`, and only as read-only advisory context |

---

## 3. Monthly Funding Policy (Permanent, per Decision 2) and Lump-Sum Handling

### 3.1 The core rule

**Kite's actual available cash balance is the only source of truth for investable cash.** Not a calendar assumption, not an expected-amount estimate, not a standing order confirmation — the literal balance Kite reports. Strategy Engine's Recurring Monthly Policy is built entirely around this rule; everything below is a consequence of it, not a separate design choice layered on top.

### 3.2 State machine

```
AWAITING_FUNDS --(funds detected via CashLedgerPort)--> EXECUTING --(cycle complete)--> IDLE (until next 1st)
     |
     |--(daily check, funds still absent)--> AWAITING_FUNDS  [self-loop]
     |
     |--(date == 8th EOD AND funds still absent AND reminder not yet sent this month)
     |        --> send ONE Telegram reminder via NotificationPort, then --> AWAITING_FUNDS
```

- **1st of the month:** state resets to `AWAITING_FUNDS` for that month's cycle. A `notify_expected_contribution(amount, expected_date=1st, source=RECURRING_MONTHLY)` call is made via `CashLedgerPort` — this is an *informational* signal to the Investment Queue that a contribution is anticipated, not a cash movement (§16.10's "no module may directly spend cash" is unaffected: nothing is being spent, only anticipated). The Investment Queue entry (owned entirely by Module 28 once built) sits as `PENDING` until real funds are confirmed. Per §17, this invocation persists its outcome (still-pending) and exits — it does not remain running.
- **Daily, starting the 1st:** Strategy Engine (invoked once per day by the Scheduler — see §3.4 on triggering, and §0.5/§17 on why this is a fresh short-lived invocation each day, not a resident loop) queries `CashLedgerPort.get_available_pool()`. If `new_capital` reflects the expected contribution having actually landed, transition to `EXECUTING` and run the normal buy-only cycle (§6) **immediately, within that same invocation** — this is the "resume execution immediately after funds are detected" requirement, not a delayed or batched response.
- **If funds are absent:** place **zero** orders. Not partial, not margin-funded, not "close enough" — zero. The pending Investment Queue entry stays `PENDING`. Persist the "still waiting" outcome, log it, and exit — check again tomorrow's invocation, not via any loop or sleep within today's.
- **8th EOD, if still absent:** send exactly one Telegram reminder for the month (`NotificationPort.send(...)`) — explicitly a **reminder, not a cancellation**. The pending request is never dropped, never expired, never silently abandoned. Checking continues daily afterward, silently (no repeated daily reminders — see §3.5 for why this is a judgment call I'm flagging, not assuming).
- **Once the month's contribution is fully allocated** (§17.3 — not necessarily every target weight gap closed, just this cycle's available capital fully deployed per §5's priority rules): the funding workflow transitions to `IDLE (until next 1st)` and performs **no further daily checks or invocations at all** for the rest of the month — full stop, not a lighter-weight background check, per §17.3.
- **No margin, no leverage, ever, for this purpose:** `CashLedgerPort.get_available_pool()`'s real Module 28 implementation (Phase 10) must query Kite's actual settled/available-for-delivery cash balance specifically — **never** margin, collateral, or leveraged buying power, even if Kite would technically permit a trade against it. I'm recording this as a hard requirement on Module 28's future implementation here, since Phase 6 only consumes the port and can't itself enforce what Module 28 does internally — but the contract is binding regardless of which phase implements it.

### 3.3 Lump-sum handling under the same funding rule

A lump-sum contribution follows the identical rule — Strategy Engine never assumes a lump sum you've told it about verbally or via a future UI is actually investable until `CashLedgerPort` confirms it against real Kite balance. The only difference from the recurring case: a lump-sum cycle is triggered by your explicit instruction, not the calendar, and has no 8th-EOD reminder milestone (there's no fixed monthly deadline concept for an ad hoc contribution) — it simply waits for funds the same way, with reminders left to your own judgment about when to check in, since you initiated it and know your own timeline.

### 3.4 Who triggers the daily check

This reveals an implicit new dependency I need to flag: Strategy Engine's Recurring Monthly Policy requires being **invoked once per day**, not once per month as I'd loosely implied in the original draft of this document. The actual daily trigger mechanism is Phase 9's Scheduler — out of scope for Phase 6 to build, but Phase 6 needs to be *invokable* in a way that supports this (a simple "run today's check" entry point, idempotent if called more than once on the same day). I'm noting this now so it isn't discovered as a surprise when Phase 9 is designed.

### 3.5 One judgment call I'm not deciding for you

Should there be any reminder cadence *after* the 8th (e.g., another Telegram message if funds are still missing a week later, or after 15/20/25 days), or does the single 8th-EOD reminder stand alone until funds arrive, however long that takes? Your instruction described the 8th as "a reminder milestone," singular, and I've implemented exactly that — one reminder, then silent daily checking indefinitely. I did not add a recurring nag pattern on my own judgment, since you didn't ask for one and I'd rather under-notify than invent a notification cadence you didn't specify. If you want a longer-silence follow-up (e.g., "remind me again if still unfunded after 20 days"), tell me and I'll add it as a second, symmetric milestone.

---

## 4. Buy-Only Strategy Logic

Directly reuses Phase 5's `proposal_builder._buy_only_diff` pattern, extended with prioritization (§7) rather than reimplemented. A target weight below current weight still produces an informational note, never a sell — identical rule, identical enforcement point, no new logic invented for this phase to accidentally get wrong differently. `RiskEvent`'s negation-aware sell-instruction guard (Phase 5) applies unchanged to any text Strategy Engine generates for human-facing rationale.

---

## 5. ETF Selection Priority and Ranking Methodology

Strategy Engine does not re-rank ETFs — it consumes Portfolio Optimizer's target weights and Phase 3's scores as given. Its own "priority" question is narrower: **given a limited `new_capital` amount that may not cover every underweight position, which gets funded first?**

**Recommended default: largest absolute weight-gap first** (the ETF furthest below its target weight is funded first, then the next, until capital is exhausted). This is simple, explainable in one sentence to a non-developer, and self-correcting — whatever doesn't get funded this cycle is still the most underweight next cycle, so it naturally rises to the top of the queue rather than being starved indefinitely.

**Rejected alternative — proportional deployment** (spread `new_capital` across all buy-only opportunities in proportion to their gaps, funding all partially rather than some fully): more "fair" in one sense, but for small SIP amounts relative to lot sizes, this risks computing fractional-unit target purchases that round down to zero for several ETFs simultaneously (wasting the cycle on those positions entirely) rather than concentrating enough capital to buy at least a whole unit of the highest-priority one. Given ETFs trade in whole units only (Phase 4's `OrderIntent` validation, frozen), largest-gap-first is more likely to produce an actual executable order at small capital levels — directly serving the ₹1,000-to-₹5,00,000 capital-agnostic requirement, since the *same* priority logic must work sensibly at both ends of that range.

**Rejected alternative — a new independent ranking score:** would duplicate Phase 3's scoring work and risk producing a different opinion about ETF quality than the one already validated and explainable there. Strategy Engine's priority is about *funding order given a shortfall*, not *which ETF is better* — those are different questions, and only the first is Phase 6's to answer.

---

## 6. Cash Allocation and Investment Scheduling Logic

On a triggered cycle (SIP or lump-sum):
1. Query current holdings (weights) and Portfolio Optimizer's last-approved target weights.
2. Compute the buy-only diff (§4).
3. Order by priority (§5).
4. Walk down the priority list, tentatively allocating `new_capital` to each position (whole-unit quantities only) until exhausted or the list is fully funded.
5. For each funded position, propose a quantity and limit price (§0.1) — package as an `OrderIntent` plus the same rich explanation fields the Approval Console already requires (Phase 5's `ProposalArtifact` pattern, extended, not replaced).
6. Submit for approval. Never execute directly (§2).

This is intentionally a **pure, stateless computation given its inputs** — Strategy Engine holds no persistent state of its own about "how much cash is available" (that's Module 28's job via `CashLedgerPort`, §0.4) or "what was last approved" (that's the `allocation_decisions` table, already in the frozen §6 schema). Statelessness here is deliberate: it makes Strategy Engine trivially testable (same discipline as Portfolio Optimizer) and means a crash mid-cycle loses no state that wasn't already durably recorded elsewhere.

---

## 7. Limit-Order Execution Strategy Through Kite

This is explicitly a **policy Strategy Engine defines, not mechanics it executes** (§0.1) — real Kite interaction is Phase 12. The policy:

- **Limit price:** default to the most recent available close price (a conservative anchor for a long-term buy-and-hold context, not a tight scalping price) with a small configurable buffer (e.g. +0.1-0.5%) to improve fill probability without chasing the market — the exact buffer is a provisional, disclosed parameter (same honesty standard as Phase 5's drift tolerance), not a researched-optimal value.
- **Order type default: LIMIT, not MARKET**, for long-term SIP-style buying — deliberate, since a long-term investor buying monthly has no urgency that would justify accepting an unknown market price, and a limit order bounds the worst case. Market orders remain available as a configurable alternative for time-sensitive scenarios (none currently exist in this platform's scope, but the frozen `OrderIntent` model already supports both, so nothing new is needed to allow it later).
- **Rejected alternative — always use MARKET orders for simplicity:** rejected because it removes price protection for no benefit in a non-time-sensitive, long-term-investing context — exactly the scenario this platform is built for (Phase 1 §0).

---

## 8. Partial Fills, Unfilled Orders, Expiry, and Retry Rules

**Reuses Phase 4's already-built, already-tested mechanics conceptually** (`max_volume_participation_pct` partial fills, `limit_order_expiry_days`, expiry-respecting re-queue logic) — Strategy Engine's job is to set sensible *policy values* for these existing frozen mechanisms, not invent new fill logic:

- **Expiry:** a proposed order that hasn't filled within N business days (configurable, provisional default matching Phase 4's default of 5) is not silently re-submitted — it returns to the priority queue for the *next* cycle's consideration, re-evaluated against then-current weights and prices, not blindly retried at a stale price.
- **Partial fills:** handled by the same volume-participation-cap mechanism already in `BacktestEngine`/`FillSimulator` (Phase 4, frozen) — when Phase 10/12 build the real execution path, they reuse this exact mechanism rather than a new one, since it was already adversarially tested there.
- **Retry:** an expired, unfilled order is a **new decision** next cycle (re-priced, re-prioritized), never an automatic identical resubmission — this avoids blindly chasing a price that may no longer make sense relative to updated target weights.

---

## 9. Monthly Investment Workflow (Capital-Agnostic)

Identical logic path for ₹1,000 and ₹5,00,000, verified the same way Phase 5 verified it (§10 testing strategy, below): an AST scan confirming no hardcoded amount appears in Strategy Engine's source, and a proportional-scaling test across multiple capital levels. The only different *outcome* at small capital is that fewer (or zero) whole-unit purchases may be executable in a given cycle — which the priority-queue design (§5, §6) already handles gracefully: unfunded positions simply carry forward, they don't error or behave differently in kind.

---

## 10. Telegram Pause / Resume / Discontinue Integration

Via `NotificationPort` (§0.4), an abstract interface Module 13 will implement later:

```
NotificationPort:
    send(message: str) -> None
    poll_commands() -> list[Command]   # Command = PAUSE | RESUME | DISCONTINUE, with timestamp
```

- **Pause:** Strategy Engine stops proposing new orders on the next scheduled trigger until Resumed. Does not affect orders already pending approval or already approved — those follow through their normal lifecycle (this avoids a Pause command creating ambiguous half-executed state).
- **Resume:** normal operation continues from the next trigger.
- **Discontinue:** stops all future proposal generation permanently until explicitly reconfigured (distinct from Pause — Discontinue is a deliberate stop, not a temporary one, and should require a more explicit re-enable step than Resume, e.g. reconstructing the Strategy Engine with fresh configuration rather than a single command).

**Rejected alternative — Pause/Discontinue implemented as the same command with different durations:** rejected because conflating them risks an accidentally-permanent pause or an accidentally-temporary discontinue; keeping them structurally distinct forces whoever builds Module 13's real command parsing to be explicit about which one a user meant.

---

## 11. Module 26 (Self-Healing Framework) Integration

Via `OperationalEventPort` (§0.4). Strategy Engine emits structured events (job started, job completed, job failed, no-op cycle, proposal generated) that Module 26 will consume once built — Strategy Engine does not implement any self-healing logic itself (§0's dependency-inversion pattern). Critically, per §14.5's already-binding rule, Module 26 can only ever halt Strategy Engine's *operational* execution (e.g., if it detects Strategy Engine is stuck or crashing repeatedly) — it can never inject or approve an allocation decision itself. This phase does not change that boundary; it only gives Module 26 something concrete to observe once it exists.

---

## 12. Investment Queue and Monthly Cash Ledger Integration

Via `CashLedgerPort` (§0.1a, §0.4) — the full contract, including `notify_expected_contribution`, is specified in §0.1a and its daily-polling usage in §3. Restated briefly here for completeness:

```
CashLedgerPort:
    get_available_pool() -> AvailableInvestmentPool
    get_pending_queue_entries() -> list[QueueEntrySummary]
    notify_expected_contribution(amount, expected_date, source) -> None
```

Strategy Engine reads `get_available_pool()`/`get_pending_queue_entries()` and *notifies* (never spends) via `notify_expected_contribution()` — it never writes to the cash ledger balance or marks a queue entry as funded/invested directly (§16.10's binding rule: only Module 28 spends cash, and only Module 28's future implementation transitions a queue entry from `PENDING` to `FULLY_INVESTED`). For Phase 6's own testing, a fake in-memory implementation stands in, exactly like every other Phase 3-5 fake-provider test pattern — including simulating the "funds arrive on day N" transition so the §3 state machine can be tested deterministically.

---

## 13. Failure Handling, Audit Logging, Reproducibility, Compliance

- **Failure handling:** any exception during a cycle aborts that cycle's proposal generation cleanly — no partial proposal is ever submitted. Consistent with Phase 4's fail-safe default (§1.4) and Phase 5's `run_and_register` pattern (a cycle's outcome, success or failure, is always recorded, never left in an ambiguous state).
- **Audit logging:** every generated proposal, every priority-queue decision, and every Pause/Resume/Discontinue transition is logged with a plain-English rationale — same standard as every other module in this platform since Phase 4's trade-explanation requirement.
- **Reproducibility:** Strategy Engine's output must be fully reproducible from (code commit hash, config version, data snapshot id, Available Investment Pool state at call time) — identical to Phase 4/5's existing reproducibility mechanism, reused not reinvented.
- **Compliance:** no interaction with Module 24 (Compliance & Regulatory Engine) is needed in Phase 6, since no real order reaches an exchange until Phase 12 — noted here only so its absence isn't mistaken for an oversight.

---

## 14. Paper-Trading Workflow and Live-Trading Transition

Because Strategy Engine implements the same frozen `Strategy` interface used by the Backtesting Engine (§0.1), the **exact same Strategy Engine code** runs in three contexts without modification:
1. **Backtest** (Phase 4, already frozen): `BacktestEngine.run()` calls `generate_orders()` against historical data.
2. **Paper trading** (Phase 10): the same call, against live data, with fills simulated rather than sent to a broker.
3. **Live trading** (Phase 12): the same call, with real fills via Kite, gated by Module 28 and the Approval Console.

This is the direct realization of Phase 1 §4's original design intent — *"what's validated in backtest is what actually runs live."* No separate "live version" of Strategy Engine is ever built; only the execution context around it changes.

---

## 15. Performance Considerations for AWS Micro

Strategy Engine's own computation (priority queue over a small number of ETFs, weight arithmetic) is trivial — Phase 5's Portfolio Optimizer already demonstrated sub-second performance at 500 ETFs, and Strategy Engine's workload is a strict subset of that. **However**, unlike Phase 3-5 (research-side only), Strategy Engine's *invocation* (SIP timing, Pause/Resume response) is inherently a **live-instance concern** — it needs to be cheaply invokable on the live micro whenever the Scheduler (Phase 9) fires it. Per §0.5/§17's event-driven principle, the *watching* itself (the calendar, incoming Telegram commands) is the Scheduler's and `NotificationPort`'s job, not something Strategy Engine does by sitting in a loop — Strategy Engine only needs to exist, briefly, when actually invoked. Per §12.1's binding constraint, this means Strategy Engine's core logic should remain dependency-light enough to run on the live micro (no numpy/scipy requirement, unlike Phase 3-5) — it consumes Portfolio Optimizer's *output* (already-computed weights), it does not recompute volatility or run its own numerical optimization. This is a real, binding design constraint I'm flagging now rather than discovering during implementation.

---

## 16. Edge Cases and Known Risks

- **New capital insufficient to buy even one whole unit of the highest-priority ETF:** the cycle produces zero orders, not an error — carries forward to next cycle with more accumulated capital (relevant at the ₹1,000 end of the capital range, especially for higher-priced ETFs).
- **All target weights already met (no buy-only opportunities exist):** `new_capital` has nowhere to go productively; Strategy Engine should report this explicitly (feeds Module 28's idle-cash aging rule, §16.9) rather than force a purchase.
- **Portfolio Optimizer's last-approved weights are stale** (Risk Management Engine has since detected drift or a breach): Strategy Engine should not blindly execute against outdated targets — needs a freshness check, likely requiring a fresh Portfolio Optimizer call before proceeding. Exact staleness threshold is a provisional parameter, disclosed as such.
- **Pause received mid-cycle, after proposals already generated but before Approval Console review:** per §10, in-flight proposals are not silently cancelled — only *future* cycles stop.
- **Two Recurring and Lump-Sum triggers overlapping in time:** needs an explicit ordering/locking rule so they don't compute against inconsistent snapshots of "current holdings" — flagged as a design detail to resolve during implementation, not fully specified here.
- **Funds arrive partially, not in full** (e.g., you transfer less than the usual monthly amount): per §3.1, `CashLedgerPort` reports whatever the actual balance is — Strategy Engine treats that as the real `new_capital` for the cycle and allocates it via the normal priority order (§5), it does not wait for "the rest" to arrive before doing anything, since there's no way to distinguish "a smaller transfer was intentional" from "the rest is coming later" without asking you, and blocking productive deployment of confirmed real cash on a guess would be the wrong default.
- **The 8th falls on a weekend or market holiday:** the reminder milestone is a calendar-date check against Kite balance, not a trading-day check — "8th EOD" means the 8th's end of day regardless of market status, since the underlying question ("has money arrived in the bank-linked account") doesn't depend on whether the exchange is open.
- **Funds detected mid-check but the amount is larger than expected** (e.g., you transferred extra, or a dividend landed the same day): Strategy Engine uses the actual reported `new_capital` as-is (§3.1's "Kite balance is the only source of truth") — it does not cap deployment at some previously-communicated "expected" figure, since there isn't a mechanism for you to have pre-declared an exact expected amount in the first place, only a general monthly cadence.
- **The Telegram reminder fails to send** (Module 13 not yet built, or a future delivery failure): per §0.3's dependency-inversion design, a `NotificationPort` failure should not block the underlying daily check-and-wait logic — the state machine (§3.2) continues regardless of whether the reminder successfully reached you, and the failure itself should be a signal Module 26 can observe (§11), not a reason to stop checking for funds.

---

## 17. Testing Strategy

- **Unit tests:** priority-queue logic (largest-gap-first, hand-verified small examples), buy-only diff reuse, capital-agnostic behavior (AST scan + proportional-scaling test, identical methodology to Phase 5), Pause/Resume/Discontinue state transitions.
- **Monthly Funding Policy state-machine tests (new, per Decision 2):** every transition in §3.2 exercised directly — zero orders while `AWAITING_FUNDS`, immediate execution on the same check that detects funds, exactly one reminder fired at 8th EOD (not zero, not more than one), reminder does not fire before the 8th, reminder does not re-fire on the 9th/10th/etc., `notify_expected_contribution` called exactly once per month on the 1st, partial/excess funds handled per §16's edge cases, idempotent behavior if the daily check is invoked twice on the same day.
- **Integration tests:** full pipeline from Phase 3's screened universe through Phase 5's Portfolio Optimizer through Strategy Engine's proposal generation, using fake `CashLedgerPort`/`NotificationPort`/`OperationalEventPort` implementations.
- **Regression tests:** locked-value baseline (same discipline as every prior phase).
- **Adversarial review (separate, after implementation, per your established pattern):** attempts to construct a sell instruction via any Strategy Engine code path (should be structurally impossible, verified the same way as Phase 5); insufficient-capital edge cases; overlapping-trigger race conditions; malformed `AvailableInvestmentPool` inputs; attempts to make Strategy Engine place an order using margin/leverage-flagged balance; attempts to make the reminder fire twice or the state machine skip the `AWAITING_FUNDS` check entirely.
- **Backtesting compatibility test:** the single most important new test category — instantiate Strategy Engine and run it through Phase 4's actual frozen `BacktestEngine` unmodified, proving the frozen interface contract holds in practice, not just in type signatures.

---

## 18. Production-Readiness and Exit Criteria

Same seven-point structure as RELEASE_POLICY.md, applied to Phase 6 when implementation is complete: all tests pass, every defect found has a regression test, no Critical/High issues open, documentation consistent, reproducibility verified, no frozen interface modified (Phases 1-5 now, not just 1-4), release metadata recorded before any tag.

---

## 19. Proposed Wording Amendment to §15.6 (Precision Correction, Not a Design Change)

**Current §15.6 text:** *"the Strategy Engine's public interface must accept an `AvailableInvestmentPool`... and must return weights, never amounts or quantities."*

**Proposed replacement text:** *"the Strategy Engine's internal allocation logic must remain weight-based throughout — it computes what fraction of new capital should go where using Portfolio Optimizer's target weights, never a hardcoded rupee amount. Its public interface, `generate_orders()` (Phase 4's frozen `Strategy` interface), necessarily returns `OrderIntent`s bearing a concrete quantity — required by the frozen Backtesting Engine contract — computed only at the final conversion step from the `AvailableInvestmentPool`'s value at call time, mirroring §12.3's 'converted to actual order quantities only at execution time.' No module may hardcode a rupee amount; producing a quantity from a live pool value at the moment of conversion is not the same thing as hardcoding one."*

This is a wording correction resolving §0.2's identified literal contradiction — it does not change any behavior, constraint, or capital-agnostic guarantee already agreed. I'm proposing the exact replacement text here rather than just describing the change, so you can approve or edit the actual words that will go into the frozen architecture document.

---

## 20. Summary of Items Requiring Your Explicit Approval

Decisions 1-4 (your last message) resolved everything that was open in the original draft of this document — restated here as **closed**, not re-asked: §0.1/§0.1a (Strategy Engine proposes, Module 28 has final one-directional veto authority), §0.2 (Module 27 stays reserved, Execution Policy folds into Phase 6), §0.3 (port-based dependency inversion), §0.4 (EC2 micro placement), §0.5/§17 (Event-Driven Resource Optimization, recorded as a permanent cross-cutting SRS amendment — no conflicts found against anything previously approved).

**Genuinely still open, carried forward or newly surfaced while designing Decision 2 in full:**

- **§3.5** — whether a reminder cadence should exist beyond the single 8th-EOD milestone (e.g., a follow-up if funds are still absent much later). I implemented exactly what you specified (one reminder) and did not invent a recurring cadence on my own judgment.
- **§5** — confirm largest-weight-gap-first as the funding priority default (rejecting proportional deployment).
- **§7** — confirm LIMIT orders as the default order type for long-term SIP-style buying (rejecting MARKET-by-default).
- **§10** — confirm the Pause/Resume vs. Discontinue distinction (different re-enable requirements) rather than a single duration-based command.
- **§19** — approve the proposed §15.6 wording amendment (or edit it) before I apply it to PHASE1_Architecture_SRS.md.

Everything else in this document, including the full Monthly Funding Policy state machine (§3), is a specification of what you already decided, not a further question.

---

## 21. Market Intelligence Port (Interface Only — Module 27 Itself Is Out of Scope)

**Resolved by your final decision on Module 27's sequencing: Module 27 will not be implemented in Phase 6. Phase 6 defines only the interface Strategy Engine will optionally consume it through, once it exists.** This section is what makes that boundary concrete rather than aspirational.

### 21.1 The port

Same dependency-inversion pattern as `CashLedgerPort`, `NotificationPort`, and `OperationalEventPort` (§0.3/§0.4) — Strategy Engine depends on an abstract interface, never a concrete Module 27 implementation:

```
MarketIntelligencePort:
    get_market_regime(as_of_date) -> MarketRegimeSnapshot | None
    get_relative_strength(symbol, as_of_date) -> float | None
    get_sector_strength(sector, as_of_date) -> float | None
```

Every method returns `| None`. **`None` is not an error condition** — it's the expected, normal return value whenever Module 27 is unavailable, disabled, or simply has no data yet for that date. This is the literal implementation of *"the Strategy Engine must function correctly whether Module 27 exists or not."*

### 21.2 How Strategy Engine is permitted to use it

Consistent with §18.0/§18.2's exhaustive prohibition list (Module 27 can never trigger, gate, or override anything): if `MarketIntelligencePort` returns data, Strategy Engine may attach it as **read-only, informational context** on a generated proposal (e.g., a line in the proposal's rationale text: "Market regime: Bull, as of [date]" — descriptive, not decisional). It must **never** be used as a condition inside §5 (priority ordering) or §6 (cash allocation logic) — those sections' behavior must be provably identical whether `MarketIntelligencePort` returns real data or `None` for every call. This is the same class of structural guarantee already used for the manual-selling rule: not "we intend not to," but "the code path to do otherwise doesn't exist."

### 21.3 Testing implication

Phase 6's test suite (§17) needs a `NullMarketIntelligencePort` fake — one that always returns `None` — used as the **default** test double for every Strategy Engine test, not an edge case tested once. If Strategy Engine's core tests only ever ran against a fake that returns rich data, the "must function correctly if absent" requirement would be asserted but never actually verified. Running the *entire* existing test suite against both a `NullMarketIntelligencePort` and a fake that returns populated data, and confirming identical `OrderIntent` outputs either way, is the concrete verification of §21.2's guarantee — not a separate test category, but a parameterization of the whole suite.

### 21.4 What this deliberately does not include

No `MarketRegimeSnapshot` schema beyond what §21.1's type hints imply, no indicator calculation logic, no historical database, no research-side/live-micro compute split — all of that is Module 27's own design, deferred to its dedicated phase per your decision. Phase 6 ships a contract Module 27 will later implement against; it does not anticipate Module 27's internals.

---

**With §21 added, this document reflects every decision made across this design conversation. Per your instruction, no implementation begins until you explicitly approve this finalized version.**
