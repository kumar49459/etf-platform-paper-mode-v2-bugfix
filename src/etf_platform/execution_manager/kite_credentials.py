"""Kite credential loading (Milestone 7, Blocker 3). Connects the
already-frozen SecretsManager to the already-frozen KiteAuthManager's
existing constructor -- glue code, not a modification to either.
"""

from __future__ import annotations

from etf_platform.execution_manager.kite_auth import KiteAuthManager
from etf_platform.secrets_manager.exceptions import SecretNotFoundError

KITE_API_KEY_SECRET_NAME = "kite_api_key"
KITE_API_SECRET_SECRET_NAME = "kite_api_secret"


class MissingKiteCredentialsError(Exception):
    """Raised when either required secret is absent. Distinct from
    SecretNotFoundError so callers at the production-runner level can
    catch one clear, Kite-specific exception."""


def load_kite_auth_manager(secrets_manager):
    """Fetches both required secrets, fails fast and loudly if either is
    absent, and constructs KiteAuthManager exactly as its existing,
    unmodified constructor expects. No plaintext credential ever appears
    in application code calling this function."""
    missing = []
    api_key = None
    api_secret = None

    try:
        api_key = secrets_manager.get_secret(KITE_API_KEY_SECRET_NAME)
    except SecretNotFoundError:
        missing.append(KITE_API_KEY_SECRET_NAME)

    try:
        api_secret = secrets_manager.get_secret(KITE_API_SECRET_SECRET_NAME)
    except SecretNotFoundError:
        missing.append(KITE_API_SECRET_SECRET_NAME)

    if missing:
        raise MissingKiteCredentialsError(
            f"Cannot start: the following required secret(s) are not present in SecretsManager: "
            f"{missing}. Store them (e.g. via SecretsManager.set_secret()) before starting the "
            f"production runner. Refusing to proceed with a partial or absent credential set -- "
            f"this platform never falls back to a plaintext or default value for live trading credentials."
        )

    return KiteAuthManager(api_key=api_key, api_secret=api_secret)
