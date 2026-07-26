"""Local, Fernet-encrypted-file secrets provider.

Intended for the micro instance and local development, where running a
full AWS Secrets Manager dependency isn't warranted. The entire secrets
store is a single JSON blob, encrypted at rest with a symmetric key that
itself is *never* stored alongside the encrypted file — it must come from
an environment variable (or, in a more advanced deployment, an OS keyring),
so that compromising the encrypted file alone doesn't compromise the
secrets.

This mirrors the same principle AWS Secrets Manager uses (data encryption
key separate from data), just without the AWS dependency.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from etf_platform.common.logging_setup import get_logger
from etf_platform.secrets_manager.base import SecretsProvider
from etf_platform.secrets_manager.exceptions import (
    SecretNotFoundError,
    SecretsBackendUnavailableError,
    SecretsProviderError,
)

logger = get_logger("secrets_manager.local_provider")


class LocalEncryptedFileProvider(SecretsProvider):
    """SecretsProvider backed by a single Fernet-encrypted JSON file. Default for the live micro instance and local development."""
    def __init__(self, file_path: str | Path, key_env_var: str) -> None:
        self._file_path = Path(file_path)
        self._key_env_var = key_env_var
        self._fernet = self._load_fernet()

    def _load_fernet(self) -> Fernet:
        key = os.environ.get(self._key_env_var)
        if not key:
            raise SecretsBackendUnavailableError(
                f"Environment variable '{self._key_env_var}' is not set. Generate a key with "
                "LocalEncryptedFileProvider.generate_key() and set it before starting the "
                "process. Never commit this key to source control."
            )
        try:
            return Fernet(key.encode("utf-8"))
        except (ValueError, TypeError) as exc:
            raise SecretsBackendUnavailableError(
                f"Value in '{self._key_env_var}' is not a valid Fernet key."
            ) from exc

    def _load_store(self) -> dict[str, str]:
        if not self._file_path.exists():
            return {}
        encrypted = self._file_path.read_bytes()
        if not encrypted:
            return {}
        try:
            decrypted = self._fernet.decrypt(encrypted)
        except InvalidToken as exc:
            raise SecretsProviderError(
                f"Failed to decrypt secrets file at {self._file_path}: wrong key or corrupted file."
            ) from exc
        return json.loads(decrypted.decode("utf-8"))

    def _save_store(self, store: dict[str, str]) -> None:
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(store).encode("utf-8")
        encrypted = self._fernet.encrypt(payload)
        self._file_path.write_bytes(encrypted)
        try:
            os.chmod(self._file_path, 0o600)
        except OSError:
            # Best effort — chmod semantics differ on non-POSIX systems.
            logger.debug("Could not chmod secrets file (non-POSIX filesystem?)")

    def get_secret(self, name: str) -> str:
        store = self._load_store()
        if name not in store:
            raise SecretNotFoundError(f"Secret '{name}' not found in local encrypted store.")
        return store[name]

    def set_secret(self, name: str, value: str) -> None:
        store = self._load_store()
        store[name] = value
        self._save_store(store)
        logger.info("Secret '%s' written to local encrypted store (value not logged).", name)

    def secret_exists(self, name: str) -> bool:
        return name in self._load_store()

    @staticmethod
    def generate_key() -> str:
        """Generate a new Fernet key. Run this once per environment, store the
        result in the environment variable named by `local_key_env_var` in
        config (e.g. via AWS SSM Parameter Store, not committed to the repo).
        """
        return Fernet.generate_key().decode("utf-8")
