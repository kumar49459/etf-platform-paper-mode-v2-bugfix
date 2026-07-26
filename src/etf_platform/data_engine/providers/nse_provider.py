"""NSE (National Stock Exchange of India) data provider — primary source,
per Phase 1 §12.6.

IMPORTANT / HONEST LIMITATION: this sandbox has no network access, so the
HTTP calls below are structured and unit-tested against *mocked* responses,
not verified against NSE's live archive endpoints. NSE's exact bhavcopy URL
pattern and CSV column names have changed before and should be re-verified
against https://www.nseindia.com/all-reports at deployment time before the
first real ingestion run — treat the constants below as a documented
starting point, not a guarantee. This is exactly the kind of assumption the
Data Quality Validator (next module) exists to catch if it's wrong: a
malformed or empty parse will surface as a CRITICAL "no data returned" issue
rather than silently producing zero rows.

Design: an `requests.Session` is injected (defaults to a real one), and a
`RateLimiter` is injected — both for testability and so the caller
(HistoricalDataEngine) controls the actual rate-limit budget from config,
not this class.
"""

from __future__ import annotations

import csv
import io
import time
import zipfile
from datetime import date, timedelta

import requests

from etf_platform.common.logging_setup import get_logger
from etf_platform.common.retry import RetryExhaustedError, retry_with_backoff
from etf_platform.data_engine.exceptions import DataProviderError
from etf_platform.data_engine.models import CorporateAction, CorporateActionType, InstrumentMeta, OHLCVBar
from etf_platform.data_engine.providers.base import DataProvider
from etf_platform.data_engine.rate_limiter import RateLimiter

logger = get_logger("data_engine.providers.nse")

_MONTH_ABBR = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

BHAVCOPY_URL_TEMPLATE = (
    "https://archives.nseindia.com/content/historical/EQUITIES/"
    "{year}/{month_abbr}/cm{day:02d}{month_abbr}{year}bhav.csv.zip"
)


def _is_retryable_request_error(exc: Exception) -> bool:
    """Retry connection/timeout errors and 5xx responses; do NOT retry 4xx
    (bad request, not found, auth failure) — those will fail identically
    every time and retrying only burns rate-limit budget and time before
    HistoricalDataEngine's fallback-to-secondary-provider path can kick in.
    """
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return 500 <= exc.response.status_code < 600
    return False


class NSEProvider(DataProvider):
    """Primary data source: NSE bhavcopy archives. See module docstring for the honest live-endpoint verification caveat."""
    def __init__(
        self,
        rate_limiter: RateLimiter,
        session: requests.Session | None = None,
        timeout_seconds: float = 20.0,
        max_retry_attempts: int = 3,
        retry_sleep_fn=None,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._session = session or requests.Session()
        self._session.headers.update(
            {"User-Agent": "Mozilla/5.0 (compatible; etf-platform-data-engine/0.2)"}
        )
        self._timeout = timeout_seconds
        self._max_retry_attempts = max_retry_attempts
        self._retry_sleep_fn = retry_sleep_fn or time.sleep

    def close(self) -> None:
        """Release the underlying HTTP connection pool. Call this (or use
        NSEProvider as a context manager) when done with the provider —
        important on the resource-constrained live micro instance where an
        unclosed session's connection pool is needless held-open sockets."""
        self._session.close()

    def __enter__(self) -> "NSEProvider":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def name(self) -> str:
        return "nse"

    def _bhavcopy_url(self, trade_date: date) -> str:
        return BHAVCOPY_URL_TEMPLATE.format(
            year=trade_date.year,
            month_abbr=_MONTH_ABBR[trade_date.month],
            day=trade_date.day,
        )

    def _fetch_bhavcopy_csv_rows(self, trade_date: date) -> list[dict[str, str]]:
        self._rate_limiter.acquire()
        url = self._bhavcopy_url(trade_date)

        def do_request() -> requests.Response:
            response = self._session.get(url, timeout=self._timeout)
            response.raise_for_status()
            return response

        try:
            response = retry_with_backoff(
                do_request,
                is_retryable=_is_retryable_request_error,
                max_attempts=self._max_retry_attempts,
                sleep_fn=self._retry_sleep_fn,
            )
        except (requests.RequestException, RetryExhaustedError) as exc:
            raise DataProviderError(f"NSE bhavcopy request failed for {trade_date}: {exc}") from exc

        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                inner_name = archive.namelist()[0]
                csv_bytes = archive.read(inner_name)
        except zipfile.BadZipFile as exc:
            raise DataProviderError(
                f"NSE bhavcopy for {trade_date} was not a valid zip file "
                "(market holiday, or NSE archive URL/format has changed — verify before relying on this)."
            ) from exc

        text = csv_bytes.decode("utf-8", errors="replace")
        return list(csv.DictReader(io.StringIO(text)))

    def fetch_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCVBar]:
        if start > end:
            raise ValueError(f"start ({start}) must be <= end ({end})")

        bars: list[OHLCVBar] = []
        current = start
        while current <= end:
            if current.weekday() < 5:  # skip weekends; holiday calendar is Phase 5+ scope
                try:
                    rows = self._fetch_bhavcopy_csv_rows(current)
                except DataProviderError as exc:
                    logger.warning("Skipping %s for %s: %s", symbol, current, exc)
                    current += timedelta(days=1)
                    continue

                for row in rows:
                    if row.get("SYMBOL", "").strip().upper() != symbol.upper():
                        continue
                    try:
                        bars.append(
                            OHLCVBar(
                                symbol=symbol.upper(),
                                trade_date=current,
                                open=float(row["OPEN"]),
                                high=float(row["HIGH"]),
                                low=float(row["LOW"]),
                                close=float(row["CLOSE"]),
                                volume=int(float(row["TOTTRDQTY"])),
                                adjusted_close=None,  # corporate-action adjustment is a
                                                        # Data Quality / downstream concern,
                                                        # not something the raw provider computes.
                                source=self.name,
                            )
                        )
                    except (KeyError, ValueError) as exc:
                        logger.warning(
                            "Malformed bhavcopy row for %s on %s, skipping: %s", symbol, current, exc
                        )
            current += timedelta(days=1)
        return bars

    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        # NSE publishes corporate actions via a separate reports endpoint. Left
        # as a documented stub returning an empty list — wiring this up
        # requires picking and verifying the exact current NSE endpoint, which
        # is out of scope for a network-less sandbox build. The interface and
        # call site are in place so this is a self-contained follow-up, not a
        # design change, when network access is available.
        logger.debug(
            "NSEProvider.fetch_corporate_actions is a documented stub for %s [%s, %s]; returning no results.",
            symbol, start, end,
        )
        return []

    def fetch_instrument_master(self) -> list[InstrumentMeta]:
        # NSE does not provide broker instrument tokens (that's Kite-specific,
        # see KiteProvider + SymbolResolver). This returns basic exchange-level
        # metadata only; same documented-stub caveat as above applies to the
        # exact securities-master endpoint used.
        logger.debug("NSEProvider.fetch_instrument_master is a documented stub; returning no results.")
        return []
