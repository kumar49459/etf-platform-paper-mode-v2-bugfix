"""SQLite-backed registry of data snapshots and ingestion runs.

This is the transactional-store side of Phase 1 §6 (as opposed to the bulk
time-series data, which lives in TimeSeriesStore/Parquet/CSV). Uses the
shared `common.db` connection helper (WAL mode, per §13.2).

Thread safety: this class holds ONE shared `sqlite3.Connection` (opened with
`check_same_thread=False`, see common/db.py) and guards every use of it with
an internal `threading.Lock`. This makes a single `SnapshotRegistry`
instance safe to call from multiple threads within one process — e.g. a
background ingestion thread and a request-handling thread sharing one
`HistoricalDataEngine`. It does NOT by itself make cross-*process*
concurrent writes safe beyond what SQLite's WAL mode + busy_timeout already
provide (one writer at a time, readers don't block on it) — that's the
"single-writer-per-domain" principle from Phase 1 §13.2, enforced by
deployment/process design, not by this lock.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

from etf_platform.common import db
from etf_platform.common.logging_setup import get_logger
from etf_platform.data_engine.exceptions import SnapshotNotFoundError
from etf_platform.data_engine.models import DataSnapshot

logger = get_logger("data_engine.storage.snapshot_registry")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS data_snapshots (
    snapshot_id     TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    symbols         TEXT NOT NULL,      -- comma-separated
    start_date      TEXT NOT NULL,
    end_date        TEXT NOT NULL,
    source_providers TEXT NOT NULL,     -- comma-separated
    row_count       INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    snapshot_id     TEXT NOT NULL,      -- intentionally NOT a foreign key: a run
                                         -- must be trackable even when it fails or
                                         -- is halted before any snapshot is ever
                                         -- registered (that's the primary case this
                                         -- table exists to audit) — see start_ingestion_run().
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL,      -- running | succeeded | failed | halted_critical
    detail          TEXT
);
"""


class SnapshotRegistry:
    """SQLite-backed (WAL mode) registry of immutable data snapshots and their ingestion run history. Thread-safe for concurrent use within one process via an internal lock."""
    def __init__(self, db_path: str | Path) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection = db.connect(db_path)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def register_snapshot(self, snapshot: DataSnapshot) -> None:
        with self._lock, db.transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO data_snapshots
                    (snapshot_id, created_at, symbols, start_date, end_date, source_providers, row_count)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.snapshot_id,
                    snapshot.created_at.isoformat(),
                    ",".join(snapshot.symbols),
                    snapshot.start_date.isoformat(),
                    snapshot.end_date.isoformat(),
                    ",".join(snapshot.source_providers),
                    snapshot.row_count,
                ),
            )
        logger.info("Registered snapshot '%s' (%d rows).", snapshot.snapshot_id, snapshot.row_count)

    def get_snapshot(self, snapshot_id: str) -> DataSnapshot:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM data_snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
        if row is None:
            raise SnapshotNotFoundError(f"No snapshot registered with id '{snapshot_id}'")
        return DataSnapshot(
            snapshot_id=row["snapshot_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            symbols=tuple(row["symbols"].split(",")) if row["symbols"] else (),
            start_date=date.fromisoformat(row["start_date"]),
            end_date=date.fromisoformat(row["end_date"]),
            source_providers=tuple(row["source_providers"].split(",")) if row["source_providers"] else (),
            row_count=row["row_count"],
        )

    def latest_snapshot_id(self) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT snapshot_id FROM data_snapshots ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return row["snapshot_id"] if row else None

    def start_ingestion_run(self, snapshot_id: str) -> int:
        with self._lock, db.transaction(self._conn):
            cursor = self._conn.execute(
                "INSERT INTO ingestion_runs (snapshot_id, started_at, status) VALUES (?, ?, 'running')",
                (snapshot_id, datetime.now().isoformat()),
            )
            return cursor.lastrowid

    def finish_ingestion_run(self, run_id: int, status: str, detail: str = "") -> None:
        with self._lock, db.transaction(self._conn):
            self._conn.execute(
                "UPDATE ingestion_runs SET finished_at = ?, status = ?, detail = ? WHERE run_id = ?",
                (datetime.now().isoformat(), status, detail, run_id),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
