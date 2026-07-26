# Live Validation Checklist

Each item below assumes the blockers in the Live Readiness Report (NotificationPort implementation, live entry point, SecretsManager wiring) are resolved first - this checklist validates the *platform's behavior*, not a substitute for closing those gaps. All procedures assume minimal position sizes throughout.

---

## 1. Kite Authentication

**Objective**: Confirm the manual login flow produces a usable session.
**Procedure**: Complete the interactive browser login (with 2FA) manually; capture the resulting request_token; call KiteAuthManager.compute_checksum() and exchange it via POST /session/token.
**Expected result**: A valid access_token is returned and successfully set via KiteAuthManager.set_session().
**Failure criteria**: Checksum rejected, or exchange fails for any reason.
**Rollback procedure**: No system state changes at this step - simply retry the login flow. Nothing to roll back.

## 2. Token Generation

**Objective**: Confirm the checksum computation matches what Kite expects in practice, not just what the documentation describes.
**Procedure**: Compare the checksum this platform computes against a manually-verified reference computation (e.g. via curl and a manual SHA-256 tool) for the same inputs.
**Expected result**: Identical values.
**Failure criteria**: Any mismatch - indicates a real bug in KiteAuthManager.compute_checksum() despite passing unit tests against mocked expectations.
**Rollback procedure**: Do not proceed to any further live step. Investigate the checksum function directly.

## 3. Session Expiry

**Objective**: Confirm real access_token expiry behavior matches this platform's assumptions (no automated recovery expected, per Decision 3).
**Procedure**: Allow a valid session to age past its expected expiry window; attempt a KiteBrokerPort call.
**Expected result**: A TokenException/403 is returned by Kite, correctly caught and re-raised as BrokerCommunicationError (not silently retried).
**Failure criteria**: Any silent retry against the dead token, or any exception type other than BrokerCommunicationError reaching the caller.
**Rollback procedure**: Re-authenticate manually (Item 1). No trading state to roll back - this test should occur with no open positions.

## 4. Available Cash Verification

**Objective**: Resolve the net vs available.live_balance ambiguity (Decision 4) against a real account.
**Procedure**: Call get_available_cash(); separately check the account's own displayed "available for trading" figure via Kite's web/app UI at the same moment.
**Expected result**: The two figures are either equal or the discrepancy is understood and small.
**Failure criteria**: A large, unexplained discrepancy - indicates Decision 4's conservative choice may need revisiting.
**Rollback procedure**: N/A - read-only check, no state change.

## 5. Holdings Retrieval

**Objective**: Confirm get_holdings() returns real, sensible data.
**Procedure**: Call get_holdings(); cross-check against the account's actual current holdings via Kite's own UI.
**Expected result**: Matching holdings list.
**Failure criteria**: Missing positions, incorrect quantities, or a malformed response this platform doesn't handle gracefully.
**Rollback procedure**: N/A - read-only check.

## 6. Positions Retrieval

**Objective**: Confirm get_positions() returns real, sensible data.
**Procedure**: Same as Item 5, for get_positions().
**Expected result / Failure criteria / Rollback**: Same as Item 5.

## 7. Place One Small ETF LIMIT BUY Order

**Objective**: The first real order this platform ever places. Minimal size, deliberately.
**Procedure**: Submit one LIMIT BUY order for the smallest sensible quantity (e.g. 1 unit) of a liquid ETF (e.g. NIFTYBEES), at a limit price with a real chance of filling within the session.
**Expected result**: A broker_order_id is returned; the order appears in Kite's own order book.
**Failure criteria**: Any exception not matching the documented error taxonomy; any broker_order_id that doesn't correspond to a real order visible in Kite's UI.
**Rollback procedure**: If the order is still open and unwanted, cancel it. If filled and unwanted, this is a real position - sell manually via Kite's own UI or app, outside this platform (this platform has no sell capability by design, per the frozen manual-selling rule).

## 8. Verify Broker Acknowledgement

**Objective**: Confirm get_order_status() correctly reflects the real order's actual state.
**Procedure**: Poll get_order_status(broker_order_id) for the order from Item 7.
**Expected result**: The returned OrderLifecycleState matches the order's real status as shown in Kite's UI, derived correctly via the status mapping table.
**Failure criteria**: Any mismatch between this platform's reported state and Kite's actual displayed state.
**Rollback procedure**: N/A - read-only check on an order already placed under Item 7's own rollback plan.

## 9. Verify Order History

**Objective**: Confirm get_order_history() returns the complete, correctly-ordered interim status sequence for a real order.
**Procedure**: Call get_order_history(broker_order_id) for the Item 7 order after it reaches a terminal state.
**Expected result**: A sequence of KiteOrderView entries matching the real progression Kite's own order-history UI shows for that order.
**Failure criteria**: Missing interim states, or a sequence that doesn't match Kite's own displayed history.
**Rollback procedure**: N/A - read-only check.

## 10. Verify Trade History

**Objective**: Confirm the trades generated by the Item 7 order can be retrieved and are correct.
**Procedure**: Query Kite's trades endpoint (not currently wrapped by KiteBrokerPort, but reachable via the underlying KiteHTTPClient directly for this validation step) for the order from Item 7.
**Expected result**: Trade record(s) matching the actual filled quantity and price.
**Failure criteria**: Missing or incorrect trade data.
**Rollback procedure**: N/A - read-only check.

## 11. Verify Audit Log

**Objective**: Confirm the complete event history for the Item 7 order is reconstructable end-to-end, against a real order for the first time.
**Procedure**: Use `EventArchive.reconstruct_by_cycle_id()` for the order's cycle_id; confirm a non-empty, chronologically-ordered event sequence.
**Expected result**: Full, reconstructable history, matching the guarantee already proven against PaperBrokerPort (Production Verification).
**Failure criteria**: Any gap in the reconstructed history for this real order.
**Rollback procedure**: N/A - read-only check. If a gap is found, this is a genuine defect requiring investigation before any further live orders.

## 12. Verify Reconciliation

**Objective**: Confirm `ReconciliationService` correctly reconciles a real order against real broker state.
**Procedure**: Run `reconcile()` after the Item 7 order reaches a terminal state; confirm it correctly reports `NO_DISCREPANCY` (or the appropriate outcome) and, if applicable, advances the record to `RECONCILED`.
**Expected result**: Correct classification, matching local and broker state.
**Failure criteria**: Any discrepancy that shouldn't exist, or a classification that doesn't match reality.
**Rollback procedure**: N/A - read-only reconciliation check.

## 13. Verify Telegram Alerts

**Objective**: Confirm alerts actually reach a human — the item this entire checklist exists to eventually make meaningful, given the Live Readiness Report's Section 7 finding.
**Procedure**: **Blocked until a real `NotificationPort` implementation exists** (see Live Readiness Report). Once built: trigger a test alert (e.g. by manually constructing an `AMBIGUOUS`-equivalent test scenario, or a deliberate low-stakes notification) and confirm it is received via the real channel within an acceptable delay.
**Expected result**: The alert is received, readable, and actionable.
**Failure criteria**: No alert received, a delayed or malformed alert, or any alert requiring the recipient to already know internal system details to act on it.
**Rollback procedure**: N/A - this item cannot be completed until the underlying gap is closed. Do not proceed past this item to any AMBIGUOUS-dependent live scenario (Item 16) until it passes.

## 14. Verify Crash Recovery

**Objective**: Confirm the platform recovers correctly from a real process interruption during real, live order handling.
**Procedure**: With a real order in flight (ideally still `PENDING`), deliberately terminate the running process; restart it; confirm mandatory reconciliation runs and correctly resolves the order's true state.
**Expected result**: Clean recovery, matching the disaster recovery exercise's proven behavior (33/33 recoveries, Production Verification) — now against a real broker for the first time.
**Failure criteria**: Any failure to recover, any incorrect state adopted, any duplicate action taken.
**Rollback procedure**: If recovery fails, do not resume automated operation. Manually verify the order's true state via Kite's own UI and correct the local record by hand if needed before restarting.

## 15. Verify Duplicate-Order Protection

**Objective**: Confirm the DDR-001 safety guarantee holds against a real broker, not just PaperBrokerPort.
**Procedure**: Deliberately reproduce the exact scenario DDR-001's regression test covers — interrupt the process between a real order's fill and the local recording of its broker_order_id — and confirm reconciliation escalates to `AMBIGUOUS` rather than retrying.
**Expected result**: `AMBIGUOUS`, not a duplicate order. This is the single most safety-critical validation step in this entire checklist.
**Failure criteria**: A duplicate order is created. **If this occurs, halt all live operation immediately and treat it as a critical defect, not a one-off anomaly.**
**Rollback procedure**: If a duplicate is created, manually reconcile the account's real position via Kite's own UI (likely selling the unintended duplicate, done manually outside this platform), and do not resume live operation until the root cause is understood and fixed.

## 16. Verify AMBIGUOUS Workflow

**Objective**: Confirm the full operator workflow — report generation, manual investigation, `resolve_ambiguous_execution()` — works end-to-end against a real `AMBIGUOUS` escalation.
**Procedure**: Using the scenario from Item 15 (if reproduced) or a similar deliberately-constructed one, generate the operator report (`generate_ambiguous_execution_report()`), manually investigate via Kite's own UI, and call `resolve_ambiguous_execution()` with the confirmed outcome.
**Expected result**: The report is accurate and actionable; resolution correctly updates the record to the confirmed state with the operator's notes persisted.
**Failure criteria**: An inaccurate or unhelpful report; a resolution that doesn't correctly reflect what was actually confirmed.
**Rollback procedure**: If resolution was incorrect, the record can be manually corrected in the database directly (an explicit, logged, exceptional action — not a normal operating procedure).

## 17. Verify Restart Recovery

**Objective**: Confirm ordinary (non-crash) restart — e.g. for a routine deployment update — behaves identically to crash recovery.
**Procedure**: Perform a graceful shutdown and restart with no order in flight; confirm mandatory reconciliation still runs and reports a clean state.
**Expected result**: Clean startup, no discrepancies reported (since nothing was in flight).
**Failure criteria**: Any discrepancy reported despite no order having been in flight, or reconciliation failing to run at all.
**Rollback procedure**: N/A - if this fails, do not proceed to Item 18 or resume live operation until understood.

## 18. Verify Deployment Rollback

**Objective**: Confirm a deployment can be safely rolled back to a previous version without corrupting state.
**Procedure**: Deploy a new version, then deliberately roll back to the prior one; confirm the database and archive files remain valid and reconciliation runs cleanly under the rolled-back version.
**Expected result**: Clean rollback, no data loss, no corruption.
**Failure criteria**: Any schema mismatch, any data loss, any corruption detected via `PRAGMA integrity_check`.
**Rollback procedure**: This item is itself testing rollback — if the rollback procedure fails, that is the finding. Do not attempt live trading until a genuinely reliable rollback procedure exists and is proven.
