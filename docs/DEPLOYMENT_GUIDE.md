# Deployment Guide

**Scope note**: this documents how to configure and run ProductionRunner as it exists today. Per instruction, no deployment infrastructure (systemd, containers, infrastructure-as-code) has been built in this milestone - this guide covers manual, direct-process deployment, which is what actually exists to document.

## Prerequisites

1. Python environment with this repository's dependencies installed.
2. A config/base.yaml (and optionally environment-specific overrides) under config_manager's existing schema.
3. ETF_PLATFORM_MASTER_KEY environment variable set to a valid Fernet key (if using the local SecretsManager provider) - generate one with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
4. All required secrets populated (see Configuration Guide) before the first startup attempt.
5. A real Kite Connect API key/secret and a completed manual daily login producing a valid access_token (see Live Operational Runbook, Section 2).
6. A real Telegram bot token and chat ID (create via @BotFather on Telegram - outside this platform's scope to automate).

## Running

```python
from etf_platform.production.production_runner import ProductionRunner

runner = ProductionRunner(config_dir="config")
runner.startup()
# ... operational loop (order submission via SubmissionOrchestrator,
#     periodic reconciliation, periodic runner.health_check(), checking
#     runner.shutdown_requested() to exit cleanly on SIGTERM/SIGINT) ...
runner.shutdown()
```

**Not yet built**: the actual operational loop shown as a comment above - ProductionRunner provides startup/shutdown/health-check plumbing; the loop that periodically checks for new Strategy Engine cycles, submits orders, and polls for AMBIGUOUS resolution is out of this milestone's explicit scope ("resolve ONLY the three approved production blockers") and remains a genuine gap for a future milestone to close.

## Rollback

**Not yet a tested, proven procedure** - no deployment infrastructure exists to roll back *to*. Manual rollback today means: stop the process, restore the database from the most recent verified backup (Operational Runbook, Section 3), and run reconciliation before resuming. See Live Validation Checklist item 18, which remains unactioned.

## Health Checks

ProductionRunner.health_check() performs a real, live check (get_available_cash() against the actual Kite API) - not a static "yes." Returns False and logs the failure reason if the broker connection is unreachable.

---

## Milestone 8 Update: Real HTTP Transport Now Exists

The gap noted above ("no default real transport wired in") is resolved. `ProductionRunner` now auto-constructs `RequestsHTTPTransport` (`common/requests_http_transport.py`) whenever no transport is explicitly supplied - the example in this guide's "Running" section now works as written, with no additional wiring required. Explicit dependency injection (e.g. for tests) continues to work exactly as before; auto-construction only fills in the previously-missing default case.

**This does not mean live validation has occurred.** The transport can now make a real HTTP call - it has not been exercised against real Kite or Telegram endpoints, only against this environment's network egress policy (which blocks external hosts) and mocked `requests.Session` objects in tests. See the Live Validation Checklist, unchanged and still entirely unactioned.
