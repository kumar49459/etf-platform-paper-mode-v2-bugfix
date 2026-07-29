from pathlib import Path

import yaml


class ConfigManager:
    """
    Loads application configuration from YAML.
    """

    def __init__(self, config_file="config/settings.yaml"):
        self.config_file = Path(config_file)

    def load(self):
        if not self.config_file.exists():
            raise FileNotFoundError(self.config_file)

        with self.config_file.open(
            "r",
            encoding="utf-8",
        ) as file:
            return yaml.safe_load(file)
