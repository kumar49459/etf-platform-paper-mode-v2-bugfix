"""Shared SQLite access helper.

Design decision (binding, per PHASE1_Architecture_SRS.md §13.2):
WAL (Write-Ahead Logging) mode is enabled on every connection this platform
opens. WAL allows concurrent readers alongside a single writer without the
whole-file locking that SQLite's default rollback-journal mode uses — this
matters once the live micro instance has more than one process reading
(Dashboard, Approval Console) while another writes (Live Trading Engine,
Data Engine's nightly ingestion). See §13.2 for the full rationale and the
rejected alternative (default journal mode).

We use raw `sqlite3` (stdlib) with a thin wrapper rather than an ORM
(SQLAlchemy, etc.) — see PHASE1 §5.1 / Phase 2 design notes: the schema is
simple and single-writer-per-table by design, so an ORM would add
dependency weight without a corresponding benefit at this stage. This
decision can be revisited if/when schema complexity grows materially
(tracked as a documented future decision, not a silent default).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from etf_platform.common.logging_setup import get_logger

logger = get_logger("common.db")


def connect(db_path: str | Path, *, timeout_seconds: float = 30.0) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and sane defaults enabled.

    - WAL journal mode: concurrent readers + one writer.
    - foreign_keys ON: referential integrity is enforced, not just assumed.
    - busy_timeout: instead of failing immediately on a lock conflict, retry
      for up to `timeout_seconds` — appropriate for a low-write-volume system
      where a lock is almost always transient, not a real contention problem.
    - check_same_thread=False: stdlib sqlite3's default (True) raises if a
      connection is used from any thread other than the one that created it.
      That default is safe but too restrictive here — a single
      HistoricalDataEngine instance may reasonably be called from more than
      one thread within one process (e.g. a background ingestion thread and
      a request-handling thread both reading via the same SnapshotRegistry).
      This is deliberately paired with an explicit `threading.Lock` around
      every connection use in SnapshotRegistry (see that module) — disabling
      this check WITHOUT that lock would be a real thread-safety bug, not a
      fix. WAL mode's own multi-connection concurrency guarantees are a
      separate, complementary mechanism for *separate* connections across
      threads/processes, not a substitute for this lock on one *shared*
      connection.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=timeout_seconds, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute(f"PRAGMA busy_timeout={int(timeout_seconds * 1000)};")
    logger.debug("Opened SQLite connection to %s (WAL mode)", path)
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Context manager wrapping a single explicit transaction.

    Because we open connections with isolation_level=None (autocommit), every
    write must be wrapped explicitly — this makes transaction boundaries
    visible in the calling code rather than implicit, which matters for
    auditability (Phase 1 §1.4 NFR).
    """
    conn.execute("BEGIN;")
    try:
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        logger.exception("Transaction rolled back")
        raise
