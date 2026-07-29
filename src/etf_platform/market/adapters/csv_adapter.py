import csv
from pathlib import Path


class CSVAdapter:
    """
    CSV-based Historical Data Adapter.

    Reads OHLCV data from:

        data/historical/<SYMBOL>.csv
    """

    def __init__(self, data_directory="data/historical"):
        self._data_directory = Path(data_directory)

    def get_history(self, symbol):
        path = self._data_directory / f"{symbol.upper()}.csv"

        if not path.exists():
            raise FileNotFoundError(f"No historical data found for {symbol}")

        rows = []

        with path.open("r", newline="", encoding="utf-8") as csvfile:
            reader = csv.DictReader(csvfile)

            for row in reader:
                rows.append(
                    {
                        "date": row["Date"],
                        "open": float(row["Open"]),
                        "high": float(row["High"]),
                        "low": float(row["Low"]),
                        "close": float(row["Close"]),
                        "volume": int(row["Volume"]),
                    }
                )

        return rows
