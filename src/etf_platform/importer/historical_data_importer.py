from pathlib import Path
import shutil


class HistoricalDataImporter:
    """
    Historical Data Import Manager

    Imports ETF CSV files into the platform's
    historical data directory.
    """

    def __init__(self, destination="data/historical"):
        self.destination = Path(destination)
        self.destination.mkdir(parents=True, exist_ok=True)

    def import_file(self, source):
        source = Path(source)

        if not source.exists():
            raise FileNotFoundError(source)

        destination = self.destination / source.name

        # Skip if source and destination are identical
        if source.resolve() == destination.resolve():
            return destination

        shutil.copy2(source, destination)

        return destination

    def import_directory(self, source_directory):
        source_directory = Path(source_directory)

        imported = []

        for csv_file in sorted(source_directory.glob("*.csv")):
            imported.append(self.import_file(csv_file))

        return imported
