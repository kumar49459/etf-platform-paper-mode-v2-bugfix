"""SecretsManager facade.

This is the only class the rest of the platform should import from this
package. It selects a concrete provider based on `SecretsConfig.provider`
and registers every retrieved secret value with the shared log-scrubbing
filter (see common/logging_setup.py) so that even an accidental
`logger.info(f"token={token}")` elsewhere in the codebase gets redacted
before it reaches disk — belt-and-suspenders on top of the discipline of
simply not logging secrets.
"""

from __future__ import annotations

from etf_platform.common.logging_setup import get_logger, get_secret_scrubbing_filter
from etf_platform.config_manager.schema import SecretsConfig
from etf_platform.secrets_manager.aws_provider import AWSSecretsManagerProvider
from etf_platform.secrets_manager.base import SecretsProvider
from etf_platform.secrets_manager.local_provider import LocalEncryptedFileProvider

logger = get_logger("secrets_manager")


class SecretsManager:
    """Provider-agnostic facade for secret storage/retrieval (local encrypted file or AWS Secrets Manager). The only class outside this package that should be imported for secrets access."""
    def __init__(self, config: SecretsConfig) -> None:
        self._provider: SecretsProvider = self._build_provider(config)
        self._scrubber = get_secret_scrubbing_filter()

    @staticmethod
    def _build_provider(config: SecretsConfig) -> SecretsProvider:
        if config.provider == "local":
            return LocalEncryptedFileProvider(
                file_path=config.local_secrets_file,
                key_env_var=config.local_key_env_var,
            )
        if config.provider == "aws":
            assert config.aws_secret_name is not None  # guaranteed by schema validation
            assert config.aws_region is not None
            return AWSSecretsManagerProvider(
                secret_name=config.aws_secret_name,
                region_name=config.aws_region,
            )
        # Schema validation already prevents this, but fail loudly rather than
        # silently defaulting if it's ever reached (e.g. via direct construction
        # bypassing ConfigManager in a test).
        raise ValueError(f"Unknown secrets provider '{config.provider}'")

    def get_secret(self, name: str) -> str:
        value = self._provider.get_secret(name)
        self._scrubber.register_secret_value(value)
        logger.debug("Retrieved secret '%s' (value redacted from logs).", name)
        return value

    def set_secret(self, name: str, value: str) -> None:
        self._provider.set_secret(name, value)
        self._scrubber.register_secret_value(value)

    def secret_exists(self, name: str) -> bool:
        return self._provider.secret_exists(name)

    def __repr__(self) -> str:
        # Deliberately does not expose provider internals or any secret material.
        return f"SecretsManager(provider={type(self._provider).__name__})"
