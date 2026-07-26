"""Secrets Manager (Phase 1 Module: Secrets & Credentials Manager, §1.3).

Provider-abstracted secrets access: a local Fernet-encrypted file backend for
dev/micro deployments, and an AWS Secrets Manager backend for production live
trading. Callers use `SecretsManager.get_secret(name)` and never know or care
which backend is active.
"""

from etf_platform.secrets_manager.exceptions import SecretNotFoundError, SecretsProviderError
from etf_platform.secrets_manager.secrets_manager import SecretsManager

__all__ = ["SecretsManager", "SecretNotFoundError", "SecretsProviderError"]
