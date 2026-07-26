"""Unit tests for the Secrets Manager (local encrypted provider + AWS
provider with mocked boto3).

Run with: python -m unittest tests.unit.test_secrets_manager -v
"""

from __future__ import annotations

import shutil
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from etf_platform.config_manager.schema import SecretsConfig
from etf_platform.secrets_manager.exceptions import (
    SecretNotFoundError,
    SecretsBackendUnavailableError,
    SecretsProviderError,
)
from etf_platform.secrets_manager.local_provider import LocalEncryptedFileProvider
from etf_platform.secrets_manager.secrets_manager import SecretsManager


class TestLocalEncryptedFileProvider(unittest.TestCase):
    KEY_ENV_VAR = "TEST_ETF_PLATFORM_MASTER_KEY"

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.secrets_file = self.tmp_dir / "secrets.enc"
        self.key = LocalEncryptedFileProvider.generate_key()
        self._env_patch = mock.patch.dict("os.environ", {self.KEY_ENV_VAR: self.key})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_missing_key_env_var_raises(self) -> None:
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(SecretsBackendUnavailableError):
                LocalEncryptedFileProvider(self.secrets_file, "SOME_UNSET_VAR")

    def test_set_and_get_roundtrip(self) -> None:
        provider = LocalEncryptedFileProvider(self.secrets_file, self.KEY_ENV_VAR)
        provider.set_secret("kite_api_key", "abc123")
        self.assertEqual(provider.get_secret("kite_api_key"), "abc123")

    def test_get_missing_secret_raises(self) -> None:
        provider = LocalEncryptedFileProvider(self.secrets_file, self.KEY_ENV_VAR)
        with self.assertRaises(SecretNotFoundError):
            provider.get_secret("does_not_exist")

    def test_secret_exists(self) -> None:
        provider = LocalEncryptedFileProvider(self.secrets_file, self.KEY_ENV_VAR)
        self.assertFalse(provider.secret_exists("x"))
        provider.set_secret("x", "y")
        self.assertTrue(provider.secret_exists("x"))

    def test_file_is_encrypted_at_rest(self) -> None:
        provider = LocalEncryptedFileProvider(self.secrets_file, self.KEY_ENV_VAR)
        provider.set_secret("kite_api_key", "super-secret-value")
        raw_bytes = self.secrets_file.read_bytes()
        self.assertNotIn(b"super-secret-value", raw_bytes)

    def test_wrong_key_cannot_decrypt(self) -> None:
        provider = LocalEncryptedFileProvider(self.secrets_file, self.KEY_ENV_VAR)
        provider.set_secret("k", "v")

        other_key_var = "TEST_OTHER_KEY"
        with mock.patch.dict("os.environ", {other_key_var: LocalEncryptedFileProvider.generate_key()}):
            wrong_key_provider = LocalEncryptedFileProvider(self.secrets_file, other_key_var)
            with self.assertRaises(SecretsProviderError):
                wrong_key_provider.get_secret("k")

    def test_file_permissions_restricted(self) -> None:
        provider = LocalEncryptedFileProvider(self.secrets_file, self.KEY_ENV_VAR)
        provider.set_secret("k", "v")
        mode = self.secrets_file.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


class TestSecretsManagerFacade(unittest.TestCase):
    KEY_ENV_VAR = "TEST_ETF_PLATFORM_MASTER_KEY_FACADE"

    def setUp(self) -> None:
        self.tmp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp_dir, ignore_errors=True)
        self.key = LocalEncryptedFileProvider.generate_key()
        self._env_patch = mock.patch.dict("os.environ", {self.KEY_ENV_VAR: self.key})
        self._env_patch.start()
        self.addCleanup(self._env_patch.stop)

    def test_facade_selects_local_provider(self) -> None:
        config = SecretsConfig(
            provider="local",
            local_key_env_var=self.KEY_ENV_VAR,
            local_secrets_file=str(self.tmp_dir / "s.enc"),
        )
        manager = SecretsManager(config)
        manager.set_secret("hello", "world")
        self.assertEqual(manager.get_secret("hello"), "world")

    def test_repr_does_not_leak_secret_values(self) -> None:
        config = SecretsConfig(
            provider="local",
            local_key_env_var=self.KEY_ENV_VAR,
            local_secrets_file=str(self.tmp_dir / "s.enc"),
        )
        manager = SecretsManager(config)
        manager.set_secret("api_key", "should-never-appear-in-repr")
        self.assertNotIn("should-never-appear-in-repr", repr(manager))

    def test_log_scrubbing_filter_registers_secret_value(self) -> None:
        config = SecretsConfig(
            provider="local",
            local_key_env_var=self.KEY_ENV_VAR,
            local_secrets_file=str(self.tmp_dir / "s.enc"),
        )
        manager = SecretsManager(config)
        manager.set_secret("token", "leak-me-not-12345")
        value = manager.get_secret("token")
        self.assertIn(value, manager._scrubber._secret_values)


class TestAWSSecretsManagerProviderWithMockedBoto3(unittest.TestCase):
    """boto3 isn't installed in this sandbox — we inject a fake module into
    sys.modules so the lazy `import boto3` inside AWSSecretsManagerProvider
    resolves to our mock, letting us test the provider's own logic (caching,
    JSON parsing, error translation) without a real AWS dependency or network
    call. This exercises everything except boto3's own wire behavior, which
    is out of scope for a unit test anyway (that's what integration/contract
    tests against a real or LocalStack AWS endpoint would cover, in Phase 11)."""

    def setUp(self) -> None:
        self.fake_boto3 = types.ModuleType("boto3")
        self.fake_client = mock.MagicMock()
        self.fake_boto3.client = mock.MagicMock(return_value=self.fake_client)
        self._patcher = mock.patch.dict(sys.modules, {"boto3": self.fake_boto3})
        self._patcher.start()
        self.addCleanup(self._patcher.stop)

    def test_get_secret_from_json_bundle(self) -> None:
        import json

        from etf_platform.secrets_manager.aws_provider import AWSSecretsManagerProvider

        self.fake_client.get_secret_value.return_value = {
            "SecretString": json.dumps({"kite_api_key": "aws-value-1"})
        }
        provider = AWSSecretsManagerProvider(secret_name="etf-platform/live/kite-credentials", region_name="ap-south-1")
        self.assertEqual(provider.get_secret("kite_api_key"), "aws-value-1")
        self.fake_boto3.client.assert_called_once_with("secretsmanager", region_name="ap-south-1")

    def test_missing_key_in_bundle_raises(self) -> None:
        import json

        from etf_platform.secrets_manager.aws_provider import AWSSecretsManagerProvider

        self.fake_client.get_secret_value.return_value = {"SecretString": json.dumps({"other_key": "x"})}
        provider = AWSSecretsManagerProvider(secret_name="bundle", region_name="ap-south-1")
        with self.assertRaises(SecretNotFoundError):
            provider.get_secret("kite_api_key")

    def test_non_json_secret_string_raises(self) -> None:
        from etf_platform.secrets_manager.aws_provider import AWSSecretsManagerProvider

        self.fake_client.get_secret_value.return_value = {"SecretString": "not-json"}
        provider = AWSSecretsManagerProvider(secret_name="bundle", region_name="ap-south-1")
        with self.assertRaises(SecretsProviderError):
            provider.get_secret("anything")

    def test_set_secret_not_implemented(self) -> None:
        from etf_platform.secrets_manager.aws_provider import AWSSecretsManagerProvider

        provider = AWSSecretsManagerProvider(secret_name="bundle", region_name="ap-south-1")
        with self.assertRaises(NotImplementedError):
            provider.set_secret("x", "y")

    def test_cache_avoids_repeated_calls_within_ttl(self) -> None:
        import json

        from etf_platform.secrets_manager.aws_provider import AWSSecretsManagerProvider

        self.fake_client.get_secret_value.return_value = {"SecretString": json.dumps({"k": "v"})}
        provider = AWSSecretsManagerProvider(secret_name="bundle", region_name="ap-south-1", cache_ttl_seconds=999)
        provider.get_secret("k")
        provider.get_secret("k")
        provider.get_secret("k")
        self.assertEqual(self.fake_client.get_secret_value.call_count, 1)

    def test_boto3_missing_raises_clear_error(self) -> None:
        self._patcher.stop()  # remove the fake boto3 for this one test
        with mock.patch.dict(sys.modules, {"boto3": None}):
            from etf_platform.secrets_manager.aws_provider import AWSSecretsManagerProvider

            provider = AWSSecretsManagerProvider(secret_name="bundle", region_name="ap-south-1")
            with self.assertRaises(SecretsBackendUnavailableError):
                provider.get_secret("k")
        self._patcher.start()  # restore for tearDown/addCleanup bookkeeping consistency


if __name__ == "__main__":
    unittest.main()
