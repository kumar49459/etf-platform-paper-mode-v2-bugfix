# Operational Runbook

Companion to Module 28 (Portfolio Cash & Execution Manager) and the paper trading operational layer. Written for whoever operates this platform day-to-day - not a design document, a working reference.

**Scope note, stated plainly:** everything below describes the *paper trading* operational envelope, which is what has actually been built, tested, and stress-tested (100,000-cycle Module 28 stress test, 2-year continuous operation validation, disaster recovery exercise). Nothing here has been exercised against a real Kite Connect connection - sections that would differ for live trading are marked **[LIVE TRADING - UNVERIFIED]** throughout.

## 1. Startup

1. Verify the repository's frozen-module integrity before starting anything: `git diff v0.6 --stat -- <every frozen package>` must be empty. A non-empty diff means something is wrong with the deployment artifact itself - do not start.
2. Verify secrets/credentials are available via the Phase 2 `SecretsManager` (local Fernet-encrypted store or AWS Secrets Manager, per `secrets_manager/`). **[LIVE TRADING - UNVERIFIED]**: real Kite API credentials have never been exercised in this environment.
3. Open (or create, on first run) the `ExecutionStateStore` SQLite database. On connect, it runs `PRAGMA integrity_check` automatically - a `DatabaseCorruptionError` here means the database file itself is damaged; do not proceed past this point without following the Recovery section below.
4. **Mandatory reconciliation runs on every startup, unconditionally** (Milestone 1's Decision 1: the broker is always the source of truth). This is not optional and cannot be skipped by configuration - do not attempt to bypass it even if startup feels slow.
5. Confirm the event archive path (`EventArchive`) and cycle-log archive path (`CycleLogArchive`) are writable and on durable storage, not a temp/ephemeral filesystem - these are what makes the audit trail survive periodic in-memory clearing.

## 2. Shutdown

1. There is no special "graceful shutdown" sequence required for correctness - the whole design assumes a process can be killed at any instruction boundary (proven via chaos restart testing, Milestone 4, and the disaster recovery exercise) and will recover correctly on next startup via mandatory reconciliation.
2. If a graceful shutdown IS possible, prefer it anyway: call `ExecutionStateStore.close()` to release the SQLite connection cleanly, and archive any not-yet-archived events/cycle-log entries (`EventArchive.archive_and_clear()` / `CycleLogArchive.archive_and_trim()`) rather than leaving them to be picked up by the next periodic purge - this reduces (does not eliminate the need for) recovery work on next startup.
3. **Never** delete the SQLite database file or the archive files as part of shutdown. They are the audit trail.

## 3. Backup

1. The `ExecutionStateStore` SQLite file (WAL mode) and its `-wal`/`-shm` sidecar files must be backed up together, not the main file alone - an inconsistent partial backup of only the main file can lose committed-but-not-checkpointed transactions.
2. The event archive (JSONL) and cycle-log archive (JSONL) files should be backed up on the same cadence as the database - they are the durable audit trail, and a database backup without them loses reconstructability for anything already archived-and-cleared from memory.
3. Recommended cadence: daily, at minimum, given the database is designed to grow with genuine trading activity (confirmed healthy, proportional growth) rather than being prunable.
4. Backup verification: periodically restore a backup to a scratch location and confirm `ExecutionStateStore`'s own `PRAGMA integrity_check` passes on the restored copy - an unverified backup is not a backup.

## 4. Recovery

**Scenario: process crashed or was killed unexpectedly.**
Restart normally (Section 1). Mandatory reconciliation on startup handles this - proven via the disaster recovery exercise (33/33 injected disasters recovered cleanly, 0 orphans, 0 duplicates) and the 2-year continuous operation validation (40 restarts, 100% clean recovery).

**Scenario: `ExecutionStateStore` raises `DatabaseCorruptionError` on startup.**
This means `PRAGMA integrity_check` failed - the database file itself is damaged, not just "state looks inconsistent." Do not attempt to force-open it. Restore from the most recent verified backup (Section 3), then run reconciliation manually before resuming normal operation, since the restored backup may be missing the most recent transactions.

**Scenario: reconciliation finds a `BROKER_HAS_NO_RECORD` discrepancy.**
This is a genuine anomaly (a local record references a broker order the broker has never heard of) - `ReconciliationService` deliberately does not guess a resolution for this case. Requires manual review: check the broker's own records directly (dashboard, support) before deciding whether to mark the local record as abandoned or investigate further.

**Scenario: an order is stuck at `SUBMITTED` with no `broker_order_id`.**
This is exactly the crash-recovery case Module 28 Milestone 3 was built around. Reconciliation will attempt to match it against the broker's open orders by `client_reference`. **Updated per DDR-001**: if genuinely not found among open orders, this no longer auto-retries — it escalates to `AMBIGUOUS` (see the dedicated scenario below), since "not found in open orders" cannot be distinguished from "already reached a terminal state before this check ran."

**Scenario: an execution reaches `AMBIGUOUS` (DDR-001).**
This is the platform's core safety guarantee, not a failure mode to route around. It means reconciliation could not confirm whether an order reached the broker, and — critically — the platform will **never** automatically retry in this state, because the "safe" case (never reached the broker) and the "already resolved" case (filled/cancelled/rejected before the check ran) look identical from the local vantage point, and guessing between them risks a genuine duplicate order.

*What to do:*
1. Generate the operator report: `generate_ambiguous_execution_report(record, store)` (`execution_manager/ambiguous_report.py`) — gives execution ID, cycle ID, timestamps, whatever broker info is known, the reconciliation evidence already gathered, and recommended next steps.
2. Check the broker directly — its own web/app order history, contract notes, or support contact — for anything matching the symbol, quantity, price, and approximate time in the report. This platform's own API access could not resolve this; that's the entire reason the state exists.
3. Call `ReconciliationService.resolve_ambiguous_execution(execution_id, confirmed_state, operator_notes, ...)` with whatever you actually confirmed. `operator_notes` is mandatory and becomes the permanent audit record of how this was resolved — write what you checked and what you found, not just the conclusion.
4. There is no automated escape from `AMBIGUOUS` — if this step is skipped, the execution simply waits, indefinitely, for a human. That's the intended behavior, not a bug.

*Expected frequency*: rare, given this platform's validated low order volume — a handful of times per year at most, only when a crash or interruption happens to land in the narrow window between a broker call and its local recording. Treat a rising frequency as itself worth investigating (see Monitoring).

## 5. Troubleshooting

| Symptom | Likely cause | Where to look |
|---|---|---|
| Order stuck, never resolving | Check `OrderLifecycleState` via `ExecutionStateStore.load_execution_record()` | If `SUBMITTED`, see Recovery above. If `AMBIGUOUS`, this requires operator action, not troubleshooting — see the AMBIGUOUS scenario above. If `PENDING`/`PARTIALLY_FILLED`, confirm the broker connection is actually being polled. |
| Reconciliation reports many mismatches | First check `DiscrepancyType` - `STILL_OPEN_AT_BROKER` is benign (not a real mismatch); `AMBIGUOUS_NO_LOCAL_ID` is a valid operational outcome requiring operator review, not an error; only `STATE_MISMATCH` and `BROKER_HAS_NO_RECORD` are otherwise genuine | `reconciliation.py`'s `DiscrepancyType` enum |
| Memory usage climbing | Check `resource_trends.py`'s per-structure breakdown, not just the aggregate verdict - confirmed DB growth is healthy (proportional to activity); confirmed `cycle_log`/`resource_snapshots` are now bounded; a genuinely new growing structure would be a new finding worth investigating the same way | `paper_trading_operations/resource_trends.py` |
| Report shows an anomaly for an order that seems fine | Check whether it's `CONFIRMED MISSING` (real problem) vs. a transient-read-failure note (not a real problem, re-run reconciliation) | `reports.py`'s `_current_status` |
| Can't find an order's full history | Use `EventArchive.reconstruct_by_cycle_id()` and `session.get_full_cycle_log()`, not the raw in-memory `cycle_log`/event recorder alone - those are periodically cleared by design | `event_archive.py` |

## 6. Monitoring

Recommended metrics to track continuously (all already measured in the Production Verification run - see that report for baseline figures):

- Restart frequency and recovery success rate (target: 100%, as validated)
- Reconciliation mismatch rate, broken down by `DiscrepancyType` (rising `BROKER_HAS_NO_RECORD` or `AMBIGUOUS_NO_LOCAL_ID` specifically warrants investigation — these are the two categories `ReconciliationService` refuses to auto-resolve)
- **Count of executions currently sitting in `AMBIGUOUS`, awaiting operator review** — should normally be zero or near-zero; a growing, unaddressed backlog means operator review isn't keeping pace, which erodes this platform's core safety guarantee in practice even though the guarantee itself (no automated duplicate) always holds
- Duplicate submission count (must always be 0 - any nonzero value is a critical finding requiring immediate investigation, not routine monitoring noise)
- Database growth rate, in bytes-per-cumulative-event (not raw size alone - this ratio should be flat or declining for healthy operation; a rising bytes-per-event ratio would indicate genuine bloat, unlike the raw-size growth already confirmed healthy)
- Memory usage trend (confirmed small residual ~45-50KB/year growth not fully explained - see the Production Verification Report; worth continued monitoring at longer real-world durations)

## 7. Alert Handling

- **Any duplicate submission**: treat as critical, page immediately. This should never happen given everything validated so far; if it does, the underlying invariant has broken.
- **Any `AMBIGUOUS` escalation**: treat as high priority. A high-priority notification is generated automatically by `ReconciliationService` at the moment of escalation (per DDR-001) — this alert is the trigger for the AMBIGUOUS operator workflow in Section 4, not something to defer.
- **`BROKER_HAS_NO_RECORD` discrepancy**: treat as high priority, requires manual review within the same operational day - this is the other class of anomaly the system deliberately does not auto-resolve.
- **`DatabaseCorruptionError` on any startup**: treat as critical, follow the Recovery procedure above before any other action.
- **Reconciliation not running on schedule**: treat as high priority - mandatory reconciliation is load-bearing for every recovery guarantee this platform has proven; a missed reconciliation window erodes those guarantees silently.

## 8. Maintenance

- Periodically verify archive files (`EventArchive`, `CycleLogArchive`) are still being written to and growing - a silently-stopped archive would reintroduce the exact "complete execution history must be reconstructable" gap found and fixed in Milestone 5B.
- Periodically re-run a scaled-down version of the Production Verification exercise (continuous operation + disaster injection) against any new deployment or after any dependency upgrade, rather than assuming prior validation still holds indefinitely.
- **[LIVE TRADING - UNVERIFIED]**: token refresh for a real broker connection is a manual runbook step per the existing design documentation (`PHASE7_Objectives.md`), not yet automated - this remains true and unaddressed by anything in this milestone.
