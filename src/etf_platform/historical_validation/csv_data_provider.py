"""CSVDataProvider: a new implementation of Phase 2's frozen DataProvider
interface (data_engine/providers/base.py), for local CSV files.

This is exactly the extension point Phase 1 section 12.6 and the frozen
DataProvider docstring already anticipated: "implemented by NSEProvider,
KiteProvider, and -- later -- a paid vendor adapter, without any change
to callers." A CSV file is not a "paid vendor adapter," but the principle
is identical -- a new DataProvider implementation, zero changes to the
interface, zero changes to anything that consumes it (including
BacktestEngine, which already only depends on the dict[str,
list[OHLCVBar]] shape any DataProvider.fetch_ohlcv() produces).

This is the provider a real historical dataset (once acquired, from
wherever) gets loaded through -- export it to CSV in the documented
format (see docs/DATA_SOURCE_INTEGRATION_GUIDE.md), point this provider
at the file, and nothing else in this platform needs to change.
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from etf_platform.data_engine.exceptions import DataProviderError
from etf_platform.data_engine.models import CorporateAction, CorporateActionType, InstrumentMeta, OHLCVBar
from etf_platform.data_engine.providers.base import DataProvider

REQUIRED_COLUMNS = ("date", "open", "high", "low", "close", "volume")
"""Exactly documented in the integration guide -- this is the contract,
not an implementation detail. adjusted_close is optional; if absent,
close is used as adjusted_close."""


class CSVDataProvider(DataProvider):
    def __init__(self, data_dir, corporate_actions_dir=None, provider_name="csv"):
        self._data_dir = Path(data_dir)
        self._corporate_actions_dir = Path(corporate_actions_dir) if corporate_actions_dir else None
        self._provider_name = provider_name

    @property
    def name(self):
        return self._provider_name

    def fetch_ohlcv(self, symbol, start, end):
        path = self._data_dir / f"{symbol}.csv"
        if not path.exists():
            raise DataProviderError(f"CSVDataProvider: no file found for {symbol!r} at {path}")

        bars = []
        try:
            with open(path, newline="") as f:
                reader = csv.DictReader(f)
                missing = set(REQUIRED_COLUMNS) - set(reader.fieldnames or [])
                if missing:
                    raise DataProviderError(
                        f"CSVDataProvider: {path} is missing required column(s) {sorted(missing)}. "
                        f"See docs/DATA_SOURCE_INTEGRATION_GUIDE.md for the required format."
                    )
                for row_num, row in enumerate(reader, start=2):
                    try:
                        trade_date = _parse_date(row["date"])
                    except ValueError as exc:
                        raise DataProviderError(f"CSVDataProvider: {path} row {row_num}: unparseable date {row['date']!r}") from exc
                    if trade_date < start or trade_date > end:
                        continue
                    try:
                        bar = OHLCVBar(
                            symbol=symbol, trade_date=trade_date,
                            open=float(row["open"]), high=float(row["high"]), low=float(row["low"]),
                            close=float(row["close"]), volume=int(float(row["volume"])),
                            adjusted_close=float(row["adjusted_close"]) if row.get("adjusted_close") else float(row["close"]),
                            source=self._provider_name,
                        )
                    except (ValueError, KeyError) as exc:
                        raise DataProviderError(f"CSVDataProvider: {path} row {row_num}: malformed data ({exc})") from exc
                    bars.append(bar)
        except OSError as exc:
            raise DataProviderError(f"CSVDataProvider: could not read {path}: {exc}") from exc

        bars.sort(key=lambda b: b.trade_date)
        return bars

    def fetch_corporate_actions(self, symbol, start, end):
        if self._corporate_actions_dir is None:
            return []
        path = self._corporate_actions_dir / f"{symbol}.csv"
        if not path.exists():
            return []
        actions = []
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ex_date = _parse_date(row["ex_date"])
                if ex_date < start or ex_date > end:
                    continue
                actions.append(CorporateAction(
                    symbol=symbol, ex_date=ex_date,
                    action_type=CorporateActionType(row["action_type"]),
                    ratio_or_amount=float(row["ratio_or_amount"]), source=self._provider_name,
                ))
        return actions

    def fetch_instrument_master(self):
        instruments = []
        if not self._data_dir.exists():
            return instruments
        for path in sorted(self._data_dir.glob("*.csv")):
            instruments.append(InstrumentMeta(symbol=path.stem, name=path.stem, exchange="CSV"))
        return instruments


def _parse_date(value):
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()
