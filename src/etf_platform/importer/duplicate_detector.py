import csv
from pathlib import Path


class DuplicateDetector:
    """
    Detect duplicate dates in historical data.
    """

    def detect(self, file_path):
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(file_path)

        seen = set()
        duplicates = []

        with file_path.open(
            "r",
            newline="",
            encoding="utf-8"
        ) as f:

            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                raise ValueError("CSV has no header")

            # Locate the date column (case-insensitive)
            date_column = None
            for name in reader.fieldnames:
                if name.strip().lower() == "date":
                    date_column = name
                    break

            if date_column is None:
                raise ValueError("Date column not found")

            for row in reader:
                date = row[date_column].strip()

                if date in seen:
                    duplicates.append(date)
                else:
                    seen.add(date)

        return {
            "duplicate_count": len(duplicates),
            "duplicates": duplicates,
        }
