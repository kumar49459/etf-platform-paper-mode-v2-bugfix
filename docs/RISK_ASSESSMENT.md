# Production Risk Assessment

Every risk below reflects an actual, verified gap or genuine uncertainty in this repository - not a generic risk-register template. Where a risk is a blocker, it is marked as one explicitly, with the evidence behind that marking.

## Risk 1: No alert actually reaches a human (BLOCKER)

**Probability**: Certain - confirmed current fact, not a probabilistic event. Direct search of the repository found NotificationPort (strategy_engine/ports.py) is an abstract interface whose own docstring states "Module 13 (Telegram Notifications) - not yet implemented." No concrete implementation exists anywhere in the codebase.
**Impact**: Critical. DDR-001's entire safety design - the platform's headline production-readiness achievement - depends on a human seeing an AMBIGUOUS escalation alert and acting on it. Without a real NotificationPort implementation, an AMBIGUOUS execution is invisible until someone happens to inspect the database directly.
**Detection method**: None currently exists. A periodic manual database query (checking for records in the AMBIGUOUS state) would be the only way to notice, until a real alerting channel is built.
**Mitigation**: Build a real NotificationPort implementation (Telegram or otherwise) before live trading begins. This is not optional and not minor - it is the delivery mechanism for the platform's core safety guarantee.
**Recovery procedure**: N/A until mitigated - there is no recovery from an alert that was never sent.

## Risk 2: No live-operation entry point exists (BLOCKER)

**Probability**: Certain, confirmed by direct search - no main.py, __main__.py, run_live.py, or daemon.py exists anywhere in src/.
**Impact**: Critical - there is currently no actual program that wires KiteAuthManager, KiteBrokerPort, SubmissionOrchestrator, and ReconciliationService together into a runnable process. This blocks live trading entirely, not partially - there is nothing to connect to a real account yet.
**Detection method**: N/A - self-evident from the repository structure.
**Mitigation**: Build a live-operation entry point performing mandatory startup reconciliation (per the existing Operational Runbook's already-established Decision 1: the broker is always the source of truth, reconciliation on every startup is unconditional) before accepting any new order activity.
**Recovery procedure**: N/A until mitigated.

## Risk 3: Credentials not routed through SecretsManager (BLOCKER)

**Probability**: Certain, confirmed by direct inspection - KiteAuthManager.__init__(self, api_key, api_secret) takes plaintext arguments directly; no import or usage of SecretsManager exists anywhere in execution_manager's Kite-related files.
**Impact**: High. Both SecretsManager (Phase 2, frozen, encrypted local storage or AWS Secrets Manager) and KiteAuthManager exist and are independently well-tested, but nothing connects them - whatever constructs KiteAuthManager today is responsible for sourcing real credentials safely on its own, with no enforcement that this happens correctly.
**Detection method**: Code/configuration review of whatever script or process ends up constructing KiteAuthManager at deployment time.
**Mitigation**: Wire KiteAuthManager's credential sourcing through SecretsManager as part of building the live-operation entry point (Risk 2) - these two gaps are naturally closed together, not independently.
**Recovery procedure**: If credentials are found to have been mishandled (e.g. committed in plaintext, logged, or stored insecurely), rotate them immediately via Kite's developer console and re-secure before any further use.

## Risk 4: Real Kite API behavior diverges from documented/mocked behavior

**Probability**: Medium - the architecture review already found real, disclosed documentation inconsistencies (rate limits changing over time, token expiry timing reported inconsistently even by Kite's own support staff) before ever touching a live account. Divergence in other areas is a reasonable expectation, not a remote possibility.
**Impact**: Medium to high, depending on which behavior diverges. A genuinely new/unrecognized status value fails loudly (UnrecognizedKiteStatusError) rather than silently misbehaving - a deliberate, safer failure mode built into the status-mapping design.
**Detection method**: The Live Validation Checklist's items 1-10 exist specifically to surface this before it matters at scale.
**Mitigation**: Complete the full Live Validation Checklist before scaling beyond the single minimal test order in item 7.
**Recovery procedure**: For an unrecognized status, the platform halts processing for that specific order rather than guessing - manual intervention required, but no silent corruption of state.

## Risk 5: The duplicate-order defect DDR-001 fixed could have a real-broker-specific edge case

**Probability**: Low - the fix operates on ExecutionRecord state and is broker-agnostic by construction; the same logic path is exercised regardless of which BrokerPort implementation sits behind it. But "designed to be agnostic" and "verified against a real broker" are different claims, and only the first has been established.
**Impact**: Critical if it occurs - a real duplicate order with real capital.
**Detection method**: Live Validation Checklist item 15, purpose-built to reproduce the exact DDR-001 root-cause scenario against a real broker.
**Mitigation**: Complete item 15 before any live trading beyond the single validation order in item 7.
**Recovery procedure**: Per item 15's own rollback plan - halt all live operation immediately, manually reconcile the real position via Kite's own UI, do not resume until the root cause is understood and fixed.

## Risk 6: Static IP compliance is fail-open by default

**Probability**: Depends entirely on deployment configuration discipline - confirmed by direct inspection: MinimalInlineComplianceChecker.__init__(self, static_ip_verified=True, ...) defaults to True (assumed compliant) rather than False (must be proven compliant).
**Impact**: High if misconfigured - Kite has required static IP for order placement since April 1, 2025 (architecture review finding); a deployment that never explicitly sets this parameter would silently pass this check regardless of whether the requirement is actually met.
**Detection method**: Explicit configuration review before deployment - confirming this parameter is set from a real verification result, not left at its default.
**Mitigation**: A deployment checklist item requiring explicit confirmation of this parameter's real value, not just its presence in the constructor call.
**Recovery procedure**: If discovered live, orders would likely be rejected by Kite directly at the exchange level, surfacing as an OrderRejectedError - a safe failure mode, but one that should not be relied upon as the actual compliance mechanism given this platform's own check can be silently bypassed by omission.

## Risk 7: No deployment infrastructure means no proven rollback path

**Probability**: Certain - confirmed by direct search: no systemd unit, container definition, or infrastructure-as-code exists anywhere in this repository.
**Impact**: Medium to high - without a proven rollback procedure, a bad deployment has no fast, safe recovery path.
**Detection method**: Live Validation Checklist item 18.
**Mitigation**: Build and test a real deployment/rollback procedure before live trading.
**Recovery procedure**: Until built, the only "rollback" is manual: stop the process, manually restore from a database backup, manually verify state via reconciliation before resuming.

## Risk 8: Residual, unexplained memory growth (~45-50KB/year)

**Probability**: Confirmed to exist (Production Verification report), magnitude small and previously investigated in detail.
**Impact**: Low at the scale already validated (under 300KB total over 2 simulated years); unknown at longer real-world durations, since only simulated durations have been tested.
**Detection method**: Ongoing monitoring per the existing Operational Runbook's Monitoring section.
**Mitigation**: Continued monitoring; re-investigate if the growth rate changes or accelerates beyond what was previously characterized.
**Recovery procedure**: A process restart clears in-memory state entirely; not currently a recovery-requiring issue at its observed magnitude.

## Risk 9: resolve_ambiguous_execution() untested against a real KiteBrokerPort-sourced AMBIGUOUS case

**Probability**: N/A - this is a testing-coverage gap, not a probabilistic event. All DDR-001 testing to date used PaperBrokerPort exclusively.
**Impact**: Medium - the underlying mechanism operates on ExecutionRecord and is broker-agnostic by design, but this specific combination (a real Kite-sourced AMBIGUOUS record, resolved by a human) has never actually been exercised.
**Detection method**: Live Validation Checklist item 16.
**Mitigation**: Complete item 16 before relying on this workflow during a real incident under time pressure.
**Recovery procedure**: If the workflow behaves unexpectedly against a real case, fall back to direct, explicit, logged manual database correction as an exceptional action, not a normal operating procedure.

## Blockers Summary (must be resolved before live trading can even begin)

1. Risk 1 - no real alerting channel exists.
2. Risk 2 - no live-operation entry point exists.
3. Risk 3 - credentials are not routed through SecretsManager.

Every other risk listed here is real and worth tracking through the Live Validation Checklist, but none of them individually blocks *beginning* validation the way Risks 1-3 do. The checklist itself cannot even reach item 13 (Telegram alerts) meaningfully until Risk 1 is closed, and cannot be run against a live account at all until Risks 2 and 3 are closed - these three are prerequisites to everything else in this document, not peers to Risks 4-9.
