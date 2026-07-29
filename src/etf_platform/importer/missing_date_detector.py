import csv
from pathlib import Path
from datetime import datetime


class MissingDateDetector:
    """
    Detect missing dates in historical datasets.
    """

    DATE_FORMATS = (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
    )

    def _parse_date(self, value):
        for fmt in self.DATE_FORMATS:
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                pass
        raise ValueError(f"Unsupported date format: {value}")

    def detect(self, file_path):
        file_path = Path(file_path)

        if not file_path.exists():
            raise FileNotFoundError(file_path)

        dates = []

        with file_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)

            if reader.fieldnames is None:
                raise ValueError("CSV has no header")

            date_column = None
            for name in reader.fieldnames:
                if name.strip().lower() == "date":
                    date_column = name
                    break

            if date_column is None:
                raise ValueError("Date column not found")

            for row in reader:
                dates.append(self._parse_date(row[date_column]))

        dates.sort()

        gaps = []

        for previous, current in zip(dates, dates[1:]):
            delta = (current - previous).days

            if delta > 1:
                gaps.append({
                    "from": previous.isoformat(),
                    "to": current.isoformat(),
                    "missing_days": delta - 1,
                })

        return {
            "gap_count": len(gaps),
            "gaps": gaps,
        }
