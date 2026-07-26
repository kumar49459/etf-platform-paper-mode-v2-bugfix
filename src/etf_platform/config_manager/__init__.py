"""Configuration Manager (Phase 1 Module 15).

Loads layered, environment-aware YAML config into an immutable, validated
config object, with a reproducibility-oriented `config_version` hash.
"""

from etf_platform.config_manager.config_manager import ConfigManager, get_config
from etf_platform.config_manager.exceptions import (
    ConfigFileNotFoundError,
    ConfigValidationError,
)
from etf_platform.config_manager.schema import AppConfig

__all__ = [
    "ConfigManager",
    "get_config",
    "AppConfig",
    "ConfigFileNotFoundError",
    "ConfigValidationError",
]
