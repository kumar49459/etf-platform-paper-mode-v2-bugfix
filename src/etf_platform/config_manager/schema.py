"""Config schema, implemented with stdlib `dataclasses` rather than pydantic.

Design decision: pydantic is a common and reasonable choice for config
validation, but it's an extra dependency on every instance that imports this
module — including the always-on live micro instance, where Phase 1 §12.1
binds us to keeping runtime dependencies minimal. Stdlib dataclasses plus
explicit validation functions give us the same guarantee (invalid config
fails loudly, fails fast) without that dependency. If config schemas grow
complex enough that hand-rolled validation becomes unwieldy, pydantic is a
reasonable module to introduce for the *research-side* config loading only —
that trade-off should be revisited explicitly if it comes up, not silently
assumed either way.

All dataclasses here are frozen (immutable) — see Phase 1 NFR on
reproducibility: a config object must not change after a run starts, or
"which config produced this backtest" stops being an answerable question.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any

from etf_platform.config_manager.exceptions import ConfigValidationError

VALID_ENVIRONMENTS = frozenset({"dev", "paper", "live"})
VALID_SECRETS_PROVIDERS = frozenset({"local", "aws"})
VALID_STORAGE_BACKENDS = frozenset({"auto", "csv", "parquet"})
VALID_DATA_PROVIDERS = frozenset({"nse", "kite"})
VALID_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True)
class RateLimitConfig:
    """Per-provider API rate limit budget (calls/second and calls/minute)."""
    calls_per_second: float = 3.0
    calls_per_minute: float = 60.0

    def validate(self, path: str) -> list[str]:
        errors = []
        if self.calls_per_second <= 0:
            errors.append(f"{path}.calls_per_second must be > 0")
        if self.calls_per_minute <= 0:
            errors.append(f"{path}.calls_per_minute must be > 0")
        return errors


@dataclass(frozen=True)
class DataEngineConfig:
    """Configuration for the Historical Data Engine: providers, storage, rate limits, and quality thresholds."""
    primary_provider: str = "nse"
    secondary_provider: str = "kite"
    storage_backend: str = "auto"
    storage_path: str = "./data/processed"
    snapshot_registry_db: str = "./data/snapshots.db"
    rate_limits: dict[str, RateLimitConfig] = field(
        default_factory=lambda: {
            "nse": RateLimitConfig(calls_per_second=2.0, calls_per_minute=30.0),
            "kite": RateLimitConfig(calls_per_second=3.0, calls_per_minute=180.0),
        }
    )
    halt_on_critical_quality_issue: bool = True
    max_price_jump_pct: float = 20.0
    stale_price_max_days: int = 10

    def validate(self, path: str = "data_engine") -> list[str]:
        errors = []
        if self.primary_provider not in VALID_DATA_PROVIDERS:
            errors.append(
                f"{path}.primary_provider '{self.primary_provider}' must be one of {sorted(VALID_DATA_PROVIDERS)}"
            )
        if self.secondary_provider not in VALID_DATA_PROVIDERS:
            errors.append(
                f"{path}.secondary_provider '{self.secondary_provider}' must be one of {sorted(VALID_DATA_PROVIDERS)}"
            )
        if self.storage_backend not in VALID_STORAGE_BACKENDS:
            errors.append(
                f"{path}.storage_backend '{self.storage_backend}' must be one of {sorted(VALID_STORAGE_BACKENDS)}"
            )
        if not self.storage_path:
            errors.append(f"{path}.storage_path must not be empty")
        if self.max_price_jump_pct <= 0:
            errors.append(f"{path}.max_price_jump_pct must be > 0")
        if self.stale_price_max_days <= 0:
            errors.append(f"{path}.stale_price_max_days must be > 0")
        for provider_name, rate_limit in self.rate_limits.items():
            errors.extend(rate_limit.validate(f"{path}.rate_limits.{provider_name}"))
        return errors


@dataclass(frozen=True)
class DatabaseConfig:
    """Configuration for the shared SQLite transactional store."""
    sqlite_path: str = "./data/platform.db"
    busy_timeout_seconds: float = 30.0

    def validate(self, path: str = "database") -> list[str]:
        errors = []
        if not self.sqlite_path:
            errors.append(f"{path}.sqlite_path must not be empty")
        if self.busy_timeout_seconds <= 0:
            errors.append(f"{path}.busy_timeout_seconds must be > 0")
        return errors


@dataclass(frozen=True)
class SecretsConfig:
    """Configuration selecting and parameterizing the active Secrets Manager provider."""
    provider: str = "local"
    local_key_env_var: str = "ETF_PLATFORM_MASTER_KEY"
    local_secrets_file: str = "./config/.secrets.enc"
    aws_secret_name: str | None = None
    aws_region: str | None = "ap-south-1"

    def validate(self, path: str = "secrets") -> list[str]:
        errors = []
        if self.provider not in VALID_SECRETS_PROVIDERS:
            errors.append(f"{path}.provider '{self.provider}' must be one of {sorted(VALID_SECRETS_PROVIDERS)}")
        if self.provider == "local" and not self.local_key_env_var:
            errors.append(f"{path}.local_key_env_var must be set when provider is 'local'")
        if self.provider == "aws":
            if not self.aws_secret_name:
                errors.append(f"{path}.aws_secret_name is required when provider is 'aws'")
            if not self.aws_region:
                errors.append(f"{path}.aws_region is required when provider is 'aws'")
        return errors


@dataclass(frozen=True)
class LoggingConfig:
    """Configuration for process-wide logging (level, destination, format)."""
    level: str = "INFO"
    log_dir: str | None = "./logs"
    json_format: bool = False

    def validate(self, path: str = "logging") -> list[str]:
        errors = []
        if self.level.upper() not in VALID_LOG_LEVELS:
            errors.append(f"{path}.level '{self.level}' must be one of {sorted(VALID_LOG_LEVELS)}")
        return errors


@dataclass(frozen=True)
class AppConfig:
    """Root, immutable, validated application configuration.

    `config_version` is populated by ConfigManager after merging + validation
    — it is a hash of the fully-resolved config, used to tie backtest_runs and
    proposal artifacts back to the exact config that produced them (Phase 1
    §1.4 reproducibility NFR).
    """

    environment: str = "dev"
    config_version: str = ""
    data_engine: DataEngineConfig = field(default_factory=DataEngineConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    secrets: SecretsConfig = field(default_factory=SecretsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    def validate(self) -> None:
        errors: list[str] = []
        if self.environment not in VALID_ENVIRONMENTS:
            errors.append(f"environment '{self.environment}' must be one of {sorted(VALID_ENVIRONMENTS)}")
        errors.extend(self.data_engine.validate())
        errors.extend(self.database.validate())
        errors.extend(self.secrets.validate())
        errors.extend(self.logging.validate())
        if errors:
            raise ConfigValidationError(
                "Configuration failed validation with "
                f"{len(errors)} error(s):\n  - " + "\n  - ".join(errors)
            )


def _build_dataclass(cls: type, data: dict[str, Any]) -> Any:
    """Recursively build a (possibly nested) frozen dataclass from a plain dict.

    This is the hand-rolled equivalent of pydantic's `.parse_obj()` — kept
    deliberately small since our schema is small. Unknown keys raise rather
    than being silently ignored, since a typo'd config key silently doing
    nothing is exactly the kind of bug this validation layer exists to catch.
    """
    if not isinstance(data, dict):
        raise ConfigValidationError(f"Expected a mapping for {cls.__name__}, got {type(data).__name__}")

    field_types = {f.name: f.type for f in fields(cls)}
    known_keys = set(field_types)
    unknown_keys = set(data) - known_keys
    if unknown_keys:
        raise ConfigValidationError(
            f"Unknown config key(s) for {cls.__name__}: {sorted(unknown_keys)}. "
            f"Valid keys are: {sorted(known_keys)}"
        )

    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if cls is AppConfig and key == "data_engine" and isinstance(value, dict):
            rate_limits_raw = value.get("rate_limits")
            de_kwargs = dict(value)
            if isinstance(rate_limits_raw, dict):
                de_kwargs["rate_limits"] = {
                    name: RateLimitConfig(**rl) if isinstance(rl, dict) else rl
                    for name, rl in rate_limits_raw.items()
                }
            kwargs[key] = DataEngineConfig(**de_kwargs)
        elif cls is AppConfig and key == "database" and isinstance(value, dict):
            kwargs[key] = DatabaseConfig(**value)
        elif cls is AppConfig and key == "secrets" and isinstance(value, dict):
            kwargs[key] = SecretsConfig(**value)
        elif cls is AppConfig and key == "logging" and isinstance(value, dict):
            kwargs[key] = LoggingConfig(**value)
        else:
            kwargs[key] = value
    return cls(**kwargs)


def build_app_config(data: dict[str, Any]) -> AppConfig:
    return _build_dataclass(AppConfig, data)
