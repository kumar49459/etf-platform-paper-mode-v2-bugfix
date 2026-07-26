# Configuration Guide

## Required Secrets (via SecretsManager)

All of the following must be present before ProductionRunner.startup() will succeed. None may ever be hardcoded, logged, or stored outside SecretsManager - confirmed by test (TestKiteCredentialsIntegration, Milestone 7).

| Secret name | Source | Notes |
|---|---|---|
| kite_api_key | Kite Connect developer console (one-time app registration) | Long-lived, does not rotate daily |
| kite_api_secret | Kite Connect developer console | Long-lived. Never appears in application code - see kite_credentials.py |
| kite_access_token | Daily manual login flow (see Live Operational Runbook, Section 2) | Rotates daily. Must be re-stored via SecretsManager.set_secret() every trading day before startup - this platform never generates one automatically (Decision 3, architecture review) |
| telegram_bot_token | Created via @BotFather on Telegram | Long-lived |
| telegram_chat_id | The target chat/channel ID for alerts | Long-lived |

## Setting a Secret

```python
from etf_platform.config_manager.config_manager import ConfigManager
from etf_platform.secrets_manager.secrets_manager import SecretsManager

config = ConfigManager(config_dir="config").load()
sm = SecretsManager(config.secrets)
sm.set_secret("kite_api_key", "<real value>")
```

## Startup Validation

ProductionRunner fails fast with a specific, actionable error message for each of the following, verified by test:
- Missing Telegram credentials (checked first - running without a working alert channel was the Live Readiness Review's top-priority finding, so this is refused before anything else is attempted).
- Missing kite_api_key/kite_api_secret (via MissingKiteCredentialsError, listing exactly which secret(s) are absent).
- Missing kite_access_token (distinct message directing the operator to the manual daily login flow).
- An unreachable Kite API at the startup health-check step (get_available_cash()).

## Compliance Configuration

MinimalInlineComplianceChecker's static_ip_verified is explicitly set to False by ProductionRunner at construction time, not left at the class's own default of True. There is currently no automated mechanism to flip this to True based on a real verification - this remains a manual code/configuration change an operator must make deliberately once static-IP compliance is actually confirmed for the real deployment environment, per Kite's requirement (mandatory since April 1, 2025).

---

## Milestone 8 Update: Transport Configuration

No new configuration is required for `RequestsHTTPTransport` itself - it uses a standard `requests.Session()` with sensible defaults (10-second timeout, overridable per-call). If a deployment requires custom transport behavior (a proxy, custom TLS certificates, connection pooling tuning), inject a pre-configured `requests.Session` via `RequestsHTTPTransport(session=<configured session>)` and pass that instance as `ProductionRunner(http_transport=..., telegram_transport=...)` - existing dependency injection, unchanged, is the extension point for this, not new configuration schema.
