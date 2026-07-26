"""Abstract SecretsProvider interface.

Every concrete backend (local encrypted file, AWS Secrets Manager, and any
future backend) implements this same interface. Nothing outside the
`secrets_manager` package should ever import a concrete provider directly —
callers depend only on `SecretsManager`, which selects a provider based on
config. This is the same adapter pattern already committed to for data
providers (Phase 1 §12.6) and symbol resolution.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class SecretsProvider(ABC):
    """Backend-agnostic secret storage/retrieval interface."""

    @abstractmethod
    def get_secret(self, name: str) -> str:
        """Return the plaintext value of `name`. Raises SecretNotFoundError if absent."""

    @abstractmethod
    def set_secret(self, name: str, value: str) -> None:
        """Store or update a secret. Not all providers necessarily support this
        (e.g. a production AWS backend may intentionally disable writes from the
        application — rotation should go through infrastructure tooling, not
        the running trading process). Providers that don't support writes should
        raise NotImplementedError with a clear message rather than silently no-op.
        """

    @abstractmethod
    def secret_exists(self, name: str) -> bool:
        """Return True if `name` exists, without raising and without logging the value."""
