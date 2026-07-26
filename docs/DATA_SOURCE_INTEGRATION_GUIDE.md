# Data Source Integration Guide

Companion to the Historical Data Acquisition Module
(`src/etf_platform/historical_validation/`). This document is the contract
a new data source must satisfy to plug into the platform without any
architectural change - read this before writing a new `DataProvider`
implementation or preparing a CSV export.

## 1. The interface every source implements

Every data source is a `DataProvider` (frozen, Phase 2,
`src/etf_platform/data_engine/providers/base.py`) - never modified, only
implemented. Four methods:

- `name` (property): a short, stable identifier used as the `source` tag on every bar it produces.
- `fetch_ohlcv(symbol, start, end) -> list[OHLCVBar]`
- `fetch_corporate_actions(symbol, start, end) -> list[CorporateAction]`
- `fetch_instrument_master() -> list[InstrumentMeta]`

`BacktestEngine` (frozen, Phase 4) never imports or depends on any
concrete provider - it only ever consumes the `dict[str, list[OHLCVBar]]`
shape a provider's `fetch_ohlcv()` produces. This was already true before
this module existed (confirmed by testing `CSVDataProvider` output
directly against the unmodified engine) - the guide below exists so a new
source produces *correct* data for that shape, not to change what the
engine depends on.

## 2. Supported formats today

- **CSV**, via `CSVDataProvider` - the only concrete file-based
  implementation built so far. See section 3 for the exact format.
- **A future API provider** - anything implementing `DataProvider`.
  `NSEProvider` and `KiteProvider` already exist (Phase 2, frozen) but are
  designed for recent/live data, not multi-decade historical depth; a
  paid vendor's historical API would be a new class following the same
  pattern, explicitly out of scope for this module per your instruction
  (no provider-specific integrations requiring paid services).

## 3. CSV format (required)

One file per symbol, named `{SYMBOL}.csv`, placed in a directory passed
to `CSVDataProvider(data_dir=...)`.

**Required columns** (`historical_validation/csv_data_provider.py`,
`REQUIRED_COLUMNS`): `date, open, high, low, close, volume`

**Optional column**: `adjusted_close` - if absent, `close` is used as
`adjusted_close`. For any period with dividends, splits, or bonuses, an
explicit `adjusted_close` is strongly preferred; without it, the raw
close is used unadjusted, which will distort return calculations across
a corporate action.

**Date format**: `YYYY-MM-DD`, strictly. Any other format raises
`DataProviderError` immediately, not a silent misparse.

**Row ordering**: not required to arrive pre-sorted - `CSVDataProvider`
sorts before returning. (`ordering_check.py`'s chronological-ordering
check exists for a *different* purpose: detecting an unexpected upstream
pipeline defect, run explicitly via `validate_and_gate`/
`ValidatedDataProvider`, not by `CSVDataProvider` itself.)

**Corporate actions** (optional): a second directory of
`{SYMBOL}.csv` files with columns `ex_date, action_type, ratio_or_amount`,
where `action_type` is one of `dividend`, `split`, `bonus`, `merger`,
`other` (`CorporateActionType`, frozen). Passed to `CSVDataProvider` via
`corporate_actions_dir=...`.

## 4. Timezone assumptions

All dates in this module are **naive `date` objects, not `datetime`** -
there is no time-of-day or timezone component at the CSV/provider layer.
This is deliberate and matches how Phase 2's `OHLCVBar` already models
daily bars. If a future intraday data source is ever added, that would
need `datetime` with explicit timezone handling (see
`execution_manager/timezone_utils.py`'s UTC-internal/IST-at-boundary
discipline, built for a different, live-trading purpose but the same
principle would apply) - out of scope for this module today.

## 5. Calendar assumptions

`CSVDataProvider` does not itself know or enforce the NSE trading
calendar - it loads whatever rows exist in the file, in the requested
date range. **Calendar alignment is a validation-layer concern, not a
provider-layer one**: `validate_and_gate()` (reused via
`ValidatedDataProvider`) accepts an optional `holidays` parameter and
runs Phase 2's frozen `check_missing_trading_days` check against it. A
CSV missing expected trading days will pass through `CSVDataProvider`
silently but will be flagged (and, if critical, aborted) by
`ValidatedDataProvider` - always wrap a new provider in
`ValidatedDataProvider` for anything feeding a real analysis, not
`CSVDataProvider` alone.

## 6. Corporate-action handling

Corporate actions are informational context for the frozen
`DataQualityValidator`'s price-jump check (an unexplained large move
without a matching corporate action is flagged as critical) - they are
**not** automatically applied to adjust prices at the provider layer.
If a CSV's `close` column is not already corporate-action-adjusted, any
large legitimate move (e.g. a stock split) needs either: (a) an
`adjusted_close` column reflecting the true adjusted series, or (b) a
corresponding corporate action record so the price-jump check doesn't
flag it as a data-quality failure. This module does not currently ADJUST
raw prices for corporate actions itself - a genuine gap for any real
future integration where only unadjusted close prices are available.

## 7. Benchmark mapping

Benchmark mapping (which index proxies which ETF) is **an explicit,
external assumption this module does not verify** - flagged in
`provenance.py`'s adversarial-review disclosure. When registering an
index-proxy segment via `HistoricalDataAcquisitionService.register()`,
the caller is asserting the correct index was chosen; nothing in this
module cross-checks that assertion. `verified_etf_records.py` documents
the correct, web-search-verified benchmark for each of the five mandatory
symbols (e.g. NIFTYBEES -> Nifty 50 TRI) - use it as the source of truth
for benchmark assignment rather than guessing.

## 8. Validation requirements (mandatory)

Every provider feeding a real historical analysis **must** be wrapped in
`ValidatedDataProvider`, which runs, on every `fetch_ohlcv()` call:

1. `ordering_check.check_chronological_ordering` (new, this module)
2. Phase 2's frozen `DataQualityValidator` (no-data, OHLC consistency,
   negative/zero prices, duplicates, price jumps, staleness, missing
   trading days)

Any CRITICAL issue raises `DataIntegrityAbortedError` - the pipeline
halts, it does not silently continue with bad data. This is not
optional or best-effort: `ValidatedDataProvider.fetch_ohlcv()` has no
parameter to suppress it. (Phase 2's underlying validator does support a
narrow `force=True` override for a genuine false positive, but
`ValidatedDataProvider` does not currently expose that escape hatch -
deliberately, since exposing it without a `force_reason` audit trail at
this layer would weaken the fail-safe default; if a real false positive
is ever hit, that's a signal to extend `ValidatedDataProvider`
deliberately, not to bypass it silently.)

## 9. Provenance requirements (mandatory)

Every dataset used in an analysis must be registered through
`HistoricalDataAcquisitionService.register(symbol, start, end, provider,
source)`, where `source` is a `provenance.DataSource` value
(`ETF_ACTUAL`, `INDEX_PROXY`, or `SYNTHETIC`). Registering two
overlapping date ranges for the same symbol raises immediately (at
`register()` time, before any provider is even called) - this is the
structural enforcement of "never mix datasets with different provenance
without explicit labeling": it is not possible to accidentally create an
ambiguous, unlabeled blend of two sources for the same date.

## 10. What happens when real data becomes available

Nothing architectural changes. The sequence:

1. Obtain real historical data (from wherever - a paid vendor export, an
   NSE bhavcopy archive processed into CSV, a future credentialed API).
2. Format it per section 3, or write a new `DataProvider` implementation
   if it's API-based rather than file-based.
3. Wrap it in `ValidatedDataProvider` (and `IndexProxyDataProvider` if
   it's proxy data for a pre-inception period).
4. Register it with `HistoricalDataAcquisitionService`.
5. Call `fetch_all()` and pass the result straight into `BacktestEngine`
   - exactly as already demonstrated end-to-end against `CSVDataProvider`
   in this module's test suite, with the real, frozen, unmodified engine.

No change to `BacktestEngine`, `StrategyEngine`, `performance_analytics`,
`validation`, or any other frozen module is required at any point in this
sequence.
