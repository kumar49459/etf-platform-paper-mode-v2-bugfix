import csv
from pathlib import Path


class CSVValidator:
    """
    Validates ETF historical data CSV files.
    """

    REQUIRED_COLUMNS = {
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
    }

    def validate(self, file_path):
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(file_path)

        with file_path.open(
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                raise ValueError("CSV file has no header")

            # Normalize headers (case-insensitive + trim spaces)
            normalized_headers = [
                h.strip().lower()
                for h in reader.fieldnames
            ]

            missing = sorted(
                self.REQUIRED_COLUMNS - set(normalized_headers)
            )

            if missing:
                raise ValueError(
                    f"Missing columns: {', '.join(missing)}"
                )

            rows = list(reader)

            if not rows:
                raise ValueError("CSV contains no data")

        return {
            "valid": True,
            "rows": len(rows),
            "columns": normalized_headers,
        }
