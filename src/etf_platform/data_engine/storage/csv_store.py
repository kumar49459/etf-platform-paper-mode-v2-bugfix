"""CSV-based TimeSeriesStore.

This is the default, always-available backend — no extra dependency beyond
the stdlib `csv` module. It is what this sandbox's tests actually exercise
end-to-end. ParquetTimeSeriesStore (same interface) is preferred in a real
deployment for compression and columnar read performance once `pyarrow` is
installed — see factory.py for the auto-selection logic, and the module
docstring there for why CSV is the safe, always-correct fallback rather than
a compromise.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

from etf_platform.data_engine.exceptions import StorageError
from etf_platform.data_engine.models import CorporateAction, CorporateActionType, OHLCVBar
from etf_platform.data_engine.storage.base import TimeSeriesStore

_OHLCV_FIELDS = ["symbol", "trade_date", "open", "high", "low", "close", "volume", "adjusted_close", "source"]
_CA_FIELDS = ["symbol", "ex_date", "action_type", "ratio_or_amount", "source"]


class CSVTimeSeriesStore(TimeSeriesStore):
    """Default, dependency-free TimeSeriesStore implementation using stdlib csv. Selected automatically by storage_backend='auto' when pyarrow is not installed."""
    def __init__(self, base_path: str | Path) -> None:
        self._base_path = Path(base_path)

    def _ohlcv_path(self, snapshot_id: str, symbol: str) -> Path:
        return self._base_path / snapshot_id / "ohlcv" / f"{symbol.upper()}.csv"

    def _ca_path(self, snapshot_id: str, symbol: str) -> Path:
        return self._base_path / snapshot_id / "corporate_actions" / f"{symbol.upper()}.csv"

    def write_ohlcv(self, snapshot_id: str, symbol: str, bars: list[OHLCVBar]) -> None:
        path = self._ohlcv_path(snapshot_id, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=_OHLCV_FIELDS)
                writer.writeheader()
                for bar in sorted(bars, key=lambda b: b.trade_date):
                    writer.writerow(
                        {
                            "symbol": bar.symbol,
                            "trade_date": bar.trade_date.isoformat(),
                            "open": bar.open,
                            "high": bar.high,
                            "low": bar.low,
                            "close": bar.close,
                            "volume": bar.volume,
                            "adjusted_close": bar.adjusted_close if bar.adjusted_close is not None else "",
                            "source": bar.source,
                        }
                    )
        except OSError as exc:
            raise StorageError(f"Failed to write OHLCV for {symbol} in snapshot {snapshot_id}: {exc}") from exc

    def read_ohlcv(self, snapshot_id: str, symbol: str, start: date, end: date) -> list[OHLCVBar]:
        path = self._ohlcv_path(snapshot_id, symbol)
        if not path.exists():
            return []
        bars: list[OHLCVBar] = []
        with path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                trade_date = date.fromisoformat(row["trade_date"])
                if not (start <= trade_date <= end):
                    continue
                bars.append(
                    OHLCVBar(
                        symbol=row["symbol"],
                        trade_date=trade_date,
                        open=float(row["open"]),
                        high=float(row["high"]),
                        low=float(row["low"]),
                        close=float(row["close"]),
                        volume=int(row["volume"]),
                        adjusted_close=float(row["adjusted_close"]) if row["adjusted_close"] else None,
                        source=row["source"],
                    )
                )
        return bars

    def write_corporate_actions(self, snapshot_id: str, symbol: str, actions: list[CorporateAction]) -> None:
        path = self._ca_path(snapshot_id, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=_CA_FIELDS)
                writer.writeheader()
                for action in actions:
                    writer.writerow(
                        {
                            "symbol": action.symbol,
                            "ex_date": action.ex_date.isoformat(),
                            "action_type": action.action_type.value,
                            "ratio_or_amount": action.ratio_or_amount,
                            "source": action.source,
                        }
                    )
        except OSError as exc:
            raise StorageError(
                f"Failed to write corporate actions for {symbol} in snapshot {snapshot_id}: {exc}"
            ) from exc

    def read_corporate_actions(self, snapshot_id: str, symbol: str) -> list[CorporateAction]:
        path = self._ca_path(snapshot_id, symbol)
        if not path.exists():
            return []
        actions: list[CorporateAction] = []
        with path.open("r", newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                actions.append(
                    CorporateAction(
                        symbol=row["symbol"],
                        ex_date=date.fromisoformat(row["ex_date"]),
                        action_type=CorporateActionType(row["action_type"]),
                        ratio_or_amount=float(row["ratio_or_amount"]),
                        source=row["source"],
                    )
                )
        return actions
