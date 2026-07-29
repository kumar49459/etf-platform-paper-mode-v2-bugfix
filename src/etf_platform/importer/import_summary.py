from pathlib import Path

from etf_platform.importer.csv_validator import CSVValidator
from etf_platform.importer.duplicate_detector import DuplicateDetector
from etf_platform.importer.missing_date_detector import MissingDateDetector


class ImportSummary:
    """
    Generates a validation summary for a historical CSV file.
    """

    def __init__(self):
        self.validator = CSVValidator()
        self.duplicate_detector = DuplicateDetector()
        self.missing_detector = MissingDateDetector()

    def summarize(self, file_path):
        file_path = Path(file_path)

        validation = self.validator.validate(file_path)
        duplicates = self.duplicate_detector.detect(file_path)
        gaps = self.missing_detector.detect(file_path)

        return {
            "file": file_path.name,
            "valid": validation["valid"],
            "rows": validation["rows"],
            "duplicate_count": duplicates["duplicate_count"],
            "gap_count": gaps["gap_count"],
            "columns": validation["columns"],
        }
