"""Parquet-based TimeSeriesStore — preferred backend per Phase 1 §6, used
automatically when `pyarrow` is installed (see factory.py).

NOT covered by this sandbox's test run: `pyarrow` is unavailable here (no
network to install it), so this class is implemented to the same interface
and same behavior as CSVTimeSeriesStore, but is not exercised by the unit
test suite included in this delivery. It should be smoke-tested in an
environment with pyarrow installed (e.g. via `pip install pyarrow` and
re-running `tests/unit/test_storage_backends.py` with `RUN_PARQUET_TESTS=1`,
see that test file) before being relied on in production. I'm flagging this
explicitly rather than claiming test coverage that doesn't exist.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from etf_platform.data_engine.exceptions import StorageError
from etf_platform.data_engine.models import CorporateAction, CorporateActionType, OHLCVBar
from etf_platform.data_engine.storage.base import TimeSeriesStore


class ParquetTimeSeriesStore(TimeSeriesStore):
    """Preferred TimeSeriesStore implementation for production use; requires pyarrow. Selected automatically by storage_backend='auto' when pyarrow is installed."""
    def __init__(self, base_path: str | Path) -> None:
        self._base_path = Path(base_path)
        try:
            import pyarrow  # noqa: F401
        except ImportError as exc:
            raise StorageError(
                "ParquetTimeSeriesStore requires 'pyarrow'. Install it (see requirements-research.txt) "
                "or use storage_backend='csv' in config."
            ) from exc

    def _ohlcv_path(self, snapshot_id: str, symbol: str) -> Path:
        return self._base_path / snapshot_id / "ohlcv" / f"{symbol.upper()}.parquet"

    def _ca_path(self, snapshot_id: str, symbol: str) -> Path:
        return self._base_path / snapshot_id / "corporate_actions" / f"{symbol.upper()}.parquet"

    def write_ohlcv(self, snapshot_id: str, symbol: str, bars: list[OHLCVBar]) -> None:
        path = self._ohlcv_path(snapshot_id, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            [
                {
                    "symbol": b.symbol,
                    "trade_date": b.trade_date,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "adjusted_close": b.adjusted_close,
                    "source": b.source,
                }
                for b in sorted(bars, key=lambda b: b.trade_date)
            ]
        )
        try:
            df.to_parquet(path, index=False)
        except Exception as exc:  # noqa: BLE001 — pyarrow raises several exception types
            raise StorageError(f"Failed to write parquet OHLCV for {symbol} in {snapshot_id}: {exc}") from exc

    def read_ohlcv(self, snapshot_id: str, symbol: str, start: date, end: date) -> list[OHLCVBar]:
        path = self._ohlcv_path(snapshot_id, symbol)
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
        mask = (df["trade_date"] >= start) & (df["trade_date"] <= end)
        bars = []
        for row in df[mask].itertuples(index=False):
            bars.append(
                OHLCVBar(
                    symbol=row.symbol,
                    trade_date=row.trade_date,
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=int(row.volume),
                    adjusted_close=float(row.adjusted_close) if pd.notna(row.adjusted_close) else None,
                    source=row.source,
                )
            )
        return bars

    def write_corporate_actions(self, snapshot_id: str, symbol: str, actions: list[CorporateAction]) -> None:
        path = self._ca_path(snapshot_id, symbol)
        path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(
            [
                {
                    "symbol": a.symbol,
                    "ex_date": a.ex_date,
                    "action_type": a.action_type.value,
                    "ratio_or_amount": a.ratio_or_amount,
                    "source": a.source,
                }
                for a in actions
            ]
        )
        try:
            df.to_parquet(path, index=False)
        except Exception as exc:  # noqa: BLE001
            raise StorageError(
                f"Failed to write parquet corporate actions for {symbol} in {snapshot_id}: {exc}"
            ) from exc

    def read_corporate_actions(self, snapshot_id: str, symbol: str) -> list[CorporateAction]:
        path = self._ca_path(snapshot_id, symbol)
        if not path.exists():
            return []
        df = pd.read_parquet(path)
        actions = []
        for row in df.itertuples(index=False):
            actions.append(
                CorporateAction(
                    symbol=row.symbol,
                    ex_date=pd.Timestamp(row.ex_date).date(),
                    action_type=CorporateActionType(row.action_type),
                    ratio_or_amount=float(row.ratio_or_amount),
                    source=row.source,
                )
            )
        return actions
