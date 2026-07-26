# Production Deployment Checklist - Before Connecting to a Live Broker

Every item below is a genuine prerequisite, not a formality. Items marked **BLOCKING** are things this project has explicitly found are not yet done - checking this document without resolving them would be checking a box, not verifying readiness.

## 1. Paper Trading Validation (complete)

- [x] Module 28 stress-tested to 100,000 cycles with zero invariant violations (Milestone 4)
- [x] Extended continuous operation validated over 2 simulated years (Production Verification)
- [x] Disaster recovery exercise: 33/33 injected disasters (unexpected shutdown, process termination, database interruption, broker failure, notification failure) recovered cleanly
- [x] Complete audit-trail reconstruction proven for every order in a validation run, not a sample
- [x] Zero duplicate submissions across every stress test, chaos test, and long-duration run performed
- [x] Mutation testing performed; surviving mutations either fixed or proven mathematically inert (Milestone 4)

## 2. Data and Historical Validation

- [ ] **BLOCKING**: Real historical market data has never been used to validate this strategy. Every historical backtest performed used either synthetic data (clearly labeled) or the CSV-based framework validated against manufactured data. The Historical Validation Framework (Milestone 5A) is complete and proven correct against the data it was given - it has never been proven correct against what actually happened in any real market regime.
- [ ] **BLOCKING**: The five mandatory ETF symbols' inception dates and benchmarks have been verified via web search against 2-6 independent sources each, but three genuine cross-source conflicts remain (two resolved via corroboration, one - LIQUIDBEES - genuinely unresolved). Confirm with a primary/authoritative source before this matters for a live capital allocation decision.
- [ ] Survivorship bias is structurally unaddressed - the platform only ever reasons about ETFs that exist today. Acceptable for paper trading; worth a decision before live capital is at risk.

## 3. Live Broker Integration

- [ ] **BLOCKING**: Zero real Kite Connect API calls have been made in this environment. Every one of the 8 Broker Capability Matrix assumptions (`PHASE7_Objectives.md`) remains unconfirmed.
- [ ] **BLOCKING, highest-risk item in the entire platform**: idempotency key support at the real Kite API is unverified. If the real API does not support this the way the design assumes, the crash-recovery guarantees proven against `PaperBrokerPort` (client_reference matching, see Milestone 3) may not transfer directly - this needs to be the first thing verified against the real API, not assumed to just work because it works in simulation.
- [ ] Token refresh for a live connection remains a manual runbook step, not automated (`PHASE7_Objectives.md`, unchanged by any milestone since).
- [ ] `KiteLiveBrokerPort` (or equivalent) has not been implemented - `BrokerPort`'s interface exists and `PaperBrokerPort` implements it, but no live implementation exists to review, let alone test.
- [ ] Rate limiting behavior against the real API is unverified - Phase 2's `NSEProvider`/`KiteProvider` have rate limiting built in for data fetching, but Module 28's own order-submission rate limits against a live account are untested.

## 4. Security and Credentials

- [ ] Confirm real Kite API credentials are stored via `SecretsManager` (Fernet-encrypted local, or AWS Secrets Manager) - never in plaintext, never in version control. This mechanism is built and frozen (Phase 2) but has never been exercised against real credentials in this environment.
- [ ] Confirm the static-IP requirement (`MinimalInlineComplianceChecker`'s two narrow checks - static IP verification, Algo ID tagging) is satisfied by the actual deployment environment before any live order submission.
- [ ] Confirm no test/paper-trading database or archive files are accidentally reused for live trading - a live deployment should start from a clean, dedicated `ExecutionStateStore`.

## 5. Capital and Risk Controls

- [ ] Confirm real starting capital and position-sizing assumptions match what `VerificationService`'s affordability checks expect - every validation run to date has used synthetic or arbitrarily-large paper capital.
- [ ] Confirm the liquidity-protection threshold (`max_spread_pct`, currently a provisional, disclosed default - `VerificationService`) is appropriate for real market conditions and real position sizes, not the values used in simulation.
- [ ] Confirm `RiskManagementEngine`'s hard constraints (frozen, Phase 5) are configured with real, intended limits, not test defaults.

## 6. Operational Readiness

- [x] Operational Runbook complete (`docs/OPERATIONAL_RUNBOOK.md`) - startup, shutdown, backup, recovery, troubleshooting, monitoring, alert handling, maintenance.
- [x] `AMBIGUOUS` execution state and operator resolution workflow implemented and tested (DDR-001) - the platform never automatically retries an order whose broker outcome is unknown; a genuine reconciliation defect (proven by direct test, not theoretical) that risked automated duplicate orders is closed.
- [ ] **BLOCKING**: a real, staffed on-call/escalation path exists specifically for `AMBIGUOUS` executions, with a defined maximum response time — this state is designed to wait indefinitely for a human, which means live trading's actual safety depends on that human existing and being reachable, not just on the code being correct.
- [ ] Backup and recovery procedures (Runbook Section 3-4) have been *exercised* against a real deployment target, not just documented - a documented-but-never-tested backup procedure is not the same as a verified one.
- [ ] Monitoring and alerting (Runbook Sections 6-7) are wired into real infrastructure, not just specified.
- [ ] A real on-call/escalation path exists for the alert conditions in Runbook Section 7 (especially: any duplicate submission, any `AMBIGUOUS` escalation, any `BROKER_HAS_NO_RECORD` discrepancy).

## 7. Sign-off

This checklist should not be considered satisfied until every **BLOCKING** item above is resolved. As of this Production Verification milestone, the paper-trading operational envelope is genuinely ready; the live-broker integration and real-historical-data validation are not, and nothing in this milestone was designed to close either gap.
