# Milestone 6 Decision Document

**Status: DECISIONS PROPOSED, NOT YET IMPLEMENTED.** No `KiteBrokerPort` code has been written. One decision below (Decision 1) proposes a fix to `execution_manager` itself - not because Milestone 6 requires touching that code for Kite's sake, but because researching Kite surfaced a genuine, pre-existing defect in already-production-verified code. That distinction matters and is explained in full below.

---

## Decision 1 - Duplicate Order Risk

### This is not a Kite-specific problem. It is a real defect in Module 28 today.

Before proposing anything, I tested the exact scenario directly against the current, already-production-verified PaperBrokerPort and ReconciliationService - not theorized about it, ran it:

1. A record reaches SUBMITTED (persisted).
2. The broker call succeeds and the order **immediately fills** (IMMEDIATE_FILL scenario).
3. The process is interrupted before broker_order_id is recorded locally - the exact crash window Milestone 3's design exists for.
4. Reconciliation runs, as it would on any restart.

**Result, confirmed by direct execution, not inference**: `PaperBrokerPort.get_open_orders()` excludes the now-FILLED order (it's no longer open). Reconciliation's `broker_order_id is None` branch searches only that open-orders set, finds nothing, concludes `NEVER_REACHED_BROKER`, and reverts the record to VERIFIED for retry. **A real duplicate submission would occur on the next process_order() call.** This has been sitting in ReconciliationService since Milestone 3, undetected through the 100,000-cycle stress test, the 365-day and 2-year continuous operation validations, and the disaster recovery exercise - because none of those test suites happened to construct this exact precise timing (crash strictly between fill and local recording). Researching Kite's stricter constraints (no idempotency key, transient order book) is what surfaced it; the defect itself is independent of Kite entirely.

### Why get_open_orders() alone can never fix this

`PaperBrokerPort.get_open_orders()` filters to non-terminal orders by design (`state not in (FILLED, CANCELLED)`) - this mirrors the method's own name and BrokerPort's docstring ("the authoritative list of orders the broker currently considers open"). Redefining get_open_orders() itself to return everything would silently change an existing, tested, named contract and could break other callers relying on its current "open only" semantics (e.g. sweep_outstanding()'s open-orders bookkeeping). That's the wrong fix.

### What the real Kite API actually offers (reviewed directly, not assumed)

Kite's GET /orders endpoint documentation states explicitly: **"Retrieve the list of all orders (open and executed) for the day."** The example response fetched during the architecture review mixes CANCELLED, COMPLETE, and REJECTED statuses in a single response to this one endpoint - Kite does NOT have a separate "open orders only" endpoint the way this platform's get_open_orders() naming implies. A real KiteBrokerPort would have to *choose* to filter Kite's full daily order list down to "open only" to satisfy the existing interface's contract - the same self-inflicted narrowing PaperBrokerPort already does.

### Proposed fix

Add one new method to BrokerPort:

```
search_orders_by_reference(client_reference) -> list of orders, any status, within the broker's current retention window
```

- PaperBrokerPort implementation: search self._orders.values() unfiltered by client_reference, no status restriction.
- KiteBrokerPort implementation (future): call GET /orders (unfiltered, exactly as documented) and filter client-side by tag (see Decision 2 for the tag encoding).
- get_open_orders() is UNCHANGED - same name, same "open only" contract, same existing tests, same behavior for sweep_outstanding() and every other current caller.
- ReconciliationService._reconcile_one()'s broker_order_id is None branch is changed to call search_orders_by_reference() instead of (or in addition to, as a fallback after) checking open_at_broker - this is the actual fix. The NEVER_REACHED_BROKER conclusion is only reached if this broader search also finds nothing.

### Why this is a disclosed interface addition, not scope creep

This adds one new abstract method to BrokerPort and one behavioral change to ReconciliationService - both inside execution_manager, which the original Milestone 6 instruction said not to architecturally change *for Kite's sake*. This proposal is not for Kite's sake - it fixes a defect that exists today, independent of Kite, proven by direct test against PaperBrokerPort. This is the exact class of exception this entire project has consistently applied: frozen/stable code can be touched when a genuine defect is found, not worked around. I have not implemented this yet - it needs your explicit sign-off before any code changes, given it touches code three prior milestones already treated as production-verified.

### Recommendation

Fix this in execution_manager BEFORE any KiteBrokerPort work begins, as its own small, focused, thoroughly-tested change - independent of and prior to Kite integration, since the defect exists with or without Kite. Waiting for KiteBrokerPort to "surface" this properly would mean shipping live-money code on top of a reconciliation layer already known to be unsafe.

---

## Decision 2 - Tag Length

### Constraint, precisely

Kite's tag field: **alphanumeric only, maximum 20 characters** (confirmed from official docs). This platform's real, production cycle_id format (strategy.py): `f"{current_month}-{capital_source.value}"`, e.g. `"2026-07-recurring_monthly"` - 26 characters, and contains hyphens and underscores, which are not alphanumeric. Both the length and the character set fail Kite's constraint; truncation alone is insufficient.

### Proposed design

```
tag = hex(SHA-256(cycle_id))[:20]
```

- **Deterministic**: SHA-256 is a pure function of its input - the same cycle_id always produces the same tag, every time, on every machine, with no dependency on runtime state.
- **Collision resistant**: 20 hex characters is 80 bits of the underlying 256-bit hash - for this platform's realistic order volume (low hundreds to low thousands of orders per year, per every long-duration validation performed so far), the probability of two different cycle_id values colliding on the first 20 hex characters is negligible, not a real operational risk.
- **Alphanumeric-only**: hex digits (0-9a-f) satisfy Kite's character-set constraint exactly; no further encoding needed.
- **Stable across restart**: depends only on cycle_id, never on wall-clock time, process state, or anything that would differ between a first attempt and a post-crash retry - the same order retried after a restart produces the identical tag.
- **Reversible, via a lookup table, not pure computation**: a SHA-256 hash cannot be inverted mathematically, but "reversible" in the operationally meaningful sense (recovering the original cycle_id from a tag seen at the broker) is achieved by persisting a small tag -> cycle_id mapping at the moment a tag is generated (i.e., at submission time), queryable during reconciliation and for audit. This requires one new small table in ExecutionStateStore (or a lightweight sidecar file, consistent with EventArchive's pattern) - a genuinely new, small persistence addition, flagged here rather than silently added.
- **Suitable for audit**: the mapping table itself is the audit bridge between a Kite tag and the platform's own cycle_id/execution_id. This is in addition to, not instead of, ExecutionRecord's own existing cycle_id field, which remains the primary audit-trail key once a broker_order_id is known - the tag/hash mapping is specifically for the narrow window where only the tag is available (the crash-recovery search in Decision 1).

### What this does not resolve

Whether Kite's tag field is server-side filterable in GET /orders, or requires a full client-side scan of the day's order list, is not documented - flagged in Decision 5. Given this platform's low order volume, a full scan is acceptable either way; this affects implementation efficiency, not correctness.

---

## Decision 3 - Authentication: Operational Implications, Documented

Per your instruction, no browser automation or credential workaround will be built. This is a firm design constraint, not a placeholder for a future automation effort.

**What is confirmed, directly from Kite's own documentation and support forum:**

- access_token expires daily - Kite's own docs state it **"will expire at 6 AM the next day (regulatory requirement)"** unless explicitly logged out first. Some inconsistency exists in operator-reported exact expiry time (6:00-7:30 AM across different forum reports), but the regulatory framing is unambiguous.
- refresh_token exists but is documented as **"only available to certain approved platforms"** - not standard developer-tier access, and this platform has no basis to assume it qualifies.
- The login flow requires a human completing a browser redirect and 2FA. This is an exchange/regulatory requirement, not a Kite Connect API limitation Kite could relax even if asked.

**Operational implications, stated plainly:**

- **Live trading cannot run fully unattended across a calendar-day boundary.** A human must complete the login flow once per trading day before KiteBrokerPort can be used that day. This is a materially lower "continuous operation" ceiling than what Milestones 5B/Production Verification validated for paper trading (which ran unattended for a simulated 2 years with zero human intervention).
- KiteAuthManager (the proposed component from the architecture review) must **fail loudly and immediately** when no valid token is available - surfacing a clear "re-authentication required" state through the existing alert-handling framework (OPERATIONAL_RUNBOOK.md Section 7), never silently retrying against a dead token, never attempting to work around the missing token.
- The Operational Runbook needs a live-trading-specific addition to its Startup section: the daily login step, who performs it, and by what time it must be done relative to intended trading hours - an operational procedure, not something this decision document can fully specify without knowing the actual operating team's schedule.
- Any outage or delay in completing the daily login effectively pauses live trading for that day - this should be treated as expected, normal behavior (a controlled pause), not a system failure requiring investigation, though it should still be logged/alerted so it's a *visible*, tracked pause rather than a silent one.

---

## Decision 4 - Cash Source

### The ambiguity, stated plainly

GET /user/margins/equity exposes at least two candidate fields for "available cash": net (computed as intraday_payin + adhoc_margin + collateral, per the API's documented field semantics) and available.live_balance ("current available balance"). Kite's documentation does not state these are equivalent, and does not explain when they might diverge.

### Operational impact of getting this wrong

VerificationService's affordability check (Module 28, Milestone 3) uses this figure as the hard ceiling for how much can be committed to a new order. If the chosen field overstates real available cash, an order could be verified as affordable and then rejected at the broker for insufficient margin - a real, if recoverable, operational failure (Module 28's RejectionReason.INSUFFICIENT_CASH / broker-side MarginException handling already exists for this, but it's a worse outcome than never submitting the order at all). If the chosen field understates real available cash, the platform simply deploys slightly less capital than it could have - a strictly safer failure mode.

### Recommendation

**Use net as the authoritative field.** The asymmetry in failure modes favors conservatism: an order that's slightly smaller than it could have been is a minor inefficiency; an order that gets rejected after being verified as affordable is a worse, more confusing operational outcome. This recommendation is not yet confirmed against a real account - see Decision 5's validation plan for how it will be checked.

---

## Decision 5 - Remaining Unknowns

| # | Unknown | Impact if wrong | How it will be validated |
|---|---|---|---|
| 1 | Real network failure behavior under actual internet conditions (timeouts, partial responses, connection resets) | Module 28's crash-recovery design is tested against *simulated* failures; real-world failure modes may not match the simulator's assumptions | First-week live operation (smallest possible order sizes) with enhanced logging on every submit_order/get_order_status call's actual latency and failure shape, reviewed before scaling to normal operation |
| 2 | Whether cancel_order is safe to call twice | If not idempotent, a retried cancellation could have an unexpected side effect | One deliberate, minimal-size test order cancelled twice during initial live verification, observing the actual API response both times, before this path is ever exercised automatically |
| 3 | net vs available.live_balance (Decision 4) | Covered above | A single manual cross-check: compare the API's net value against the account's own "available for trading" figure as shown in Kite's own web/app UI, at the same moment, once, before relying on the field automatically |
| 4 | Whether Kite's tag is server-side filterable in GET /orders, or requires a full client-side scan | Affects implementation efficiency only, not correctness, given this platform's low order volume | Direct inspection of GET /orders's query parameters during initial API exploration; not blocking, since a full scan is an acceptable fallback |
| 5 | The guid field's actual purpose (client-suppliable idempotency key, or purely server-generated/informational) | If it turns out to be a genuine client-supplied idempotency key, it would be a materially better mechanism than Decision 1's tag-based search, and worth adopting instead | Direct experimentation against a real account/sandbox: attempt to supply a guid on order submission and observe whether Kite honors it or ignores it |
| 6 | Real behavior of the original 8 PHASE7_Objectives.md Broker Capability Matrix assumptions | This review has already resolved most of them (idempotency: confirmed absent; static IP: confirmed mandatory since April 1, 2025; rate limits: confirmed with a disclosed historical inconsistency) | Any still open after this document will be validated incrementally during a phased, small-order-size initial live verification period before normal operation begins - not assumed resolved by documentation alone |

---

## Summary - What's Being Asked For

- **Decision 1** proposes an execution_manager fix (one new BrokerPort method, one ReconciliationService behavioral change) for a defect that exists today, independent of Kite - needs your explicit approval before implementation, since it touches code three prior milestones already validated.
- **Decision 2**'s tag-encoding design needs your confirmation before it's built.
- **Decision 3** requires no further action beyond acknowledging the operational constraint - no code proposed.
- **Decision 4**'s net recommendation needs your confirmation, or you may prefer to wait for the real-account validation in Decision 5 item 3 before deciding.
- **Decision 5** is a validation plan, not a set of resolved facts - presented for your awareness, not requiring a decision beyond agreeing the plan is adequate.

**No KiteBrokerPort code has been written. No execution_manager code has been changed.** Awaiting your review before either begins.
