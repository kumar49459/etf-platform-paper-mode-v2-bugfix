"""Zerodha Kite Connect data provider — secondary source, per Phase 1 §12.6.

Same honest limitation as nse_provider.py: HTTP calls are structured and
unit-tested against mocked responses only, since this sandbox has no network
access to verify against the live Kite API. Endpoint shapes below follow
Kite Connect's documented historical-candle and instrument-dump APIs as of
Claude's training data; re-verify against https://kite.trade/docs/connect/v3/
before first live use, particularly given SEBI's 2026 compliance
requirements (static IP whitelisting, Algo ID tagging — see Phase 1 §12.5
Module 24, which is a separate, later module, not handled here).

KiteProvider needs instrument_token, not just a trading symbol, to fetch
historical candles — that's what SymbolResolver exists for. To avoid a
circular constructor dependency (SymbolResolver needs
`fetch_instrument_master`, KiteProvider needs a resolver), the resolver is
attached after construction via `attach_symbol_resolver()`, wired together
by HistoricalDataEngine at startup.
"""

from __future__ import annotations

import time
from datetime import date, datetime

import requests

from etf_platform.common.logging_setup import get_logger
from etf_platform.common.retry import RetryExhaustedError, retry_with_backoff
from etf_platform.data_engine.exceptions import DataProviderError, SymbolResolutionError
from etf_platform.data_engine.models import CorporateAction, InstrumentMeta, OHLCVBar
from etf_platform.data_engine.providers.base import DataProvider
from etf_platform.data_engine.rate_limiter import RateLimiter
from etf_platform.secrets_manager import SecretsManager

logger = get_logger("data_engine.providers.kite")

KITE_API_BASE = "https://api.kite.trade"


def _is_retryable_request_error(exc: Exception) -> bool:
    """Same policy as NSEProvider — see that module for the rationale.
    Additionally, Kite's documented 429 (rate limit exceeded) is treated as
    retryable: our own RateLimiter should prevent this in practice, but a
    429 arriving anyway (e.g. clock drift, another process sharing the same
    API key) is exactly the transient case backoff exists for."""
    if isinstance(exc, (requests.ConnectionError, requests.Timeout)):
        return True
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        status = exc.response.status_code
        return status == 429 or 500 <= status < 600
    return False


class KiteProvider(DataProvider):
    """Secondary data source: Zerodha Kite Connect. Requires a SymbolResolver (see attach_symbol_resolver) for instrument_token resolution."""
    def __init__(
        self,
        rate_limiter: RateLimiter,
        secrets_manager: SecretsManager,
        api_key_secret_name: str = "kite_api_key",
        access_token_secret_name: str = "kite_access_token",
        session: requests.Session | None = None,
        timeout_seconds: float = 20.0,
        max_retry_attempts: int = 3,
        retry_sleep_fn=None,
    ) -> None:
        self._rate_limiter = rate_limiter
        self._secrets_manager = secrets_manager
        self._api_key_secret_name = api_key_secret_name
        self._access_token_secret_name = access_token_secret_name
        self._session = session or requests.Session()
        self._timeout = timeout_seconds
        self._max_retry_attempts = max_retry_attempts
        self._retry_sleep_fn = retry_sleep_fn or time.sleep
        self._symbol_resolver = None  # attached later; see attach_symbol_resolver()

    def close(self) -> None:
        """Release the underlying HTTP connection pool — see NSEProvider.close()
        for the same rationale (matters on the resource-constrained live micro)."""
        self._session.close()

    def __enter__(self) -> "KiteProvider":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    @property
    def name(self) -> str:
        return "kite"

    def attach_symbol_resolver(self, resolver) -> None:
        """Wire in a SymbolResolver. Not a constructor arg — see module docstring."""
        self._symbol_resolver = resolver

    def _auth_headers(self) -> dict[str, str]:
        api_key = self._secrets_manager.get_secret(self._api_key_secret_name)
        access_token = self._secrets_manager.get_secret(self._access_token_secret_name)
        return {
            "Authorization": f"token {api_key}:{access_token}",
            "X-Kite-Version": "3",
        }

    def fetch_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCVBar]:
        if self._symbol_resolver is None:
            raise SymbolResolutionError(
                "KiteProvider has no SymbolResolver attached; call attach_symbol_resolver() "
                "before fetching OHLCV by trading symbol."
            )
        instrument_token = self._symbol_resolver.resolve(symbol)

        self._rate_limiter.acquire()
        url = f"{KITE_API_BASE}/instruments/historical/{instrument_token}/day"
        params = {"from": start.isoformat(), "to": end.isoformat()}

        def do_request() -> requests.Response:
            response = self._session.get(
                url, headers=self._auth_headers(), params=params, timeout=self._timeout
            )
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
            raise DataProviderError(
                f"Kite historical candle request failed for {symbol} (token={instrument_token}): {exc}"
            ) from exc

        payload = response.json()
        candles = payload.get("data", {}).get("candles", [])
        bars: list[OHLCVBar] = []
        for candle in candles:
            try:
                ts, o, h, l, c, v = candle[:6]
                trade_date = datetime.fromisoformat(ts).date()
                bars.append(
                    OHLCVBar(
                        symbol=symbol.upper(),
                        trade_date=trade_date,
                        open=float(o),
                        high=float(h),
                        low=float(l),
                        close=float(c),
                        volume=int(v),
                        adjusted_close=None,
                        source=self.name,
                    )
                )
            except (ValueError, IndexError, TypeError) as exc:
                logger.warning("Malformed Kite candle for %s, skipping: %s (%s)", symbol, candle, exc)
        return bars

    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction]:
        # Kite Connect does not expose a general corporate-actions REST
        # endpoint in the same shape as OHLCV/instruments. Documented stub,
        # same rationale as NSEProvider — see that module's docstring.
        logger.debug(
            "KiteProvider.fetch_corporate_actions is a documented stub for %s [%s, %s]; returning no results.",
            symbol, start, end,
        )
        return []

    def fetch_instrument_master(self) -> list[InstrumentMeta]:
        self._rate_limiter.acquire()
        url = f"{KITE_API_BASE}/instruments"

        def do_request() -> requests.Response:
            response = self._session.get(url, headers=self._auth_headers(), timeout=self._timeout)
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
            raise DataProviderError(f"Kite instrument master request failed: {exc}") from exc

        import csv
        import io

        reader = csv.DictReader(io.StringIO(response.text))
        instruments: list[InstrumentMeta] = []
        for row in reader:
            if row.get("exchange") not in {"NSE", "BSE"}:
                continue
            try:
                instruments.append(
                    InstrumentMeta(
                        symbol=row["tradingsymbol"].upper(),
                        name=row.get("name", ""),
                        exchange=row["exchange"],
                        instrument_token=int(row["instrument_token"]),
                        source=self.name,
                    )
                )
            except (KeyError, ValueError) as exc:
                logger.warning("Malformed Kite instrument row, skipping: %s (%s)", row, exc)
        return instruments
