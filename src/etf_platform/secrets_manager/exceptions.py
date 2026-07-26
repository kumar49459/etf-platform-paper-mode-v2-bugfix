"""Exceptions raised by the Secrets Manager and its providers."""


class SecretsProviderError(Exception):
    """Base class for all secrets-provider errors (encryption, network, config)."""


class SecretNotFoundError(SecretsProviderError):
    """Raised when a requested secret name does not exist in the active provider."""


class SecretsBackendUnavailableError(SecretsProviderError):
    """Raised when a provider's runtime dependency (e.g. boto3) is not installed.

    Deliberately distinct from SecretNotFoundError: this is an environment
    configuration problem, not a missing-secret problem, and should be
    diagnosed differently.
    """
