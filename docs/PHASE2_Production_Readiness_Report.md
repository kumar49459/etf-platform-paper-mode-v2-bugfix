# Phase 2 — Production-Readiness Review

**Scope:** Historical Data Engine, Data Quality Validator, Configuration Manager, Secrets Manager.
**Status at end of review: 3 critical issues found and fixed. 1 architectural note recorded (non-blocking). Freezing after fixes.**
**Test suite: 134 tests, all passing.** (Was 95 at initial delivery; 39 added during this review, specifically to cover the gaps this review found.)

This review was done by actually reading the code against each point below and running targeted checks — not asserted from memory. Two of the three critical issues were caught by writing a *new* test first and watching it fail, which is the point of doing this before freezing rather than after.

---

## 1. Architecture compliance with Phase 1 SRS

**PASS.** Checked against the binding decisions in `docs/PHASE1_Architecture_SRS.md` §12/§13:
- Provider abstraction (§12.6): confirmed — `HistoricalDataEngine` never imports a source-specific client outside the `providers/` package; `NSEProvider`/`KiteProvider` are interchangeable behind `DataProvider`.
- Fail-safe default on critical data quality issues (§1.4): confirmed — `CriticalDataQualityError` halts the *entire* ingestion run, not just the offending symbol (verified by `test_no_data_for_symbol_halts_entire_ingestion_run`).
- WAL mode SQLite (§13.2): confirmed, and hardened this review (see §3 below).
- Minimal live-instance dependencies (§12.1): confirmed — `requirements-live.txt` has no pandas/pyarrow/boto3.
- config_version reproducibility hash (§1.4): confirmed, present and tested.
- Secrets never logged (§1.4): confirmed, see §7.

## 2. SOLID principles and clean architecture

**PASS, with one documented non-blocking note.**
- **S**ingle Responsibility: each class has one job (RateLimiter only rate-limits, SymbolResolver only resolves, etc.) — no god objects.
- **O**pen/Closed: new data providers or storage backends can be added without modifying existing code (both are ABC-based).
- **L**iskov Substitution: `NSEProvider` and `KiteProvider` are fully interchangeable via `DataProvider`; `CSVTimeSeriesStore`/`ParquetTimeSeriesStore` likewise via `TimeSeriesStore`.
- **I**nterface Segregation: `DataProvider` and `SecretsProvider` are both small, focused interfaces — no fat interfaces forcing unused method implementations.
- **D**ependency Inversion — **mostly clean, one soft spot**: `HistoricalDataEngine._build_providers()` directly constructs `NSEProvider`/`KiteProvider` rather than resolving them through a registry/factory keyed by provider name. This is a minor coupling to concrete classes in one method. **Not fixed now** — with exactly two providers and no near-term plan for a third beyond the already-scoped paid-vendor adapter (Phase 1 §12.6), a full plugin registry would be premature abstraction. Noted here so it's a deliberate deferral, not an oversight; revisit if/when a third provider is actually added.

## 3. Thread safety and SQLite WAL implementation

**CRITICAL ISSUE FOUND AND FIXED.** `common/db.py` opened SQLite connections with the stdlib default `check_same_thread=True`, while `SnapshotRegistry` held one shared connection for the lifetime of a `HistoricalDataEngine`. Any use of that engine from more than one thread within a process (a realistic scenario — e.g. Phase 1's Approval Console and Scheduler both eventually running on the same micro instance) would raise `sqlite3.ProgrammingError`.
**Fix:** `check_same_thread=False` in `common/db.connect()`, paired with a `threading.Lock` guarding every connection access in `SnapshotRegistry` (both changes only work together — disabling the check without the lock would have been a real bug, not a fix).
**Verified:** new regression test `test_concurrent_access_from_multiple_threads_is_safe` — 8 threads × 5 writes each, zero errors, zero lost writes, confirmed via direct row count.

## 4. Memory and CPU suitability for AWS EC2 Micro

**PASS.** `CSVTimeSeriesStore` (the tested default) streams rows via stdlib `csv`, no full-dataset in-memory structures. `RateLimiter` and `SnapshotRegistry` hold O(1) state. `ParquetTimeSeriesStore` does use pandas DataFrames, but scoped per-symbol-per-snapshot (a few years of daily EOD data for one ETF is a few hundred KB) — not a full-universe load. Nothing in Phase 2 loads the full ETF universe into memory at once. This is consistent with Phase 1 §0's original concern, which was specifically about *backtesting/optimization* workloads (out of scope until Phase 4+), not ingestion.

## 5. Resource leaks (connections, files, HTTP sessions, locks)

**TWO CRITICAL ISSUES FOUND AND FIXED.**
1. `HistoricalDataEngine` had a `close()` method but no context manager support, and it never closed provider HTTP sessions — only the SQLite registry. **Fixed:** added `__enter__`/`__exit__` to `HistoricalDataEngine`, `NSEProvider`, and `KiteProvider`; `close()` now closes every provider session (best-effort — one provider's close failure doesn't block the others or the registry close, verified by `test_provider_close_failure_does_not_block_registry_close`).
2. `common/logging_setup.configure_logging()` called `root.handlers.clear()` without closing the old handlers first — every reconfigure leaked a file descriptor. **Found via a new test** (`ResourceWarning: unclosed file`, promoted to a hard failure by running the suite with `-W error::ResourceWarning`). **Fixed:** handlers are explicitly `.close()`d before being cleared.
Locks: `RateLimiter`'s lock and `SnapshotRegistry`'s new lock are both used exclusively via `with` blocks — no manual acquire/release pairing that could leak on an exception path.

## 6. Retry logic, timeout handling, exponential backoff

**CRITICAL ISSUE FOUND AND FIXED.** Neither `NSEProvider` nor `KiteProvider` had any retry logic — a single transient connection blip caused an immediate skip (NSE, per-day) or provider-level failure (Kite), even though both APIs are documented as occasionally flaky. Timeouts were already present (`timeout_seconds`, passed to every `requests` call) but timeouts alone don't help with resilience.
**Fix:** new `common/retry.py` — exponential backoff with full jitter, a hard `max_attempts` ceiling (default 3), and a `is_retryable` predicate so only transient failures (connection errors, timeouts, 5xx, and Kite's 429) are retried — 4xx client errors (bad auth, bad request) fail immediately rather than wasting the rate-limit budget on retries that cannot succeed.
**Verified:** `test_transient_connection_error_is_retried_then_succeeds`, `test_http_404_is_not_retried`, `test_429_rate_limit_is_retried`, `test_401_auth_error_not_retried`, plus 8 tests directly against `retry_with_backoff` itself.

## 7. Logging quality and secret exposure

**PASS.** Manually audited every `logger.*` call site in `secrets_manager/` and `data_engine/providers/` — no call site logs a secret value, an `Authorization` header, or a raw response body from an authenticated endpoint. Defense-in-depth is also in place and tested: `SecretScrubbingFilter` redacts any value registered via `SecretsManager.get_secret()`/`set_secret()` from every subsequent log record, verified end-to-end with a real file handler in `test_secrets_redacted_in_actual_file_output` (writes a secret to an actual log file, reads the file back, confirms the value is absent and `***REDACTED***` is present).

## 8. Unit test coverage and missing edge cases

**Gaps found and closed this review** (39 new tests): `common/logging_setup.py` (0 tests → 11), `data_engine/storage/factory.py` (0 tests → 4), `common/retry.py` (0 tests → 8), `get_config()` process-wide singleton (0 tests → 2), `HistoricalDataEngine` resource cleanup / context manager (0 tests → 4), `SnapshotRegistry` real concurrency (0 tests → 1, but a meaningful one — see §3). Total: 134 tests, all passing.
**Remaining known gap (not closed, documented as intentional):** `ParquetTimeSeriesStore` has zero test coverage in this sandbox — `pyarrow` isn't installable here. This was already disclosed in Phase 2's README and is not new to this review; re-confirming it's still accurate and still flagged.

## 9. Security review: Config Manager, Secrets Manager, Historical Data Engine

- **Config Manager:** uses `yaml.safe_load` (not `yaml.load`) — no arbitrary code execution risk from a malicious/malformed config file. Unknown config keys are rejected rather than silently ignored, which prevents a typo'd key from producing a false sense of a setting being applied.
- **Secrets Manager:** Fernet (AES-128-CBC + HMAC) symmetric encryption for the local provider; encryption key is never stored alongside the encrypted file (must come from an env var); file permissions are forced to `0600` and verified by test. AWS provider never writes secrets from the running process (rotation is infrastructure-side only, least-privilege by design).
- **Historical Data Engine:** all URL construction uses either fixed templates with numeric/date interpolation (NSE bhavcopy path) or `requests`' own `params=` encoding (Kite query params) — no raw string concatenation of user- or provider-supplied data into a URL, so no injection surface. Kite's Authorization header is rebuilt fresh on every call from `SecretsManager` rather than cached in a plain attribute, minimizing the window a decrypted token sits in memory.

## 10. Public API stability and documentation

**GAP FOUND AND FIXED.** 23 public classes had a docstring on their containing *module* (explaining design rationale) but not on the *class itself* — meaning `help(HistoricalDataEngine)` and similar returned nothing. Added concise class-level docstrings to all 23 (the 12 primary API/orchestration classes plus 11 supporting dataclasses/config classes), each pointing back to the module docstring for full rationale rather than duplicating it. Public interfaces (`get_ohlcv`, `get_corporate_actions`, `get_instrument_master`, `ingest`, `SecretsManager.get_secret`/`set_secret`, `ConfigManager.load`) are unchanged from what was reviewed and approved earlier in this conversation — no breaking changes introduced by this review.

## 11. Production-readiness confirmation

**Confirmed production-ready, with the limitations already disclosed in `README.md` still standing** (NSE/Kite live endpoint details unverified against the real APIs — no network access in this build environment; corporate-actions endpoints are documented stubs; Parquet backend untested here; holiday calendar is a weekday-only approximation). None of these are "hidden" — each has a docstring or README section at the exact place it matters, and none of them block Phase 3, which depends on the Data Engine's *interface*, not on the specific provider wiring being live-verified yet.

---

## Freeze declaration

Phase 2 is frozen as of this review. Frozen state: 134/134 tests passing, all 11 review points addressed, 3 critical fixes applied (thread safety, resource leaks ×2, missing retry logic) plus 1 documentation gap closed. Any further change to Phase 2 code requires reopening this freeze explicitly, the same discipline Phase 1's architecture freeze already established.
