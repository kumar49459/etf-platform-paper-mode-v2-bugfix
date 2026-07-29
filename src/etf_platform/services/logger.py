import logging
from pathlib import Path


class PlatformLogger:
    """
    Central logging service for the ETF Platform.
    """

    def __init__(
        self,
        log_file="logs/etf_platform.log",
        level=logging.INFO,
    ):
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        self.logger = logging.getLogger("ETFPlatform")

        if not self.logger.handlers:
            self.logger.setLevel(level)

            formatter = logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s"
            )

            file_handler = logging.FileHandler(log_path)
            file_handler.setFormatter(formatter)

            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)

            self.logger.addHandler(file_handler)
            self.logger.addHandler(console_handler)

    def info(self, message):
        self.logger.info(message)

    def warning(self, message):
        self.logger.warning(message)

    def error(self, message):
        self.logger.error(message)
