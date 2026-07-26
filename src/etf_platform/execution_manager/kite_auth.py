"""KiteAuthManager (Milestone 6). Per the architecture review and DDR
process: daily interactive login (with 2FA) is a regulatory requirement,
not a technical inconvenience -- this class does NOT automate it. Its job
is narrower: accept an already-obtained access_token, sign requests with
it, compute the one-time token-exchange checksum, and fail loudly and
immediately when no valid token is available -- never silently retry
against a dead token, never attempt to work around a missing one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass


class KiteAuthenticationRequiredError(Exception):
    """Raised whenever no valid access_token is available. Distinct from
    BrokerCommunicationError (a transient/network condition) -- this is
    an operator-actionable state requiring the manual login flow
    (OPERATIONAL_RUNBOOK.md), not something retryable by the caller."""


@dataclass(frozen=True)
class KiteSession:
    api_key: str
    access_token: str
    acquired_at: object


class KiteAuthManager:
    def __init__(self, api_key, api_secret):
        self._api_key = api_key
        self._api_secret = api_secret
        self._session = None

    @property
    def api_key(self):
        return self._api_key

    def compute_checksum(self, request_token):
        payload = f"{self._api_key}{request_token}{self._api_secret}".encode()
        return hashlib.sha256(payload).hexdigest()

    def set_session(self, access_token, acquired_at):
        self._session = KiteSession(api_key=self._api_key, access_token=access_token, acquired_at=acquired_at)

    def clear_session(self):
        self._session = None

    def get_access_token(self):
        if self._session is None:
            raise KiteAuthenticationRequiredError(
                "No Kite session available. Daily interactive login (with 2FA) is required and cannot be "
                "automated -- see OPERATIONAL_RUNBOOK.md's live-trading Startup section. Complete the manual "
                "login flow and call set_session() before any KiteBrokerPort operation."
            )
        return self._session.access_token

    def has_valid_session(self):
        return self._session is not None
