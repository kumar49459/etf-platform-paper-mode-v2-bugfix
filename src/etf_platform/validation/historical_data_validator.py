import csv
from pathlib import Path


class HistoricalDataValidator:
    """
    Validates historical ETF CSV files.
    """

    REQUIRED_COLUMNS = {
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }

    def validate(self, csv_file):

        path = Path(csv_file)

        if not path.exists():
            raise FileNotFoundError(csv_file)

        with path.open(newline="") as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                raise ValueError("CSV has no header")

            columns = {c.strip().lower() for c in reader.fieldnames}

            missing = self.REQUIRED_COLUMNS - columns

            if missing:
                raise ValueError(
                    f"Missing columns: {sorted(missing)}"
                )

            rows = list(reader)

            if len(rows) == 0:
                raise ValueError("CSV contains no data")

        return {
            "valid": True,
            "rows": len(rows),
            "columns": sorted(columns),
        }
