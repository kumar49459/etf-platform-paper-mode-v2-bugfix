"""Configuration Manager: loads, merges, validates, and freezes config.

Load order (later overrides earlier — standard, predictable precedence):
  1. config/base.yaml               — defaults shared by every environment
  2. config/<environment>.yaml      — environment-specific overrides
  3. OS environment variables        — `ETF_PLATFORM__<SECTION>__<KEY>=value`
     (double underscore separates nesting; useful for container/CI overrides
     without editing files, and for keeping environment-specific secrets out
     of YAML entirely)

The merged, validated result is frozen into an `AppConfig` and a
`config_version` (sha256 of the canonical merged dict, truncated to 16 hex
chars for readability) is attached — this is what reproducibility tracking
(backtest_runs, proposal artifacts) references later.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import yaml

from etf_platform.common.logging_setup import get_logger
from etf_platform.config_manager.exceptions import ConfigFileNotFoundError
from etf_platform.config_manager.schema import AppConfig, build_app_config

logger = get_logger("config_manager")

ENV_VAR_PREFIX = "ETF_PLATFORM__"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigFileNotFoundError(f"Config file not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        content = yaml.safe_load(fh) or {}
    if not isinstance(content, dict):
        raise ConfigFileNotFoundError(f"Config file {path} did not parse to a mapping")
    return content


def _coerce_scalar(value: str) -> Any:
    """Best-effort coercion of an env-var string into bool/int/float/str."""
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


def _apply_env_overrides(merged: dict[str, Any], environ: dict[str, str]) -> dict[str, Any]:
    result = copy.deepcopy(merged)
    for env_key, env_value in environ.items():
        if not env_key.startswith(ENV_VAR_PREFIX):
            continue
        path_parts = env_key[len(ENV_VAR_PREFIX):].lower().split("__")
        if not path_parts or not path_parts[0]:
            continue
        cursor = result
        for part in path_parts[:-1]:
            if part not in cursor or not isinstance(cursor[part], dict):
                cursor[part] = {}
            cursor = cursor[part]
        cursor[path_parts[-1]] = _coerce_scalar(env_value)
        logger.debug("Applied env override for %s", env_key)
    return result


def _compute_config_version(merged: dict[str, Any]) -> str:
    canonical = json.dumps(merged, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


class ConfigManager:
    """Loads and validates configuration for a given environment.

    Usage:
        config = ConfigManager(config_dir="config", environment="paper").load()

    The resulting `AppConfig` is immutable (frozen dataclasses) — nothing in
    the rest of the platform can mutate it after load, by design.
    """

    def __init__(self, config_dir: str | Path = "config", environment: str | None = None) -> None:
        self.config_dir = Path(config_dir)
        self.environment = environment or os.environ.get("ETF_PLATFORM_ENV", "dev")

    def load(self) -> AppConfig:
        base = _load_yaml(self.config_dir / "base.yaml")
        env_file = self.config_dir / f"{self.environment}.yaml"
        env_overrides = _load_yaml(env_file) if env_file.exists() else {}
        if not env_file.exists():
            logger.warning(
                "No environment-specific config file found at %s; using base.yaml only. "
                "This is only expected for ad-hoc/test environments.",
                env_file,
            )

        merged = _deep_merge(base, env_overrides)
        merged.setdefault("environment", self.environment)
        merged = _apply_env_overrides(merged, dict(os.environ))

        config_version = _compute_config_version(merged)
        merged["config_version"] = config_version

        app_config = build_app_config(merged)
        app_config.validate()

        logger.info(
            "Loaded configuration for environment='%s' (config_version=%s)",
            self.environment,
            config_version,
        )
        return app_config


_cached_config: AppConfig | None = None


def get_config(*, force_reload: bool = False, config_dir: str | Path = "config") -> AppConfig:
    """Process-wide cached accessor.

    `force_reload=True` is intended for tests, where each test may want a
    fresh load against a different environment/fixture directory rather than
    reusing whatever the first caller in the process happened to load.
    """
    global _cached_config
    if _cached_config is None or force_reload:
        _cached_config = ConfigManager(config_dir=config_dir).load()
    return _cached_config
