# DDR-001: Reconciliation Duplicate-Order Defect

**Status: PROPOSED. No code changed.** This DDR reverses the recommendation in the prior Decision Document (which proposed a new BrokerPort method) after the ordered investigation you requested. The conclusion changed because the investigation was real, not because the prior answer was reconsidered for its own sake.

---

## Root Cause

`ReconciliationService._reconcile_one()`'s handling of a record with `broker_order_id is None` matches against `open_at_broker` (built from `get_open_orders()`, which both PaperBrokerPort and any future KiteBrokerPort correctly filter to non-terminal orders only, by design). If a record's order actually reached the broker and **already resolved to a terminal state** (filled, cancelled, or rejected) before reconciliation runs, it will never appear in that filtered set. Finding no match, reconciliation concludes `NEVER_REACHED_BROKER` and reverts the record to `VERIFIED`, which the orchestrator will then automatically retry - submitting a genuine second order for a transaction that already completed.

Confirmed by direct execution against PaperBrokerPort (not inferred): this is real, present in Module 28 today, and predates any Kite-specific work.

---

## Investigation, In the Order Requested

### 1. Can the existing broker_order_id lifecycle be strengthened?

Checked the actual code (orchestrator.py's `_do_submit`). The persistence step that records broker_order_id already happens **immediately** upon submit_order() returning successfully - there is essentially no meaningful gap left to tighten in that specific sub-case; the current design is already close to optimal there.

**But this only helps the sub-case where submit_order() returns successfully and the process then crashes before the following, already-immediate write completes.** It does nothing for the more realistic and more dangerous sub-case: submit_order() **never returns at all** - a network timeout, a connection drop, a process kill mid-call. In that case there is no broker_order_id in memory, at any point, for any persistence strategy to save faster. Kite's own documentation explicitly warns this is normal, expected behavior: "Successful placement of an order via the API does not imply its successful execution... a network/timeout error does not mean the order wasn't received."

**Conclusion**: strengthening the lifecycle helps marginally, for a narrow sub-case that's already well-handled. It does not resolve the core defect, which lives in the "broker received it, we never found out" case - the exact case reconciliation exists to handle, and currently mishandles.

### 2. Can existing order-history retrieval already available through BrokerPort solve the problem?

BrokerPort has five methods. get_order_status(broker_order_id) requires already knowing the ID - useless for finding a lost order. get_open_orders() is the method already implicated in the defect (correctly excludes terminal orders by its own design). There is no way to enumerate "everything, including terminal orders" using only these two methods: one needs an ID we don't have, the other structurally excludes the very orders we're looking for.

**Considered and rejected**: repeatedly polling get_open_orders() more frequently, hoping to catch an order before it turns terminal, and persisting a local "last seen open" snapshot to consult on restart. This is probabilistic, not reliable - it depends on winning a race against however fast the order actually fills, which for a MARKET-speed fill (or PaperBrokerPort's IMMEDIATE_FILL scenario) can be effectively instantaneous. This does not meet the bar of resolving the defect; it only reduces how often it's hit.

**Conclusion**: no combination of currently-available BrokerPort methods, called any differently, reliably solves this.

### 3. Can reconciliation use a different state transition strategy without changing BrokerPort?

**Yes - and this is the finding that changes the recommendation.**

The defect isn't that reconciliation *can't* determine the truth in the ambiguous case - it's that reconciliation currently **guesses** in the ambiguous case, and guesses in the unsafe direction (assume-safe-to-retry) when it cannot actually confirm safety. The fix doesn't require a new capability; it requires reconciliation to stop guessing.

**Proposed policy change**, entirely inside ReconciliationService._reconcile_one(), no BrokerPort change:

When broker_order_id is None and no match is found in open_at_broker, **do not** revert to VERIFIED for automatic retry. Instead, classify this as a new, explicit outcome - call it AMBIGUOUS_NO_LOCAL_ID - and leave the record exactly where it is (SUBMITTED), flagged for manual review through the existing NotificationPort/alert-handling path (OPERATIONAL_RUNBOOK.md Section 7), the same way BROKER_HAS_NO_RECORD already is today. No automatic retry occurs until a human confirms, by whatever means available to them (the broker's own web/app order history, contract notes, direct support contact - sources with visibility this platform's own API access doesn't have), what actually happened.

This is a **strictly more conservative** policy than what exists today. It cannot produce an automated duplicate order, because it never automatically retries an order whose fate is unconfirmed. The cost is automation: cases that genuinely never reached the broker (truly safe to retry) now also wait for manual confirmation, rather than being auto-resolved. Given this platform's real order volume - a handful of trades per month, confirmed across every long-duration validation performed so far - this cost is small and bounded, not a scalability concern.

---

## Alternative Solutions Considered

| # | Approach | Resolves the defect? | Cost |
|---|---|---|---|
| A | Tighten broker_order_id persistence timing only (Investigation 1) | No - leaves the "never received a response at all" case, which is the realistic one, completely open | Low, but insufficient alone |
| B | New BrokerPort method, search_orders_by_reference (the prior Decision Document's proposal) | Yes, fully, with full automation preserved in both sub-cases | New interface surface; must be implemented and tested in PaperBrokerPort and (later) KiteBrokerPort; a genuinely new capability added to a previously-stable interface |
| C | Conservative reconciliation policy change (Investigation 3) - **recommended** | Yes, fully, for the specific safety property that matters (no automated duplicate) | Reduced automation for the ambiguous case; requires a reliable human-alerting path (already built, NotificationPort) and a documented manual-review procedure (new Runbook section) |
| D | Do nothing, accept the risk | No | Unacceptable - this is a real defect with real-money consequences once live trading begins |

---

## Trade-offs

**Option B (new method) vs. Option C (policy change), the real choice:**

- **Automation**: B fully automates recovery in every case. C requires a human for the ambiguous case specifically. Given this platform's low order volume, C's automation cost is small in absolute terms - a few manual reviews per month at most, in the case where a crash happens to land in exactly this narrow window (itself already a low-probability event).
- **Change surface**: B touches BrokerPort (a previously-stable, cross-cutting interface) and requires new logic in every implementer, present and future. C touches only ReconciliationService's internal decision logic - one file, one method, no interface change, nothing new for KiteBrokerPort to implement later.
- **Verification burden**: B needs new tests proving the new method behaves correctly against both PaperBrokerPort and eventually a real Kite response shape - two things to get right. C needs new tests proving the ambiguous case is now flagged rather than auto-retried - one behavior, in one place, against infrastructure already thoroughly tested.
- **Correctness bar**: C's safety property (no automated duplicate) is unconditionally true by construction - there's no code path left that auto-retries an unconfirmed order. B's safety property depends on search_orders_by_reference being implemented correctly against Kite's real, only-partially-verified retention behavior - a real API integration is inherently harder to fully verify than a policy change confined to this platform's own code.
- **What's lost**: C means live trading will occasionally require a human to manually confirm an order's fate - a real, disclosed operational cost, not a hidden one. This should be weighed against B's benefit of full automation, which is genuinely valuable but not necessary to close the safety gap this DDR exists to address.

---

## Recommended Solution

**Option C: the conservative reconciliation policy change.** It fully resolves the safety defect (root cause), requires no BrokerPort interface change, and is smaller, more contained, and easier to verify with confidence than Option B. Per your explicit instruction to exhaust the existing interface before proposing something new - Option C succeeds at that; a new BrokerPort method is not, in fact, required.

This does not mean Option B is wrong or should never be built. If, once live trading is operating, the ambiguous case turns out to occur often enough that manual review becomes a genuine operational burden (which nothing in this platform's validated order volume suggests it will), Option B remains available as a future enhancement - implemented then, with real operational data justifying it, rather than spent now against a hypothetical.

---

## Architectural Impact

- BrokerPort: **unchanged**. Zero impact on PaperBrokerPort, and zero new requirement for KiteBrokerPort.
- ReconciliationService: one new DiscrepancyType value (AMBIGUOUS_NO_LOCAL_ID), one changed branch in _reconcile_one() (no longer reverts to VERIFIED automatically; instead persists the record unchanged and emits a distinct, alertable event).
- NotificationPort: no interface change - this reuses the existing manual-review alerting path already established for BROKER_HAS_NO_RECORD.
- OPERATIONAL_RUNBOOK.md: needs a new entry under Section 4 (Recovery) and Section 7 (Alert Handling) describing this specific scenario and the manual-confirmation procedure - documentation work, not code.
- Strategy Engine: **unaffected**. This is entirely contained within Module 28's reconciliation logic.

---

## Backward Compatibility

- Every existing test that exercises the broker_order_id is None-and-found case (client-reference matching succeeds) is unaffected - that path doesn't change.
- Every existing test that exercises the confirmed-never-reached-broker case needs review: the prior behavior (auto-revert to VERIFIED) is being deliberately removed for the case where the search comes up empty. **This is expected to require updating a subset of Milestone 3's crash-recovery tests**, since some of them currently assert the auto-retry behavior this DDR proposes to remove. That's a real, disclosed cost of this change, not a hidden one - those tests encoded the *old*, less-safe policy as correct; they'll need to be rewritten to assert the *new* policy (flagged for review, record left in place) instead.
- No database schema change - AMBIGUOUS_NO_LOCAL_ID is a new enum value in application code, not a new column.

---

## Implementation Plan (pending your approval of this DDR)

1. Add AMBIGUOUS_NO_LOCAL_ID to DiscrepancyType.
2. Change ReconciliationService._reconcile_one()'s no-match branch: stop reverting to VERIFIED; emit the new discrepancy type via the existing NotificationPort alert path instead.
3. Update the subset of Milestone 3's crash-recovery tests that currently assert the old auto-retry behavior for this specific case, replacing the assertion with "record is left unchanged, a distinct alertable event is emitted."
4. Add a new, explicit regression test reproducing the exact scenario this DDR's root-cause section describes (crash-after-fill-before-recording), proving it no longer results in an automatic duplicate submission.
5. Re-run the full existing suite (710 tests) to confirm nothing else regresses.
6. Update OPERATIONAL_RUNBOOK.md Sections 4 and 7 with the new manual-review procedure.
7. Re-verify git diff v0.6 --stat remains empty for every frozen package (this change is entirely inside execution_manager, already unfrozen/actively-developed code, but the check should still be run as a matter of discipline).

**Estimated effort**: small - one new enum value, one changed method, a handful of test updates, one documentation addition. Materially smaller than Option B would have been (no new interface method, no new implementer-side work for a future KiteBrokerPort).

Awaiting your approval before any of the above is implemented.
