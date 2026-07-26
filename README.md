# ETF Investment Platform (India) — Phase 2

**Modules delivered this phase:** Historical Data Engine, Data Quality Validator,
Configuration Manager, Secrets Manager.

This is Phase 2 of the platform whose architecture was approved in
`docs/PHASE1_Architecture_SRS.md` (see that document for the full SRS, all binding
design decisions, and the module inventory). Nothing here should contradict
that document; if it appears to, the architecture doc is the source of truth
and this is a bug.

## Install

```bash
# Live/micro instance — minimal footprint (binding constraint, Phase 1 §12.1)
pip install -r requirements-live.txt
pip install -e . --no-deps

# Research/dev instance — full feature set (pandas, pyarrow, boto3, pytest)
pip install -r requirements-research.txt
pip install -e . --no-deps
```

## Honest limitations of this delivery (read before deploying)

This was built and tested in a sandboxed environment **with no network
access**. That shaped a few things, disclosed here rather than glossed over:

1. **NSE and Kite HTTP endpoint details are unverified against the live
   APIs.** The bhavcopy URL pattern, CSV column names, and Kite Connect
   endpoint shapes follow documented/historical conventions but have not
   been tested against a real request. Re-verify against
   `https://www.nseindia.com/all-reports` and
   `https://kite.trade/docs/connect/v3/` before the first real ingestion
   run. Every provider method is structured so this is a contained,
   low-risk follow-up (swap the URL/parsing logic, the interface and all
   call sites stay the same) — see the docstrings in `nse_provider.py` and
   `kite_provider.py` for exactly what to check.
2. **`ParquetTimeSeriesStore` is implemented but not test-covered here** —
   `pyarrow` isn't installed in this sandbox. `CSVTimeSeriesStore` (the
   default `auto`-selected backend without pyarrow) has full test coverage
   and is what the included test suite actually exercises. Install
   `pyarrow` and re-run the test suite in the target environment before
   relying on the Parquet path in production.
3. **NSE/Kite corporate actions endpoints are documented stubs** (return an
   empty list) rather than working implementations — picking and verifying
   the exact current endpoint needs network access this sandbox doesn't
   have. The Data Quality Validator's price-jump check already accounts for
   this (an unexplained jump with no corporate action on record is flagged
   CRITICAL, which is the *safe* default until this is wired up — it means
   real corporate actions will currently show up as CRITICAL data quality
   issues requiring a `force` override until this stub is completed).
4. **Trading holiday calendar is a simple weekday-only approximation** —
   gap detection will flag real NSE holidays as missing-data WARNINGs until
   a real holiday list is injected via `DataQualityValidator(holidays=...)`.

None of this blocks Phase 3+ design work, and none of it was hidden in the
code — every instance above has a docstring at the exact place it matters.

## Running tests

```bash
python -m unittest discover -s tests -v
```

95 tests, all passing as of this delivery. Uses stdlib `unittest` rather
than `pytest` — see `config_manager/schema.py` docstring for why this and
other minimal-dependency choices were made deliberately, not just to work
around sandbox constraints.

## Quick example

```python
from datetime import date
from etf_platform.config_manager import ConfigManager
from etf_platform.secrets_manager import SecretsManager
from etf_platform.data_engine import HistoricalDataEngine

config = ConfigManager(config_dir="config", environment="dev").load()
secrets = SecretsManager(config.secrets)
# One-time setup: secrets.set_secret("kite_api_key", "..."); etc.

engine = HistoricalDataEngine(config, secrets_manager=secrets)
snapshot = engine.ingest(["NIFTYBEES", "GOLDBEES"], date(2026, 1, 1), date(2026, 1, 31))
data = engine.get_ohlcv(["NIFTYBEES"], date(2026, 1, 1), date(2026, 1, 31))
```

## What's intentionally NOT in this phase

Per your Phase 2 scope: Portfolio Optimizer, Strategy Engine, AI Allocation
Engine, Backtesting Engine, Risk Management, the full Symbol Resolution
Engine (Module 17 — this phase includes only the scoped subset needed for
Kite token resolution), Approval Console, and everything else in the
25-module inventory. See `PHASE1_Architecture_SRS.md` §9 for the full
roadmap and phase ordering.
