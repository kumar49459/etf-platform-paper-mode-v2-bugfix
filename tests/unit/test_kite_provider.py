"""Unit tests for KiteProvider, using a mocked requests.Session and a fake
SecretsManager — no real network calls or real Kite credentials involved."""

from __future__ import annotations

import unittest
from datetime import date
from unittest import mock

import requests

from etf_platform.data_engine.exceptions import DataProviderError, SymbolResolutionError
from etf_platform.data_engine.providers.kite_provider import KiteProvider
from etf_platform.data_engine.rate_limiter import RateLimiter


class FakeSecretsManager:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def get_secret(self, name: str) -> str:
        return self._values[name]


class FakeSymbolResolver:
    def __init__(self, mapping: dict[str, int]) -> None:
        self._mapping = mapping

    def resolve(self, symbol: str) -> int:
        return self._mapping[symbol.upper()]


class TestKiteProviderOHLCV(unittest.TestCase):
    def setUp(self) -> None:
        self.rate_limiter = RateLimiter(calls_per_second=1000.0, calls_per_minute=100000.0)
        self.secrets = FakeSecretsManager({"kite_api_key": "fake_key", "kite_access_token": "fake_token"})
        self.session = mock.create_autospec(requests.Session, instance=True)
        self.provider = KiteProvider(
            rate_limiter=self.rate_limiter, secrets_manager=self.secrets, session=self.session,
            retry_sleep_fn=lambda s: None,
        )

    def test_fetch_ohlcv_without_resolver_raises(self) -> None:
        with self.assertRaises(SymbolResolutionError):
            self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 1), date(2026, 1, 2))

    def test_fetch_ohlcv_parses_candles(self) -> None:
        self.provider.attach_symbol_resolver(FakeSymbolResolver({"NIFTYBEES": 123456}))
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = {
            "data": {
                "candles": [
                    ["2026-01-02T00:00:00+0530", 250.0, 252.0, 249.0, 251.0, 10000],
                    ["2026-01-03T00:00:00+0530", 251.0, 253.0, 250.0, 252.0, 12000],
                ]
            }
        }
        self.session.get.return_value = response

        bars = self.provider.fetch_ohlcv("NIFTYBEES", date(2026, 1, 2), date(2026, 1, 3))
        self.assertEqual(len(bars), 2)
        self.assertEqual(bars[0].close, 251.0)
        self.assertEqual(bars[1].volume, 12000)

        called_url = self.session.get.call_args.args[0]
        self.assertIn("123456", called_url)

    def test_auth_header_uses_api_key_and_access_token(self) -> None:
        self.provider.attach_symbol_resolver(FakeSymbolResolver({"X": 1}))
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = {"data": {"candles": []}}
        self.session.get.return_value = response

        self.provider.fetch_ohlcv("X", date(2026, 1, 2), date(2026, 1, 2))
        headers = self.session.get.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "token fake_key:fake_token")

    def test_http_error_raises_data_provider_error(self) -> None:
        self.provider.attach_symbol_resolver(FakeSymbolResolver({"X": 1}))
        self.session.get.side_effect = requests.ConnectionError("down")
        with self.assertRaises(DataProviderError):
            self.provider.fetch_ohlcv("X", date(2026, 1, 2), date(2026, 1, 2))

    def test_malformed_candle_skipped(self) -> None:
        self.provider.attach_symbol_resolver(FakeSymbolResolver({"X": 1}))
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.json.return_value = {"data": {"candles": [["bad-data"]]}}
        self.session.get.return_value = response
        bars = self.provider.fetch_ohlcv("X", date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(bars, [])


class TestKiteProviderInstrumentMaster(unittest.TestCase):
    def setUp(self) -> None:
        self.rate_limiter = RateLimiter(calls_per_second=1000.0, calls_per_minute=100000.0)
        self.secrets = FakeSecretsManager({"kite_api_key": "k", "kite_access_token": "t"})
        self.session = mock.create_autospec(requests.Session, instance=True)
        self.provider = KiteProvider(
            rate_limiter=self.rate_limiter, secrets_manager=self.secrets, session=self.session,
            retry_sleep_fn=lambda s: None,
        )

    def test_fetch_instrument_master_filters_to_nse_bse(self) -> None:
        csv_text = (
            "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
            "tick_size,lot_size,instrument_type,segment,exchange\n"
            "123456,482,NIFTYBEES,Nifty BeES,250.0,,0,0.05,1,EQ,NSE,NSE\n"
            "999999,111,SOMEFUT,Some Future,100.0,2026-01-29,0,0.05,50,FUT,NFO-FUT,NFO\n"
        )
        response = mock.Mock()
        response.raise_for_status = mock.Mock()
        response.text = csv_text
        self.session.get.return_value = response

        instruments = self.provider.fetch_instrument_master()
        self.assertEqual(len(instruments), 1)
        self.assertEqual(instruments[0].symbol, "NIFTYBEES")
        self.assertEqual(instruments[0].instrument_token, 123456)


    def test_transient_error_retried_then_succeeds(self) -> None:
        self.provider.attach_symbol_resolver(FakeSymbolResolver({"X": 1}))
        good_response = mock.Mock()
        good_response.raise_for_status = mock.Mock()
        good_response.json.return_value = {"data": {"candles": []}}
        self.session.get.side_effect = [requests.Timeout("slow"), good_response]
        self.provider.fetch_ohlcv("X", date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(self.session.get.call_count, 2)

    def test_429_rate_limit_is_retried(self) -> None:
        self.provider.attach_symbol_resolver(FakeSymbolResolver({"X": 1}))
        response_429 = mock.Mock()
        response_429.raise_for_status.side_effect = requests.HTTPError(response=mock.Mock(status_code=429))
        good_response = mock.Mock()
        good_response.raise_for_status = mock.Mock()
        good_response.json.return_value = {"data": {"candles": []}}
        self.session.get.side_effect = [response_429, good_response]
        self.provider.fetch_ohlcv("X", date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(self.session.get.call_count, 2)

    def test_401_auth_error_not_retried(self) -> None:
        self.provider.attach_symbol_resolver(FakeSymbolResolver({"X": 1}))
        response_401 = mock.Mock()
        response_401.raise_for_status.side_effect = requests.HTTPError(response=mock.Mock(status_code=401))
        self.session.get.return_value = response_401
        with self.assertRaises(DataProviderError):
            self.provider.fetch_ohlcv("X", date(2026, 1, 2), date(2026, 1, 2))
        self.assertEqual(self.session.get.call_count, 1)

    def test_close_closes_session(self) -> None:
        self.provider.close()
        self.session.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
