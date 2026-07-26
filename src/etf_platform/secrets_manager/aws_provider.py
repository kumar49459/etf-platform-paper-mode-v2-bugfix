"""AWS Secrets Manager-backed secrets provider.

`boto3` is imported lazily (inside methods, not at module scope) so that
this file can be imported — and the rest of the package used — on a machine
that doesn't have boto3 installed at all, e.g. a minimal local-dev checkout.
The error raised if boto3 truly is missing at call time is explicit and
actionable rather than an opaque ImportError surfacing from deep in the
module.

Design decision: the whole secret bundle (all Kite credentials, etc.) is
stored as a single JSON-valued AWS secret, not one AWS secret per key. AWS
Secrets Manager bills per secret per month; bundling keeps cost predictable
and avoids the temptation to under-use secrets management for "small"
values. A short in-memory TTL cache avoids re-fetching on every call
without risking indefinitely stale credentials after a rotation.
"""

from __future__ import annotations

import json
import time
from typing import Any

from etf_platform.common.logging_setup import get_logger
from etf_platform.secrets_manager.base import SecretsProvider
from etf_platform.secrets_manager.exceptions import (
    SecretNotFoundError,
    SecretsBackendUnavailableError,
    SecretsProviderError,
)

logger = get_logger("secrets_manager.aws_provider")


class AWSSecretsManagerProvider(SecretsProvider):
    """SecretsProvider backed by a single AWS Secrets Manager JSON-bundle secret, with a short-TTL in-memory cache."""
    def __init__(self, secret_name: str, region_name: str, cache_ttl_seconds: float = 300.0) -> None:
        self._secret_name = secret_name
        self._region_name = region_name
        self._cache_ttl_seconds = cache_ttl_seconds
        self._cache: dict[str, str] = {}
        self._cache_loaded_at: float | None = None
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3  # noqa: local import by design, see module docstring
            except ImportError as exc:
                raise SecretsBackendUnavailableError(
                    "boto3 is required for the AWS Secrets Manager provider but is not installed "
                    "in this environment. Add it to requirements-aws.txt / the live deployment image."
                ) from exc
            self._client = boto3.client("secretsmanager", region_name=self._region_name)
        return self._client

    def _refresh_cache(self) -> None:
        client = self._get_client()
        try:
            response = client.get_secret_value(SecretId=self._secret_name)
        except Exception as exc:  # noqa: BLE001 — botocore exceptions vary by failure mode
            raise SecretsProviderError(
                f"Failed to fetch secret bundle '{self._secret_name}' from AWS Secrets Manager "
                f"in region '{self._region_name}'."
            ) from exc

        raw = response.get("SecretString", "{}")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise SecretsProviderError(
                f"Secret '{self._secret_name}' is not valid JSON. This provider expects a single "
                "AWS secret holding a JSON object of {name: value} pairs, not one AWS secret per key."
            ) from exc

        if not isinstance(parsed, dict):
            raise SecretsProviderError(f"Secret '{self._secret_name}' must decode to a JSON object.")

        self._cache = {str(k): str(v) for k, v in parsed.items()}
        self._cache_loaded_at = time.monotonic()
        logger.info("Refreshed secret bundle '%s' from AWS Secrets Manager.", self._secret_name)

    def _ensure_fresh_cache(self) -> None:
        now = time.monotonic()
        is_stale = (
            self._cache_loaded_at is None
            or (now - self._cache_loaded_at) >= self._cache_ttl_seconds
        )
        if is_stale:
            self._refresh_cache()

    def get_secret(self, name: str) -> str:
        self._ensure_fresh_cache()
        if name not in self._cache:
            raise SecretNotFoundError(
                f"Secret '{name}' not found in AWS secret bundle '{self._secret_name}'."
            )
        return self._cache[name]

    def set_secret(self, name: str, value: str) -> None:
        raise NotImplementedError(
            "Writing secrets from the running application is intentionally unsupported for the "
            "AWS provider. Secret rotation should go through AWS Secrets Manager / infrastructure "
            "tooling, not the live trading process, per Phase 1 least-privilege principles."
        )

    def secret_exists(self, name: str) -> bool:
        try:
            self._ensure_fresh_cache()
        except SecretsProviderError:
            return False
        return name in self._cache
