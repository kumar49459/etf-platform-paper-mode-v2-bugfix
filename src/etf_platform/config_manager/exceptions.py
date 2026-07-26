"""Exceptions raised by the Configuration Manager."""


class ConfigError(Exception):
    """Base class for all Configuration Manager errors."""


class ConfigFileNotFoundError(ConfigError):
    """Raised when a required config file (base or environment overlay) is missing."""


class ConfigValidationError(ConfigError):
    """Raised when merged config fails schema validation.

    Per Phase 1 fail-safe NFR: invalid config must never partially load or
    fall back to defaults silently. This exception is fatal by design — the
    process should not start with configuration it cannot validate.
    """
