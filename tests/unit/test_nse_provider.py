"""Unit tests for NSEProvider, using a mocked requests.Session — no real
network calls (this sandbox has no network access, and even in a networked
environment, provider tests should not depend on NSE's live availability).
"""

from __future__ import annotations

import csv
import io
import unittest
import zipfile
from datetime import date
from unittest import mock

import requests

from etf_platform.data_engine.exceptions import DataProviderError
from etf_platform.data_engine.providers.nse_provider import NSEProvider
from etf_platform.data_engine.rate_limiter import RateLimiter


def make_bhavcopy_zip_response(rows: list[dict]) -> mock.Mock:
    csv_buffer = io.StringIO()
    fieldnames = ["SYMBOL", "SERIES", "OPEN", "HIGH", "LOW", "CLOSE", "LAST", "PREVCLOSE", "TOTTRDQTY"]
    writer = csv.DictWriter(csv_buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w") as archive:
        archive.writestr("cm02JAN2026bhav.csv", csv_buffer.getvalue())

    response = mock.Mock()
    response.content = zip_buffer.getvalue()
    response.raise_for_status = mock.Mock()
    return response


class TestNSEProvider(unittest.TestCase):
    def setUp(self) -> None:
        self.rate_limiter = RateLimiter(calls_per_second=1000.0, calls_per_minute=100000.0)
        self.session = mock.create_autospec(requests.Session, instance=True)
        self.session.headers = {}
        self.provider = NSEProvider(
            rate_limiter=self.rate_limiter, session=self.session, retry_sleep_fn=lambda s: None
        )

    def test_fetch_ohlcv_parses_matching_symbol(self) -> None:
        self.session.get.return_value = make_bhavcopy_zip_response(
            [
                {
                    "SYMBOL": "NIFTYBEES", "SERIES": "EQ", "OPEN": "250.0", "HIGH": "252.0",
                    "LOW": "249.0", "CLOSE": "251.0", "LAST": "251.0", "PREVCLOSE": "250.5",
                    "TOTTRDQTY": "10000",
                },
                {
                    "SYMBOL": "GOLDBEES", "SERIES": "EQ", "OPEN": "50.0", "HIGH": "51.0",
                    "LOW": "49.5", "CLOSE": "50.5", "LAST": "50.5", "PREVCLOSE": "50.0",
                    "TOTTRDQTY": "5000",
                },
            ]
        )
        bars = self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].symbol, "NIFTYBEES")
        self.assertEqual(bars[0].close, 251.0)
        self.assertEqual(bars[0].volume, 10000)

    def test_fetch_ohlcv_skips_weekends(self) -> None:
        # Jan 3-4 2026 are Sat/Sun; only Jan 2 (Fri) and Jan 5 (Mon) should
        # trigger an HTTP call.
        self.session.get.return_value = make_bhavcopy_zip_response(
            [{"SYMBOL": "NIFTYBEES", "SERIES": "EQ", "OPEN": "1", "HIGH": "1", "LOW": "1",
              "CLOSE": "1", "LAST": "1", "PREVCLOSE": "1", "TOTTRDQTY": "1"}]
        )
        self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 2), date(2026, 1, 5))
        self.assertEqual(self.session.get.call_count, 2)  # Fri + Mon only

    def test_http_error_raises_data_provider_error_but_does_not_crash_range_fetch(self) -> None:
        self.session.get.side_effect = requests.ConnectionError("network down")
        # Should not raise — provider logs and skips days it can't fetch,
        # returning whatever it could get (empty here), so the caller
        # (HistoricalDataEngine) can fall back to the secondary provider
        # rather than the whole ingestion run crashing on one bad day.
        bars = self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(bars, [])

    def test_bad_zip_file_is_handled_gracefully(self) -> None:
        response = mock.Mock()
        response.content = b"not a zip file"
        response.raise_for_status = mock.Mock()
        self.session.get.return_value = response
        bars = self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(bars, [])

    def test_start_after_end_raises_valueerror(self) -> None:
        with self.assertRaises(ValueError):
            self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 5), date(2026, 1, 2))

    def test_provider_name(self) -> None:
        self.assertEqual(self.provider.name, "nse")

    def test_malformed_row_is_skipped_not_fatal(self) -> None:
        self.session.get.return_value = make_bhavcopy_zip_response(
            [{"SYMBOL": "NIFTYBEES", "SERIES": "EQ", "OPEN": "not-a-number", "HIGH": "1",
              "LOW": "1", "CLOSE": "1", "LAST": "1", "PREVCLOSE": "1", "TOTTRDQTY": "1"}]
        )
        bars = self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(bars, [])  # malformed row skipped, no crash


    def test_transient_connection_error_is_retried_then_succeeds(self) -> None:
        good_response = make_bhavcopy_zip_response(
            [{"SYMBOL": "NIFTYBEES", "SERIES": "EQ", "OPEN": "1", "HIGH": "1", "LOW": "1",
              "CLOSE": "1", "LAST": "1", "PREVCLOSE": "1", "TOTTRDQTY": "1"}]
        )
        self.session.get.side_effect = [requests.ConnectionError("blip"), requests.ConnectionError("blip"), good_response]
        bars = self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(len(bars), 1)
        self.assertEqual(self.session.get.call_count, 3)

    def test_retries_exhausted_raises_data_provider_error_internally_but_fetch_continues(self) -> None:
        self.session.get.side_effect = requests.ConnectionError("persistent failure")
        bars = self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 2), date(2026, 1, 2))
        # Default max_retry_attempts=3 -> exactly 3 attempts for the one trading day.
        self.assertEqual(self.session.get.call_count, 3)
        self.assertEqual(bars, [])

    def test_http_404_is_not_retried(self) -> None:
        response_404 = mock.Mock()
        http_error = requests.HTTPError(response=mock.Mock(status_code=404))
        response_404.raise_for_status.side_effect = http_error
        self.session.get.return_value = response_404
        self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 2), date(2026, 1, 2))
        # 404 is a client error, not retryable — exactly 1 attempt, not 3.
        self.assertEqual(self.session.get.call_count, 1)

    def test_close_closes_underlying_session(self) -> None:
        self.provider.close()
        self.session.close.assert_called_once()

    def test_context_manager_closes_session(self) -> None:
        with NSEProvider(rate_limiter=self.rate_limiter, session=self.session) as provider:
            self.assertIsNotNone(provider)
        self.session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
