"""Unit tests for the Configuration Manager.

Run with: python -m unittest tests.unit.test_config_manager -v
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from etf_platform.config_manager.config_manager import ConfigManager
from etf_platform.config_manager.exceptions import ConfigFileNotFoundError, ConfigValidationError


class ConfigManagerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)

    def _write_yaml(self, filename: str, content: dict) -> None:
        (self.tmp_dir / filename).write_text(yaml.safe_dump(content), encoding="utf-8")


class TestConfigLoadingAndLayering(ConfigManagerTestBase):
    def test_loads_base_only_when_no_env_file(self) -> None:
        self._write_yaml("base.yaml", {"logging": {"level": "INFO"}})
        config = ConfigManager(config_dir=self.tmp_dir, environment="dev").load()
        self.assertEqual(config.logging.level, "INFO")
        self.assertEqual(config.environment, "dev")

    def test_environment_overlay_overrides_base(self) -> None:
        self._write_yaml("base.yaml", {"logging": {"level": "INFO"}})
        self._write_yaml("dev.yaml", {"logging": {"level": "DEBUG"}})
        config = ConfigManager(config_dir=self.tmp_dir, environment="dev").load()
        self.assertEqual(config.logging.level, "DEBUG")

    def test_deep_merge_preserves_unrelated_base_keys(self) -> None:
        self._write_yaml(
            "base.yaml",
            {"logging": {"level": "INFO", "json_format": False}, "database": {"sqlite_path": "./x.db"}},
        )
        self._write_yaml("dev.yaml", {"logging": {"level": "DEBUG"}})
        config = ConfigManager(config_dir=self.tmp_dir, environment="dev").load()
        self.assertEqual(config.logging.level, "DEBUG")
        self.assertFalse(config.logging.json_format)  # preserved from base
        self.assertEqual(config.database.sqlite_path, "./x.db")  # preserved from base

    def test_missing_base_file_raises(self) -> None:
        with self.assertRaises(ConfigFileNotFoundError):
            ConfigManager(config_dir=self.tmp_dir, environment="dev").load()


class TestConfigValidation(ConfigManagerTestBase):
    def test_invalid_environment_raises(self) -> None:
        self._write_yaml("base.yaml", {"environment": "production_typo"})
        with self.assertRaises(ConfigValidationError):
            ConfigManager(config_dir=self.tmp_dir, environment="production_typo").load()

    def test_invalid_secrets_provider_raises(self) -> None:
        self._write_yaml("base.yaml", {"secrets": {"provider": "vault"}})
        with self.assertRaises(ConfigValidationError):
            ConfigManager(config_dir=self.tmp_dir, environment="dev").load()

    def test_aws_provider_requires_secret_name_and_region(self) -> None:
        self._write_yaml("base.yaml", {"secrets": {"provider": "aws", "aws_secret_name": None, "aws_region": None}})
        with self.assertRaises(ConfigValidationError):
            ConfigManager(config_dir=self.tmp_dir, environment="dev").load()

    def test_unknown_top_level_key_raises(self) -> None:
        self._write_yaml("base.yaml", {"totally_unknown_section": {"x": 1}})
        with self.assertRaises(ConfigValidationError):
            ConfigManager(config_dir=self.tmp_dir, environment="dev").load()

    def test_negative_rate_limit_raises(self) -> None:
        self._write_yaml(
            "base.yaml",
            {"data_engine": {"rate_limits": {"nse": {"calls_per_second": -1.0, "calls_per_minute": 30.0}}}},
        )
        with self.assertRaises(ConfigValidationError):
            ConfigManager(config_dir=self.tmp_dir, environment="dev").load()


class TestConfigVersionHash(ConfigManagerTestBase):
    def test_same_config_produces_same_version(self) -> None:
        self._write_yaml("base.yaml", {"logging": {"level": "INFO"}})
        v1 = ConfigManager(config_dir=self.tmp_dir, environment="dev").load().config_version
        v2 = ConfigManager(config_dir=self.tmp_dir, environment="dev").load().config_version
        self.assertEqual(v1, v2)

    def test_different_config_produces_different_version(self) -> None:
        self._write_yaml("base.yaml", {"logging": {"level": "INFO"}})
        v1 = ConfigManager(config_dir=self.tmp_dir, environment="dev").load().config_version
        self._write_yaml("base.yaml", {"logging": {"level": "DEBUG"}})
        v2 = ConfigManager(config_dir=self.tmp_dir, environment="dev").load().config_version
        self.assertNotEqual(v1, v2)


class TestEnvVarOverrides(ConfigManagerTestBase):
    def test_env_var_overrides_yaml(self) -> None:
        self._write_yaml("base.yaml", {"logging": {"level": "INFO"}})
        os.environ["ETF_PLATFORM__LOGGING__LEVEL"] = "WARNING"
        self.addCleanup(os.environ.pop, "ETF_PLATFORM__LOGGING__LEVEL", None)
        config = ConfigManager(config_dir=self.tmp_dir, environment="dev").load()
        self.assertEqual(config.logging.level, "WARNING")

    def test_immutability_of_loaded_config(self) -> None:
        self._write_yaml("base.yaml", {"logging": {"level": "INFO"}})
        config = ConfigManager(config_dir=self.tmp_dir, environment="dev").load()
        with self.assertRaises(Exception):
            config.logging.level = "DEBUG"  # frozen dataclass — must raise


class TestGetConfigSingleton(ConfigManagerTestBase):
    def test_get_config_caches_result(self) -> None:
        from etf_platform.config_manager.config_manager import get_config

        self._write_yaml("base.yaml", {"logging": {"level": "INFO"}})
        with mock.patch.dict("os.environ", {"ETF_PLATFORM_ENV": "dev"}):
            c1 = get_config(force_reload=True, config_dir=self.tmp_dir)
            c2 = get_config(config_dir=self.tmp_dir)  # should return cached, not reload
        self.assertIs(c1, c2)

    def test_force_reload_picks_up_changes(self) -> None:
        from etf_platform.config_manager.config_manager import get_config

        self._write_yaml("base.yaml", {"logging": {"level": "INFO"}})
        c1 = get_config(force_reload=True, config_dir=self.tmp_dir)
        self.assertEqual(c1.logging.level, "INFO")

        self._write_yaml("base.yaml", {"logging": {"level": "DEBUG"}})
        c2 = get_config(force_reload=True, config_dir=self.tmp_dir)
        self.assertEqual(c2.logging.level, "DEBUG")
        self.assertIsNot(c1, c2)


if __name__ == "__main__":
    unittest.main()
