"""Snapshot ID generation.

Snapshot IDs are timestamp-prefixed (sortable, human-readable in logs/S3
paths) with a short random suffix (collision-avoidance for two snapshots
started in the same second, e.g. a manual re-run immediately after a
scheduled one).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone


def generate_snapshot_id(prefix: str = "snapshot") -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = secrets.token_hex(3)
    return f"{prefix}-{timestamp}-{suffix}"
