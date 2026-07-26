# Live Trading Operational Runbook

This supplements the existing OPERATIONAL_RUNBOOK.md (paper trading) with live-trading-specific procedures. Where a procedure depends on infrastructure that does not yet exist in this repository, that is stated explicitly rather than described as if it were ready.

## 1. Daily Startup

**Cannot currently be performed as an automated or even semi-automated procedure - no live-operation entry point exists (Live Readiness Report, Section 9; Risk Assessment, Risk 2).** Once built, the intended sequence, consistent with this platform's established design principles throughout:

1. Verify frozen-module integrity (git diff against every tagged freeze point) before starting anything - established practice from every prior milestone.
2. Complete manual authentication (Section 2 below) - must happen before any KiteBrokerPort call.
3. Open the ExecutionStateStore database; a DatabaseCorruptionError here means stop immediately (existing Recovery procedure in OPERATIONAL_RUNBOOK.md).
4. Run mandatory reconciliation, unconditionally, before any new order activity - this is not a live-specific addition, it is the same rule already established for paper trading, now with real consequences.
5. Confirm the event archive and cycle-log archive paths are writable and on durable storage.

## 2. Authentication

**Verified**: KiteAuthManager correctly fails loudly (KiteAuthenticationRequiredError) with no session and has no method capable of auto-generating one - confirmed by direct test (test_kite_auth_manager_fails_loudly_with_no_session, test_kite_auth_manager_never_auto_generates_a_session).

**Procedure** (manual, daily, per the architecture review's Decision 3 - not automatable and not something this platform should ever attempt to automate):
1. A human completes the interactive browser login with 2FA.
2. The resulting request_token is captured and exchanged via KiteAuthManager.compute_checksum() + the token endpoint.
3. KiteAuthManager.set_session() is called with the real access_token.

**Not yet built**: any tooling to streamline steps 2-3, or to remind an operator this must happen before market open each trading day. This is currently a fully manual, undocumented-in-tooling process beyond what KiteAuthManager itself provides.

## 3. Monitoring

The existing OPERATIONAL_RUNBOOK.md Section 6 metrics (restart frequency, reconciliation mismatch rate by DiscrepancyType, duplicate submission count, database growth rate, memory trend) all remain applicable to live operation and are unchanged by this milestone.

**New for live operation, not yet built**: there is no monitoring dashboard, no automated metric collection pipeline, and (per Risk 1) no alerting channel to notify anyone when a metric crosses a concerning threshold. "Monitoring" today means an operator manually querying the database and log files - functional for validation-phase, minimal-order-volume operation, not a scalable production monitoring solution.

## 4. Handling AMBIGUOUS Executions

The workflow itself is built and tested (DDR-001): generate the report via generate_ambiguous_execution_report(), investigate via Kite's own UI/contract notes, resolve via resolve_ambiguous_execution() with mandatory operator notes.

**What is honestly missing**: the trigger. Per Risk 1, no real alert is currently generated anywhere a human would see it. Until a real NotificationPort exists, "handling" an AMBIGUOUS execution requires an operator to have proactively checked the database and noticed one exists - there is no push notification, no dashboard flag, nothing that surfaces this state on its own. **This is the single most important gap to close before live trading**, since it is the delivery mechanism for this platform's core safety guarantee.

## 5. Reviewing Logs

common/logging_setup.py provides RotatingFileHandler-based logging (confirmed by direct inspection) - log files exist and won't grow unbounded. Reviewing them today means direct file inspection; no log aggregation, search tooling, or structured-query capability exists beyond what standard Unix tools (grep, tail) provide.

## 6. Restart Procedures

Identical in principle to the existing OPERATIONAL_RUNBOOK.md Section 1 startup sequence and Section 4 recovery procedures - proven extensively against PaperBrokerPort (100,000-cycle stress test, 2-year continuous operation, disaster recovery exercise, all with 100% recovery success). **Not yet proven against a real KiteBrokerPort-backed process** - this is exactly what Live Validation Checklist items 14 and 17 exist to establish.

## 7. Safe Shutdown

Same principle as the existing Runbook Section 2: no special graceful-shutdown sequence is required for correctness (the whole design assumes a process can be killed at any instruction boundary and recover via mandatory reconciliation on next startup). If a graceful shutdown is possible, archive any not-yet-archived events/cycle-log entries before stopping, exactly as already documented.

## 8. Incident Response

For a suspected duplicate order, a failed reconciliation, or any other anomaly:
1. **Do not attempt to fix anything by directly resubmitting or cancelling orders outside this platform's own workflow** until the situation is understood - manual actions taken in a panic are themselves a source of risk.
2. Check the database directly for the affected record(s)' current state.
3. Check Kite's own UI/order history directly for the real, authoritative state.
4. If the record is AMBIGUOUS, follow Section 4's workflow.
5. If a genuine duplicate order is confirmed, follow Risk Assessment Risk 5's recovery procedure: halt all live operation, manually reconcile the real position, do not resume until root-caused.

**Not yet built**: any incident-response tooling beyond direct database/log inspection and the existing resolve_ambiguous_execution() mechanism. There is no runbook automation, no incident tracking system, no formal escalation path beyond "the operator handles it personally."

## 9. Emergency Rollback

**Not yet buildable as a tested procedure - no deployment infrastructure exists** (Live Readiness Report, Section 11; Risk Assessment, Risk 7). Until built and validated (Live Validation Checklist item 18), the only available "rollback" is:
1. Stop the running process.
2. Restore the database from the most recent verified backup (per the existing OPERATIONAL_RUNBOOK.md Section 3 backup procedure).
3. Manually run reconciliation before resuming any operation, since the restored backup may be missing the most recent transactions.

This is a manual, unpracticed procedure today, not a proven, one-command rollback.

---

## Milestone 7 Update: Sections 1, 2, 4 Revised

**Section 1 (Daily Startup)**: `ProductionRunner.startup()` now exists and performs this sequence directly - configuration load, secrets validation, notifier construction, authentication, broker health check, mandatory reconciliation, signal handler registration, startup notification. See the Deployment Guide for exact usage.

**Section 2 (Authentication)**: unchanged in substance (still manual, still daily, still never automated) - but `kite_access_token` is now formally the secret name `ProductionRunner` reads at startup (see Configuration Guide). The operator's daily task is: complete the browser login, obtain the `access_token`, store it via `SecretsManager.set_secret("kite_access_token", <value>)` before starting the runner.

**Section 4 (Handling AMBIGUOUS Executions)**: the trigger gap is closed. `TelegramNotificationPort` is wired into `ReconciliationService`, so an `AMBIGUOUS` escalation now generates a real, delivered alert (with retry-then-queue on transient Telegram failures) - confirmed by test, not just claimed. The investigation/resolution workflow itself (`generate_ambiguous_execution_report()`, `resolve_ambiguous_execution()`) is unchanged.

**Still not built**: the operational loop itself (periodically checking for new work, submitting orders, polling AMBIGUOUS resolution commands via `poll_commands()`) - see Deployment Guide. Sections 3, 5-9 remain otherwise as previously documented.
