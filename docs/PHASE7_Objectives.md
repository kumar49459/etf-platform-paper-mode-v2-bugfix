# Phase 7 Objectives & Design Document — Module 28: Portfolio Cash & Execution Manager, with Paper Trading

**Status: PROPOSED. No code will be written until you approve this document.**
**Builds on:** frozen v0.4 (Phases 1-4), v0.5 (Phase 5), v0.6 (Phase 6). Follows RELEASE_POLICY.md.

## 0. Naming clarification (read first)

The roadmap (§9) labeled "Phase 7" as the AI Dynamic Allocation Engine. Your decision explicitly defers that. This document is the *chronologically* next implementation phase, not roadmap-Phase-7 — I'm calling it "Phase 7 (execution-order)" in this document's title to avoid the two meanings colliding. **Roadmap renumbering — RESOLVED (Decision 5): do not renumber now.** Your exact instruction: complete this phase first, then "review the entire roadmap and renumber only if it improves long-term clarity" once it's frozen. No numbering change happens mid-flight.

---

## 1. Scope and Responsibilities

Per your twelve objectives and the already-frozen §16 amendment:

1. Implement `CashLedgerPort` exactly as Strategy Engine (frozen v0.6) already specifies it — no changes to that interface.
2. Own the full order lifecycle from a Strategy Engine proposal through terminal state (Complete/Cancelled/Failed).
3. Own real Kite API integration (Live) and a market-data-driven simulation (Paper) behind one shared internal interface.
4. Own the Investment Queue and Cash Ledger (§16.5-16.7's already-approved schema, extended for the order lifecycle — see §5).
5. Perform the final, authoritative affordability check immediately before submission, using live data — not Strategy Engine's proposal-time estimate.

**Explicitly not this phase's job:** anything Strategy Engine already does (weight computation, priority ordering, proposal generation — untouched), Module 26's process supervision, Module 13's actual Telegram delivery mechanics (Module 28 calls `NotificationPort`, doesn't implement it), Module 24's full Compliance & Regulatory Engine (flagged as a real dependency gap, §8.4).

---

## 2. Dependencies on Existing Modules (exhaustive)

| Dependency | What's used | Modification needed? |
|---|---|---|
| Strategy Engine `CashLedgerPort` (v0.6, frozen) | The exact interface Module 28 implements | **None** — implemented against as-is |
| Strategy Engine `CycleResult`, `OrderIntent` (v0.6/v0.4, frozen) | Source of proposed orders | **None** |
| Phase 4 `CostTaxEngine` (frozen) | Final affordability check (§7) — same engine, live inputs instead of proposal-time estimates | **None** |
| Phase 4 `OrderIntent` whole-unit validation (frozen) | Quantity is already guaranteed whole-unit at the proposal stage; Module 28 must preserve this through any downward adjustment | **None** |
| Phase 2 `SecretsManager` (frozen) | Kite API key/access token storage — no new secrets mechanism | **None** |
| Phase 2 `retry.py` (frozen) | Exponential backoff for Kite API calls | **None** — reused as-is |
| Phase 2 `HistoricalDataEngine` / `DataProvider` (frozen) | Historical OHLCV, if needed for context | **None** |
| Phase 5 `RiskManagementEngine.evaluate()` / `check_drawdown_constraint()` (frozen) | Verification-stage risk check (Decision 1: "Risk Management constraints") | **None** — called, not modified |
| `common/db.py` WAL+lock pattern (frozen) | Order lifecycle / cash ledger persistence | **None** — same pattern as every registry so far |
| §16.7 schema (`cash_ledger`, `investment_queue`, `execution_history` — approved, never implemented) | Base schema for this phase's persistence | **Extended, not modified** — this schema was always a sketch awaiting its first real implementation, same situation Phase 4/5 were in for several modules |
| §17 (Event-Driven Resource Optimization, frozen amendment) | Module 28 must be idle-by-default, wake on events, persist-log-release-idle | **None** — a constraint this design must satisfy |
| Module 24 (Compliance & Regulatory Engine) | "Compliance rules" in Decision 1's verification list | **Does not exist yet** — real gap, see §8.4 |
| Strategy Engine `NotificationPort` (v0.6, frozen) | **Found during this review — was missing from this table despite §17's runbook already assuming it.** Module 28 needs its own reference to alert on API outages, token expiry, and reconciliation failures (§17) — not only Strategy Engine's reminder use. Same interface, second consumer. | **None** — reused as-is, no interface change |

**One genuinely new interface, not a modification of anything existing:** Paper Trading needs live/current price data, which the frozen `DataProvider` interface (built for historical OHLCV ingestion) was never designed to serve. Proposed: a new, separate `LiveQuoteProvider` interface (§6.3) — additive, sitting alongside `DataProvider`, not replacing or altering it.

---

## 3. The Core Architectural Move: One Execution Interface, Two Implementations

Your objective 10 ("Strategy Engine cannot distinguish Paper from Live") is satisfiable trivially by construction — Strategy Engine never talks to a broker at all, only to `CashLedgerPort`, and that hasn't changed. But I'm proposing a stronger version of the same guarantee **one layer deeper**, because the weak version doesn't actually get you what Paper Trading is for.

**Rejected approach:** Module 28's own internal order-lifecycle code branches on `if paper_mode: simulate() else: call_kite()`. This satisfies your objective 10 literally (Strategy Engine still can't tell), but it means Paper Trading never actually exercises the state-machine, retry, idempotency, or verification logic in exactly the way Live Trading will — a bug in the branching itself, or in logic that only runs on one branch, would never be caught by paper trading. This defeats the entire point of "validate the Strategy Engine in a realistic execution environment" from your framing.

**Chosen approach — CONFIRMED:** an abstract `BrokerPort` interface — `submit_order()`, `get_order_status()`, `cancel_order()`, `get_open_orders()` (§8.2's reconciliation need) — with two concrete implementations, `KiteLiveBrokerPort` and `PaperBrokerPort`. **Module 28's entire order-lifecycle state machine operates against `BrokerPort` only, with zero knowledge of which implementation is behind it.** You've explicitly confirmed this exercises the complete execution lifecycle — retries, idempotency, persistence, restart recovery, and state transitions — identically in both modes, not just that Strategy Engine can't tell them apart. This is the exact same pattern already used three times in this codebase (`DataProvider`/`SecretsProvider` in Phase 2, `AllocationMethod` in Phase 5, the four ports in Phase 6) — not a new architectural idea, a consistent application of one.

```
Strategy Engine --(CashLedgerPort)--> Module 28 order-lifecycle state machine --(BrokerPort)--> KiteLiveBrokerPort | PaperBrokerPort
```

**Found during this review — a real gap that would have quietly undermined objective 9's purpose: `PaperBrokerPort`'s fill simulation must be able to produce every outcome the state machine defines, not just success.** If `PaperBrokerPort` always accepts and always fills, then "Paper Trading validates every execution path" (§23's operational readiness question) is false in practice even though it's true on paper — `FAILED`, `PARTIALLY_FILLED`, and `CANCELLED` would never actually be exercised until real money was on the line, which is exactly the outcome this whole phase exists to prevent. `PaperBrokerPort` needs deliberate, seeded simulation logic: occasional simulated rejections (mirroring realistic broker rejection reasons), partial fills when simulated order size is large relative to the queried `LiveQuoteProvider` depth, and non-fills when the simulated price never reaches the limit within the execution window — not just a coin-flip, but scenarios that plausibly mirror real conditions using the same live depth/price data `KiteLiveBrokerPort` would see. This is now a binding requirement on the implementation, not an optional realism enhancement.

---

## 4. Order Lifecycle State Machine

**Canonical state names (your exact list, adopted verbatim as the naming standard for this module), plus AMBIGUOUS (added post-launch per DDR-001, see below):** PROPOSAL -> VERIFIED -> SUBMITTED -> PENDING -> PARTIALLY_FILLED -> FILLED -> CANCELLED -> FAILED -> RECONCILED -> AMBIGUOUS.

```
PROPOSAL --(verification)--> VERIFIED --(passes, qty may be reduced)--> SUBMITTED
                                    |
                                    +--(fails / qty reduced to 0)--> FAILED

SUBMITTED --(persisted BEFORE broker call, per Phase 6's item-8 lesson)--> [broker call via BrokerPort]
                                    |
                                    +--(acknowledged)--> PENDING
                                    +--(broker rejects)--> FAILED
                                    +--(reconciliation cannot confirm either outcome)--> AMBIGUOUS

PENDING --(exchange reports a fill)--> PARTIALLY_FILLED --(remaining fills)--> FILLED
PENDING --(fully filled in one report)--> FILLED
PENDING --(expiry / market-condition cancellation / execution-window close)--> CANCELLED
PENDING --(liquidity check fails on a retry pass)--> stays PENDING, retried per policy (section 8), never silently upgraded to MARKET

FILLED --(reconciliation query confirms broker agrees)--> RECONCILED
CANCELLED --(reconciliation query confirms broker agrees)--> RECONCILED
FAILED --(reconciliation query confirms broker agrees)--> RECONCILED

AMBIGUOUS --(operator-invoked resolve_ambiguous_execution() ONLY, never automatic)--> any of
    {VERIFIED, PENDING, PARTIALLY_FILLED, FILLED, CANCELLED, FAILED, RECONCILED},
    whichever the operator actually confirmed by checking the broker directly
```

**RECONCILED is a new state, not in my original design -- added per your instruction to reflect Decision 1 (mandatory reconciliation).** It is deliberately a *closure* state reached only after Module 28 has independently confirmed, via a broker query, that its own persisted record of a terminal outcome (FILLED/CANCELLED/FAILED) actually matches what the broker itself reports. This is what makes reconciliation a first-class, auditable part of the lifecycle rather than an internal implementation detail -- every order this module ever handles has a visible record of "we checked, and the broker agrees," not just "we assumed."

**AMBIGUOUS is a second new state, added well after initial delivery, via DDR-001 -- a real defect found during the Kite Connect architecture review, not a preemptive design.** The original SUBMITTED-with-no-broker_order_id recovery path (matching against get_open_orders() by client_reference) assumed that "not found among open orders" meant "confirmed never reached the broker" -- proven false by direct test against PaperBrokerPort itself: an order that reached the broker and already resolved to a terminal state before the check ran is *also* absent from the open-orders set, and the old logic could not tell the two cases apart. It guessed the safe-looking one and reverted to VERIFIED for automatic retry, which risked a genuine duplicate submission. AMBIGUOUS replaces that guess with an explicit, terminal-for-automation state: **the platform now never automatically retries an order whose broker outcome is unknown.** The only way out is ReconciliationService.resolve_ambiguous_execution(), an explicit, human-invoked action requiring mandatory operator_notes, callable only after a human has actually confirmed the broker's real state through channels this module's own API access could not provide (the broker's web/app order history, contract notes, direct support contact).

### 4.1 Expired/Cancelled Order Handling — CONFIRMED, your exact sequence adopted verbatim

If a LIMIT order expires, is cancelled due to market conditions, or remains unfilled until the configured execution window ends:
1. Cancel any remaining open order.
2. Record the complete execution outcome.
3. Persist the execution state.
4. Notify Strategy Engine only that the investment was not completed (via `confirm_cycle_outcome(submitted_successfully=False)` — frozen v0.6, no new interface needed, see below).
5. Strategy Engine evaluates the situation again during the next scheduled execution cycle.
6. Any new price, quantity, allocation, or execution decision is always generated by Strategy Engine.
7. Module 28 never creates a new trading decision on its own.

**Confirmation, not a new decision:** step 4 requires no new interface. `confirm_cycle_outcome(as_of_date, execution_policy, state_store, submitted_successfully=False)` is already frozen in v0.6 and does exactly this — the orchestration layer (not yet built) calls it when Module 28 reports non-completion, and Strategy Engine's existing state machine reverts to a re-evaluation-ready state automatically.

This preserves the strict separation you specified: **Strategy Engine = investment decisions. Module 28 = execution and order lifecycle. Broker = order transport.**

**VERIFIED is where Decision 1's full checklist runs:** actual Kite cash balance (via `BrokerPort` or a dedicated balance query), current market price (via `LiveQuoteProvider`), Investment Queue state, `RiskManagementEngine.evaluate()`, and Compliance rules (§8.4). Quantity is recomputed here using `CostTaxEngine` against *live* inputs — the same `_affordable_quantity` logic Strategy Engine already uses, re-run with real numbers instead of proposal-time estimates. Per the one-directional constraint (frozen at §0.1a): this step may only reduce quantity or defer, never increase risk beyond what the Approval Console already approved.

**Found during this review — a hidden-coupling risk, not previously stated: Module 28 must process `CycleResult.orders` in the exact order Strategy Engine provided them, never reordered.** `CycleResult.orders` is already priority-ordered (largest weight-gap first, per Phase 6 §5) — if live prices have moved unfavorably since proposal time and cumulative affordability across all proposed orders is now less than expected, the orders that get reduced or dropped first must be the **lowest**-priority ones, preserving Strategy Engine's ranking. If Module 28 processed orders in a different order (e.g., parallelized for throughput, or reordered by symbol), it would silently override which position effectively "wins" the available cash when a shortfall occurs — that is Module 28 making an allocation judgment by omission, which directly violates "Module 28 never creates investment decisions" even though no code path explicitly computes a new price or quantity. This needs to be an explicit, tested invariant (§19's checklist item 8 should be read as covering this precisely, but the mechanism wasn't previously spelled out) — sequential processing in proposal order, not a general "process however is convenient" assumption.

---

## 5. Persistence — Extending the Already-Sketched §16.7 Schema

§16.7 was a sketch, never implemented. Extending it (additively) to carry the order lifecycle:

```
cash_ledger(entry_id, timestamp, transaction_type[...], amount, running_balance, queue_id, notes)  -- as originally sketched, now implemented

investment_queue(queue_id, deposit_date, amount, source[...], remaining_balance, status[...])  -- as originally sketched, now implemented

execution_history(execution_id, queue_id, cycle_id, timestamp, symbol, quantity_proposed, quantity_final,
                   limit_price, order_status[proposal/verification/submission/pending/partial_fill/
                   complete/cancelled/failed], broker_order_id, executed_price, executed_quantity,
                   cost_breakdown_ref, remaining_cash_after, is_paper_trade, last_status_check)
```

New columns beyond the original sketch: `cycle_id` (ties back to Strategy Engine's idempotency key), `order_status` (the lifecycle state, §4), `broker_order_id` (for reconciliation, §8.2), `is_paper_trade` (so paper and live history are queryable separately even though they share one table and one code path). Same WAL+lock SQLite pattern as every registry since Phase 2.

---

## 6. Kite API Integration

### 6.1 Authentication
Via `SecretsManager` (frozen) — API key and access token stored and retrieved through the existing abstraction, no new secrets mechanism. Token refresh/expiry handling is a real open question (§8.1).

### 6.2 Rate limiting and retry
Reuses `common/retry.py`'s exponential-backoff pattern exactly as Phase 2's providers already do. Kite Connect's actual rate limits need verification against real API docs during implementation (§8.1) — this environment has no network access to confirm them now.

### 6.3 Live market data (new interface, not a modification)
```
LiveQuoteProvider (new, additive):
    get_last_traded_price(symbol) -> float
    get_market_depth(symbol) -> MarketDepthSnapshot | None   # for liquidity protection, §8
```
`KiteLiveBrokerPort` implements this against real Kite quote endpoints; `PaperBrokerPort` needs *some* live price source too (§8.3 — real open question about whether Paper Trading gets its own lightweight quote polling or reuses `KiteLiveBrokerPort`'s quote capability without ever touching its order-placement capability).

---

## 7. Final Affordability Validation (Objective 7)

Same principle as Phase 6's `_affordable_quantity` fix, applied at the *right* moment this time — immediately before submission, not at proposal time:
1. Query live available cash (real Kite balance, not Strategy Engine's estimate).
2. Query live price (via `LiveQuoteProvider`, not yesterday's close).
3. Recompute the largest whole-unit quantity whose gross cost plus full `CostBreakdown` (all seven components, per the standard already set in Phase 6's review) fits within live available cash.
4. If the recomputed quantity is less than proposed, reduce (never increase) — logged with the specific reason (price moved, cash changed, or both).

---

## 8. Architectural Risks and Unresolved Questions (the most important section)

### 8.1 Kite API behavior is entirely unverified — the single largest risk
This environment has never made a real network call to Kite. Rate limits, exact error codes/formats, token refresh semantics, and whether Kite supports a client-supplied idempotency/order-tag for de-duplication are all currently *assumed reasonable*, not confirmed. **I'm recommending the design proceed on documented assumptions (flagged inline in the eventual code, same honesty standard as Phase 4's disclosed slippage figure), with a mandatory first-implementation-milestone of verifying each assumption against real Kite sandbox/docs before this phase can be considered code-complete** — not something I can resolve in a design document alone.

### 8.2 Idempotency and reconciliation — RESOLVED (Decision 1)

**Confirmed, mandatory, unconditional:** broker reconciliation happens after every restart, before any new order submission — never gated behind "if we suspect something went wrong." Your exact framing: **"Never rely on assumptions about the previous execution state. Broker reconciliation shall always be the source of truth."** This is now a hard architectural invariant, not an optimization — before submitting anything, Module 28 queries `get_open_orders()` (and, per §4's new RECONCILED state, reconciles every terminal-but-unconfirmed outcome too) and treats the broker's answer as authoritative over whatever is locally persisted.

### 8.3 Paper Trading's price source — RESOLVED (Decision 2)

**Confirmed:** Paper Trading uses the same `LiveQuoteProvider` and `BrokerPort` architecture as Live Trading — live read-only market data from Kite, never submitting real orders. Your stated objective, recorded precisely: **"exercise the same execution lifecycle, state machine, retry logic, persistence, and recovery logic before real money is involved."** This is the strong form of Paper Trading validation §3 was designed to enable, not the weak form — confirmed here as the intended use, not just a possible one.

### 8.4 Compliance rules without Module 24 — RESOLVED (Decision 3)

**Confirmed:** exactly two narrow inline checks, no more — static IP verification and Algo ID tagging, both already approved in Phase 1's §7 compliance section. Your exact constraint: **"No additional compliance logic shall be added inline. Future compliance requirements belong in a dedicated Compliance Module (Module 24)."** This is now a hard scope boundary, not a starting point that might grow — any compliance need beyond these two specific checks is explicitly out of this phase's scope and belongs to a future Module 24, not an expansion of Module 28's inline logic.

**Found during this review, in service of the future-extensibility question (§8 of your review request): even these two narrow checks should sit behind a small internal `ComplianceCheckPort` interface, not be called directly from Module 28's core verification logic.** Without this, "swap the two inline checks for a real Module 24" later means editing Module 28's verification code directly — a modification to its core, which is exactly what you asked me to verify won't be necessary. With a minimal `ComplianceCheckPort` (one method: `check(order) -> ComplianceResult`), today's implementation is a `MinimalInlineComplianceChecker` satisfying that interface, and Module 24's eventual real implementation is a second implementation of the same interface — Module 28's core code never changes, only which implementation is wired in. Same pattern as every other port in this design, applied here specifically because you asked me to verify this exact future-extensibility property, not because it was obviously needed before that question was asked.

### 8.5 Liquidity protection thresholds
"Wide bid-ask spread" and "no suitable sellers" need actual numeric thresholds (spread %, minimum depth) to be checkable at all. I have no principled way to set these without real market microstructure data for the specific ETFs involved. **Proposing provisional, disclosed defaults** (same honesty pattern as the limit-price buffer in Phase 6) rather than pretending precision that doesn't exist yet.

### 8.6 Retry-vs-Strategy-Engine boundary — RESOLVED, see §4

Confirmed: Module 28 retries submission-level transient failures only; expiry/cancellation always routes back to Strategy Engine via `confirm_cycle_outcome(submitted_successfully=False)`, never an autonomous re-price. Recorded in full at §4 with your exact seven-step sequence.

### 8.7 EC2 Micro load from real Kite connectivity — RESOLVED (Decision 4)

**Confirmed:** short-lived polling invocations, no resident process, event-driven architecture preserved. Your exact conditional escape hatch, recorded precisely so it isn't lost: **"Only if real-world testing later demonstrates that polling is insufficient should we consider a WebSocket exception."** This is a real, deliberate reopening condition, not a rejection of the idea entirely — if polling proves too slow once this is actually running against real fills, a WebSocket exception (matching the Live Trading Engine's already-approved carve-out from §17.2) is back on the table, but only with evidence, not speculatively now.

### 8.8 Concurrent invocations — found during the Design Readiness Review, a genuine gap

The design so far assumes sequential, non-overlapping invocations of Module 28. Nothing prevents two invocations from running at once — e.g., a Scheduler retry firing before a prior invocation has actually exited, or two independent trigger sources overlapping. Two overlapping invocations could both read "no order submitted yet" for the same cycle and both attempt to submit, defeating the entire reconciliation-based safety design, since reconciliation only protects against *sequential* restart-after-crash, not *simultaneous* execution. **This needs an explicit mutual-exclusion mechanism** — a claimed-cycle marker written atomically (a single `UPDATE ... WHERE status = 'unclaimed'` style claim, using SQLite's existing WAL+lock transaction guarantees rather than a new locking primitive) before any verification work begins, so a second concurrent invocation observes the cycle as already claimed and exits immediately rather than proceeding. This is a real design requirement, not an implementation detail — flagging it here rather than letting it be discovered as a production incident.

### 8.9 Database corruption — found during the Design Readiness Review, not previously addressed

`PRAGMA synchronous=FULL` (Phase 6's pattern, reused here) protects against losing the *most recent* commit on power failure — it does not protect against the local SQLite file becoming corrupted (a rare but real possibility: disk failure, a bug, an interrupted write in some pathological scenario). Decision 1's principle — the broker is always the source of truth — is what makes this recoverable rather than catastrophic: on startup, `PRAGMA integrity_check` runs before anything else; if it fails, Module 28 treats the local database exactly as it would treat a freshly-provisioned EC2 instance with no history — full reconciliation against the broker rebuilds the current state (open orders, recent history) from the broker's authoritative record, rather than attempting to repair or trust a corrupted file. This is a direct, low-cost consequence of Decision 1 already being the right principle — it just wasn't explicitly connected to this specific failure mode before.

### 8.10 Clock and timezone discipline — found during the Design Readiness Review, easy to get wrong silently

Not previously addressed anywhere in this design. EC2 instances default to UTC; NSE operates in IST (UTC+5:30); execution windows, expiry checks, and the reminder-day logic already built into Strategy Engine (v0.6) all implicitly assume a specific timezone without it ever being made explicit in this document. A naive mix of UTC and IST `datetime` objects — even a single accidental comparison between an aware and a naive one, or between two objects in different zones — could silently place an order outside real market hours or miscompute an expiry window, and would very likely pass every unit test that doesn't specifically probe for it. **Binding requirement for implementation:** all internal timestamps are stored and computed in UTC; conversion to IST happens at exactly one well-tested boundary (market-hours/expiry decisions), never scattered across the codebase; no naive (timezone-unaware) `datetime` object is ever constructed. This is cheap to get right up front and expensive to debug later, which is exactly why it belongs in the design document rather than being left as an implementation afterthought.

### 8.11 Exchange holiday awareness for Module 28's own scheduling — minor, cost-only

Strategy Engine already won't generate orders on a non-trading day (`is_trading_day`, Phase 6). Module 28's own polling cadence (§8.7) doesn't yet reference this — polling for status changes on a day the exchange is closed is a harmless no-op, not a correctness risk, but it's an unnecessary API call against §17's cost-minimization principle. Recommending Module 28's polling schedule also skip non-trading days, using the same `is_trading_day` signal Strategy Engine already consumes — a small consistency improvement, not a new capability.

---

## 9. Interface Changes to Frozen Phases

**None required.** Every dependency in §2 is consumed as-is. The one new interface (`LiveQuoteProvider`, §6.3) is additive, sitting alongside `DataProvider`, not modifying it — same pattern as adding `MarketIntelligencePort` in Phase 6 without touching anything frozen.

---

## 10. EC2 Micro / Event-Driven Compliance

Module 28 runs on the live instance (this is *the* live-instance module, more than Strategy Engine even was). Dependency-light by the same discipline as Phase 6 (no numpy/scipy in the core order-lifecycle logic). The one deliberate exception to "idle by default" is §8.7's pending-order polling cadence — flagged, not silently assumed compliant.

---

## 11. Sequence Diagrams

Text-based, not Mermaid — this is a document meant to be read in any editor, not necessarily rendered. Participants abbreviated: **SE** = Strategy Engine, **M28** = Module 28 order-lifecycle core, **BP** = BrokerPort (either implementation), **LQ** = LiveQuoteProvider, **Kite** = the real Kite API (Live) or nothing at all (Paper — LQ still calls real Kite read-only endpoints, but BP never does), **DB** = State Store / execution_history, **Notif** = Notification Service (Module 13, via NotificationPort).

### 11.1 Normal execution (proposal to fill)

```
SE   -> M28   : CycleResult (proposed orders, cycle_id)
M28  -> DB    : persist PROPOSAL
M28  -> LQ    : get_last_traded_price(symbol)
LQ   -> Kite  : quote request (read-only)
Kite -> LQ    : current price
M28  -> M28   : recompute affordable quantity (CostTaxEngine, live cash + live price)
M28  -> DB    : persist VERIFIED (quantity possibly reduced, never increased)
M28  -> DB    : persist SUBMITTED  <-- BEFORE the broker call, per section 4/section 8 ordering discipline
M28  -> BP    : submit_order(order)
BP   -> Kite  : place order (Live) | simulate acceptance (Paper)
Kite -> BP    : broker_order_id, acknowledged
BP   -> M28   : broker_order_id
M28  -> DB    : persist PENDING (broker_order_id attached)
M28  -> BP    : get_order_status(broker_order_id)   [short-lived polling invocation, section 8.7]
BP   -> Kite  : status query (Live) | simulated fill check against LQ price (Paper)
Kite -> BP    : FILLED, executed_price, executed_quantity
BP   -> M28   : FILLED
M28  -> DB    : persist FILLED
M28  -> BP    : get_open_orders()  [reconciliation check, mandatory per Decision 1]
BP   -> Kite  : open orders query
Kite -> BP    : (this order not in open orders -- confirms it's really done)
M28  -> DB    : persist RECONCILED
M28  -> SE    : confirm_cycle_outcome(submitted_successfully=True)
```

### 11.2 Partial fill

```
[... same as 15.1 through PENDING ...]
M28  -> BP    : get_order_status(broker_order_id)
BP   -> Kite  : status query
Kite -> BP    : PARTIALLY_FILLED, executed_quantity < requested_quantity
BP   -> M28   : PARTIALLY_FILLED
M28  -> DB    : persist PARTIALLY_FILLED
M28  -> M28   : (wait for next polling invocation -- no new decision made here, per section 4's "Module 28 never creates a new trading decision")
M28  -> BP    : get_order_status(broker_order_id)   [next polling cycle]
BP   -> Kite  : status query
Kite -> BP    : FILLED, remaining quantity now filled
BP   -> M28   : FILLED
M28  -> DB    : persist FILLED -> RECONCILED (as 15.1)
M28  -> SE    : confirm_cycle_outcome(submitted_successfully=True)
```

### 11.3 Restart recovery (crash while PENDING, mandatory reconciliation on restart)

```
[process crashes here -- order was PENDING, last confirmed broker_order_id persisted]

[EC2 restarts M28]
M28  -> DB    : load persisted state -- finds an order at PENDING with a broker_order_id, no terminal confirmation
M28  -> BP    : get_open_orders()   [MANDATORY, unconditional -- Decision 1: broker reconciliation is always the source of truth, never an assumption]
BP   -> Kite  : open orders query
Kite -> BP    : (this order is filled, not open)
BP   -> M28   : order not open -- was it filled or cancelled? -> get_order_status(broker_order_id)
BP   -> Kite  : status query
Kite -> BP    : FILLED, executed_price, executed_quantity (this happened while M28 was down)
M28  -> DB    : persist FILLED -> RECONCILED
M28  -> SE    : confirm_cycle_outcome(submitted_successfully=True)
[normal operation resumes -- no duplicate order was ever submitted, because reconciliation ran BEFORE any new submission was considered]
```

### 11.4 Order expiry (unfilled, execution window closes)

```
[... PENDING, no fill reported across several polling invocations, execution window expires ...]
M28  -> BP    : cancel_order(broker_order_id)
BP   -> Kite  : cancel request
Kite -> BP    : cancelled, acknowledged
M28  -> DB    : persist CANCELLED (complete execution outcome recorded)
M28  -> BP    : get_open_orders()   [reconciliation, Decision 1]
BP   -> Kite  : open orders query
Kite -> BP    : (confirms not open)
M28  -> DB    : persist RECONCILED
M28  -> SE    : confirm_cycle_outcome(submitted_successfully=False)   [notify only -- no new price/quantity from M28]
SE   -> SE    : (next scheduled cycle) re-evaluate from current state -- own decision, not M28's
```

### 11.5 Failed submission

```
M28  -> DB    : persist SUBMITTED
M28  -> BP    : submit_order(order)
BP   -> Kite  : place order
Kite -> BP    : rejected (e.g. invalid order, exchange-side rejection)
BP   -> M28   : rejection reason
M28  -> DB    : persist FAILED (complete outcome including broker's rejection reason)
M28  -> BP    : get_open_orders()   [reconciliation -- confirms nothing was actually placed]
M28  -> DB    : persist RECONCILED
M28  -> SE    : confirm_cycle_outcome(submitted_successfully=False)
```

### 11.6 Successful completion, end to end (summary view)

```
SE -> M28 -> [VERIFIED] -> [SUBMITTED] -> BP -> Kite -> [PENDING] -> [FILLED] -> [RECONCILED] -> SE (confirm True)
      |                                                                              ^
      +---------------------------- DB persists every transition, before each external call --+
```

---

## 12. Testing Strategy

- **Unit:** order-lifecycle state machine transitions (all nine states including RECONCILED, hand-verified), affordability recomputation, whole-unit preservation through every adjustment.
- **Integration:** full flow from a real Strategy Engine `CycleResult` through verification through `PaperBrokerPort` simulated fill, using fakes for `LiveQuoteProvider` (no real network in this environment).
- **Regression:** locked baseline, same discipline as every prior phase.
- **Adversarial (separate pass, after implementation, per your established pattern):** crash at every lifecycle transition, idempotency under retry, reconciliation-on-restart with a simulated stale `PENDING` order, liquidity-check false positives/negatives, quantity-reduction-never-increase invariant under adversarial price/cash combinations.

---

## 13. Production-Readiness and Exit Criteria

Same RELEASE_POLICY.md structure as every phase: all tests pass, every defect has a regression test, zero frozen interfaces modified, documentation consistent, reproducibility verified, release metadata recorded.

**Additional criteria specific to this phase, per your explicit instruction — none of these are satisfied by a design review alone:**

- Live Kite sandbox or real API validation completed — every assumption in §20 (Broker Capability Matrix) is either verified against the real API or replaced with an implemented, tested fallback.
- Paper Trading completes **2-3 months of continuous operation** with no critical defects before Live Trading is considered.
- Recovery from crashes, restarts, and network failures is **demonstrated**, not just designed for — real induced-failure testing, not only unit tests against fakes.
- All operational scenarios in §19 (Failure Recovery Matrix) and §21 (Operations Runbook) are covered by automated tests, not only documented procedures.

This phase cannot be marked production-ready by documentation and design review alone, unlike every phase before it that had no real external dependency. That's a genuine, structural difference from Phases 2-6, not a formality being added for its own sake.

---

## 14. Summary of Items Requiring Your Explicit Decision

**All items resolved.** §4/§8.6 (expired-order handling, your exact seven-step sequence), §3 (BrokerPort, confirmed as the shared-lifecycle version), §8.2 (mandatory reconciliation, broker as source of truth), §8.3 (Paper Trading shares live Kite quotes), §8.4 (two narrow inline compliance checks only, no more), §8.7 (short-lived polling, WebSocket only if evidence later demonstrates polling is insufficient), §0 (no roadmap renumbering until this phase freezes). No open design questions remain from the original thirteen sections.

See §15-18 below for the additional deliverables requested alongside this approval, and §19 for the final architecture review checklist.

---

## 15. Failure Recovery Matrix

| # | Failure Point | Detection Method | Recovery Action | Data Source of Truth | Max User Impact | Test Case Reference |
|---|---|---|---|---|---|---|
| 1 | Crash before VERIFIED persisted | Restart finds no PROPOSAL/VERIFIED record for a known cycle_id | Strategy Engine's next cycle re-proposes (nothing was ever submitted) | State Store | None — nothing was ever sent to the broker | §11.1-3, crash-before-verification test |
| 2 | Crash after SUBMITTED persisted, before broker acknowledges | Restart + mandatory reconciliation (§8.2) finds no matching broker record | Treat as not submitted; safe to resubmit once, since the broker never actually received it | Broker `get_open_orders()` — authoritative | None if reconciliation correctly finds nothing; a real duplicate risk if reconciliation is skipped, which is exactly why it's mandatory | §11.3 |
| 3 | Crash after broker acknowledges, before PENDING persisted | Restart + reconciliation finds the order open at the broker but not reflected locally | Adopt the broker's record as truth; persist PENDING retroactively with the real broker_order_id | Broker | None — no duplicate, no lost order | §11.3 |
| 4 | Crash while PENDING, order fills during downtime | Restart + reconciliation query returns a terminal state for an order recorded as PENDING | Persist the real terminal state (FILLED/CANCELLED), skip straight to reconciliation | Broker | None — outcome was correct, just not yet recorded | §11.3 |
| 5 | Network interruption during a status poll | The poll call raises (timeout/connection error) | Log, do not change persisted state, retry on the next scheduled polling invocation | State Store (unchanged) | A delay in noticing a fill, never a duplicate or lost order | Phase 6's Kite-failure-exception-safety pattern, reused |
| 6 | Kite token expiry mid-cycle | Auth error from any Kite call | Halt this cycle's remaining steps, alert via NotificationPort, do not guess at a refreshed token | State Store (unchanged), `Notif` | Delayed execution until token is refreshed (manual or automated, §21) | §20's token-refresh row |
| 7 | Order expires unfilled | Execution window closes with order still PENDING | §4.1's exact seven-step sequence: cancel, record, persist, notify (False), never re-price | Broker (via cancel confirmation) + State Store | A missed cycle's investment, recoverable next cycle — never a wrong investment | §11.4 |
| 8 | Broker rejects the order outright | Synchronous rejection response from `submit_order()` | Persist FAILED with the broker's stated reason, reconcile, notify (False) | Broker's rejection response | Same as #7 | §11.5 |
| 9 | Liquidity check fails (wide spread / thin book) | `LiveQuoteProvider.get_market_depth()` against §8.5's thresholds | Do not submit this polling cycle; retry next cycle per policy; never convert to MARKET | `LiveQuoteProvider` | Delayed fill, never an unprotected price | Phase 6's LIMIT-only structural guarantee, extended here |
| 10 | Reconciliation itself fails (broker query errors) | Exception from `get_open_orders()`/`get_order_status()` during mandatory reconciliation | Do NOT proceed to any new submission until reconciliation succeeds — block, alert, retry | N/A — this is the block condition itself | Delayed operation until reconciliation succeeds; never a skipped reconciliation | New test required, §19 checklist item |
| 11 | EC2 instance terminated (power-failure-equivalent) | Same as restart, plus Phase 6's `PRAGMA synchronous=FULL` durability guarantee applied to this module's own state store | Same as restart-recovery rows above | State Store (durable) + broker reconciliation | None, given the same durability discipline already verified for Strategy Engine | Phase 6's power-failure durability test, same pattern applied here |

---

## 16. Broker Capability Matrix

**Every row here is a documented assumption, not a confirmed fact — this environment has no network access to Kite.** This matrix is the explicit tracking mechanism for closing that gap during implementation, not a substitute for actually closing it.

| Capability | Assumption Made in This Design | Verified? | Fallback if Assumption Is Wrong |
|---|---|---|---|
| Rate limits | Kite Connect enforces a request-per-second cap; `common/retry.py`'s exponential backoff handles throttling | **Unverified** | Tune backoff parameters once real limits are confirmed; no architectural change needed since retry logic is already parameterized |
| Order status transitions | Kite reports OPEN → (PARTIALLY_FILLED) → COMPLETE / CANCELLED / REJECTED, queryable by order ID | **Unverified** | State machine (§4) may need additional intermediate states if Kite's real transitions are more granular — isolated to the BrokerPort implementation, not the core state machine |
| Token refresh | Access tokens expire daily and require a manual or semi-automated re-auth flow (Kite Connect's documented behavior as of general knowledge, not confirmed against current docs) | **Unverified** | §21's runbook covers manual refresh; automating it is a possible follow-up, not a blocker |
| Quote latency | `get_last_traded_price()` returns within normal API latency (sub-second), acceptable for daily/event-driven use, not HFT | **Unverified** | If materially slower, affects §8.7's polling cadence tuning, not the architecture |
| Order modification | Assumed NOT used — this design cancels and lets Strategy Engine re-propose rather than modifying an existing order in place (consistent with §4.1's "never re-price") | **Unverified whether Kite even supports modification** — irrelevant to this design either way, since it's deliberately unused |
| Cancellation behavior | `cancel_order()` is synchronous and confirms before returning | **Unverified** | If asynchronous, §4's CANCELLED transition needs a confirmation-polling step added, same pattern as PENDING |
| Idempotency support | Unknown whether Kite accepts a client-supplied tag/order-id for de-duplication | **Unverified — this is the highest-impact unknown in the whole design** | §8.2's mandatory reconciliation is the fallback regardless of the answer — designed to work whether or not Kite provides native idempotency |
| Error responses | Assumed structured (parseable error codes/messages), not just opaque HTTP failures | **Unverified** | Retry/failure classification logic may need adjustment; doesn't change the state machine |

---

## 17. Operations Runbook

Procedural, for whoever operates this system — not a replacement for the automated recovery already designed into Module 28, but the manual steps for scenarios that need a human.

**Startup:** Scheduler (Phase 9, not yet built) invokes Module 28's entry point. Module 28 loads persisted state, runs mandatory reconciliation (§8.2/§15 row 2-4) before considering any new work, then proceeds normally. No manual step required for ordinary startup.

**Shutdown:** No special procedure — Module 28 is a short-lived, invoked-per-event process (§8.7/§10); it exits normally at the end of each invocation. There is no running process to gracefully stop.

**EC2 restart:** Identical to Startup — the mandatory reconciliation step is what makes this safe, not a restart-specific procedure. This is deliberate: "restart" and "normal startup" should be the same code path, not two.

**API outage (Kite unreachable):** Detected via connection/timeout errors on any Kite call. Module 28 halts the affected cycle, logs, and alerts via `NotificationPort` (best-effort — per Phase 6's established principle, a notification failure never blocks the underlying logic). No orders are submitted or assumed-submitted during an outage. Resumes automatically on the next scheduled invocation once Kite is reachable again — no manual intervention required unless the outage is prolonged enough to matter for that cycle's investment window.

**Network outage (local/EC2-side):** Same detection and response as API outage from Module 28's perspective — it cannot distinguish "Kite is down" from "we can't reach Kite," and doesn't need to; the response is identical either way.

**Token expiry:** Detected via an authentication error from any Kite call (§16's unverified assumption on refresh mechanics). Manual procedure until/unless automated refresh is implemented: operator re-authenticates via Kite's login flow, updates the stored token via `SecretsManager` (frozen, existing mechanism — no new secrets pathway). Module 28 resumes on its next scheduled invocation.

**Order reconciliation (manual invocation):** Available as an explicit, callable operation independent of the automatic restart-triggered reconciliation — for an operator who wants to verify Module 28's records against the broker on demand, not only after a crash.

**Disaster recovery:** State Store is a SQLite file with `PRAGMA synchronous=FULL` (Phase 6's durability pattern, applied here — see §15 row 11). Recovery from a lost EC2 instance means: provision a new instance, restore the state store file (standard backup/restore, outside this module's own scope — an infrastructure concern for Phase 11's AWS deployment work), and let the mandatory reconciliation step reconcile against the broker's authoritative record regardless of how stale the restored local file is. The broker is always the source of truth (Decision 1) — this is what makes disaster recovery tractable at all, rather than requiring perfect local backups.

---

## 18. Architecture Decision Records

One per major decision made across this design process, so future changes have to reckon with the original reasoning rather than silently drift from it.

**ADR-1: BrokerPort abstraction shared by Paper and Live**
Decision: one interface, two implementations, Module 28's own lifecycle logic has zero awareness of which one is active.
Rejected: branching logic inside Module 28 (`if paper_mode`). Rejected because it would leave Paper Trading unable to validate the very logic (state machine, retry, idempotency) it exists to validate.
Status: Confirmed (this conversation).

**ADR-2: Module 28 never re-prices after expiry/cancellation**
Decision: your exact seven-step sequence (§4.1) — cancel, record, persist, notify-only, Strategy Engine re-evaluates, Strategy Engine owns all new decisions, Module 28 never decides.
Rejected: Module 28 autonomously resubmitting at an adjusted price. Rejected because re-pricing is a trading decision, and this project's whole architecture (since §15/§16 of the Phase 1 amendments) has kept trading decisions and execution strictly separated.
Status: Confirmed (this conversation).

**ADR-3: Mandatory, unconditional reconciliation on every restart**
Decision: broker state is always the source of truth; never assume the previous local state is correct.
Rejected: conditional reconciliation ("only if something looks wrong"). Rejected because correctly judging "does something look wrong" is itself a failure-prone step in a system where the cost of guessing wrong is a duplicate real-money order.
Status: Confirmed (this conversation).

**ADR-4: Paper Trading shares live Kite read-only quote infrastructure**
Decision: `PaperBrokerPort` and `KiteLiveBrokerPort` both use `LiveQuoteProvider` against real Kite data; only order *placement* differs.
Rejected: a fully offline paper mode. Rejected because it would validate less — it wouldn't exercise `LiveQuoteProvider` at all before Live Trading depends on it.
Status: Confirmed (this conversation).

**ADR-5: Compliance is two narrow inline checks, not a compliance framework**
Decision: static IP verification and Algo ID tagging only, inline in Module 28; everything else waits for Module 24.
Rejected: building a minimal Module 24 now. Rejected (by your explicit choice) in favor of a hard scope boundary — these two checks and no more.
Status: Confirmed (this conversation).

**ADR-6: Pending-order monitoring via short-lived polling, not a resident process**
Decision: process starts, checks status, exits; repeated on a schedule.
Rejected (for now): a WebSocket-based resident listener, matching the Live Trading Engine's already-approved exception. Not rejected permanently — explicitly reopenable if real-world testing demonstrates polling latency is a real problem.
Status: Confirmed, conditionally revisitable (this conversation).

**ADR-7: `LiveQuoteProvider` is a new, additive interface — `DataProvider` is not modified**
Decision: live/current price data gets its own interface, since `DataProvider` (Phase 2, frozen) was built for historical OHLCV ingestion and was never meant to serve this need.
Rejected: extending `DataProvider` with a live-quote method. Rejected because it would mean modifying a frozen Phase 2 interface for a capability it was never designed to have, when an additive sibling interface achieves the same goal with zero risk to frozen code.
Status: Proposed, consistent with the same pattern used for `MarketIntelligencePort` in Phase 6 — not yet built, no code exists to confirm against.

**ADR-8: RECONCILED as an explicit terminal-confirmation state**
Decision: every terminal outcome (FILLED/CANCELLED/FAILED) passes through an explicit RECONCILED state once independently confirmed against the broker, rather than being trusted the moment Module 28 itself records it.
Rejected: treating FILLED/CANCELLED/FAILED as sufficient on their own. Rejected because Decision 1's "broker is always the source of truth" principle deserves a visible, auditable state in the lifecycle, not just an internal implementation detail of the reconciliation mechanism.
Status: Added per your instruction to include RECONCILED in the canonical state list.

---

## 19. Final Architecture Review Checklist

Each item checked against the actual design in this document, not asserted from memory.

**1. No frozen interfaces are modified.** Confirmed, §9 — every dependency in §2 is consumed as-is; the only new interface (`LiveQuoteProvider`) is additive.

**2. No circular dependencies exist.** Verified by tracing the dependency graph: Module 28 depends on Strategy Engine (v0.6), Phase 4 (`CostTaxEngine`, `OrderIntent`), Phase 5 (`RiskManagementEngine`), and Phase 2 (`SecretsManager`, `retry.py`) — all one-directional. None of those modules depend on Module 28. `BrokerPort`/`LiveQuoteProvider` are new interfaces Module 28 owns outright, with no dependents yet. No cycle exists in this design.

**3. Event-driven architecture is preserved.** Confirmed via §10, §17 (Runbook's Startup/Shutdown sections describe a process that starts, works, exits — never a resident loop), and ADR-6 — the one deliberate polling cadence is scoped and conditionally revisitable, not a silent exception to §17's platform-wide principle.

**4. EC2 Micro resource limits are respected.** **Actually verified this round, not assumed:** I checked whether `RiskManagementEngine` (Phase 5) and `CostTaxEngine` (Phase 4) — both newly consumed by a *live-instance* module for the first time in this design — load numpy or scipy. Neither does. This matters because Risk Management Engine was previously only ever called from the research-side (via Portfolio Optimizer); this is the first time it's been pulled onto the live path, and I didn't want to claim EC2 compatibility without actually checking that specific, new combination.

**5. AWS operating cost remains minimal.** Follows from 3 and 4 — short-lived polling processes with no heavy dependency, consistent with §17 (architecture)'s original cost motivation.

**6. Strategy Engine remains completely unaware of execution mode.** Confirmed structurally — Strategy Engine's only dependency is the abstract `CashLedgerPort` (frozen, v0.6), which has no `paper`/`live` concept anywhere in its signature. It cannot distinguish what it cannot see.

**7. Paper Trading and Live Trading remain interchangeable through BrokerPort.** Confirmed by §3's chosen design — Module 28's own order-lifecycle state machine (§4) is written entirely against `BrokerPort`, with both concrete implementations satisfying the identical interface. This is the stronger guarantee you approved, not just item 6 restated.

**8. Module 28 never creates investment decisions; it only executes Strategy Engine's decisions.** Confirmed by §4.1's seven-step sequence (your exact wording) and ADR-2 — every path in the state machine (§4) either executes a proposal exactly as given (possibly reduced in quantity per the one-directional verification constraint, never redirected to a different symbol or increased) or terminates and hands control back to Strategy Engine. No code path in this design computes a price, quantity, or symbol that didn't originate from Strategy Engine's proposal.

**Result: all eight items pass.** Module 28 design is marked complete, pending your explicit approval before any implementation code is written.
