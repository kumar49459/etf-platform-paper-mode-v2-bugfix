"""SQLite-backed registry for `risk_events`, the table already specified
in PHASE1_Architecture_SRS.md §6 (unused until Phase 5 -- this is its first
real consumer). Reuses the same WAL-mode + lock-guarded pattern as Phase
2's SnapshotRegistry and Phase 4's BacktestRunRegistry, rather than
inventing a third persistence approach for what is structurally the same
kind of table.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from etf_platform.common import db
from etf_platform.common.logging_setup import get_logger
from etf_platform.risk_management.models import RiskEvent, RiskEventType, Severity

logger = get_logger("risk_management.registry")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_events (
    event_id            TEXT PRIMARY KEY,
    timestamp            TEXT NOT NULL,
    event_type           TEXT NOT NULL,
    severity              TEXT NOT NULL,
    description           TEXT NOT NULL,
    recommended_action    TEXT NOT NULL,
    symbol                TEXT,
    action_taken           TEXT
);
"""


class RiskEventRegistry:
    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection = db.connect(db_path)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def record(self, event: RiskEvent, action_taken: str = "") -> None:
        with self._lock, db.transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO risk_events
                    (event_id, timestamp, event_type, severity, description, recommended_action,
                     symbol, action_taken)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id, event.timestamp.isoformat(), event.event_type.value, event.severity.value,
                    event.description, event.recommended_action, event.symbol, action_taken,
                ),
            )
        logger.info("Recorded risk event %s: %s (%s)", event.event_id, event.event_type.value, event.severity.value)

    def get_event(self, event_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM risk_events WHERE event_id = ?", (event_id,)).fetchone()
        return dict(row) if row else None

    def list_events(self, severity: Severity | None = None, event_type: RiskEventType | None = None) -> list[dict]:
        query = "SELECT * FROM risk_events WHERE 1=1"
        params: list[str] = []
        if severity is not None:
            query += " AND severity = ?"
            params.append(severity.value)
        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type.value)
        query += " ORDER BY timestamp DESC"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
