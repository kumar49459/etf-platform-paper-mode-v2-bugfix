"""Persistence for StrategyEngineState - Strategy Engine's own operational
state (funding state machine progress, Pause/Discontinue flags), distinct
from Module 28's cash ledger and from a backtest's local Portfolio state.

Reuses the exact WAL+lock pattern established in Phase 2 (SnapshotRegistry)
and reused in Phase 4/5 (BacktestRunRegistry, RiskEventRegistry) - see
common/db.py for the underlying connection helper and rationale.

This exists because Strategy Engine is deliberately stateless BETWEEN
invocations (PHASE1_Architecture_SRS.md section 17, PHASE6_Objectives.md
section 0.5) - a short-lived process invoked once a day cannot remember
"did I already send this month's reminder" in memory, since the process
exits between invocations. This store is what makes that memory durable.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path

from etf_platform.common import db
from etf_platform.common.logging_setup import get_logger
from etf_platform.strategy_engine.models import FundingState, StrategyEngineState

logger = get_logger("strategy_engine.state_store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_engine_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    current_month TEXT NOT NULL,
    funding_state TEXT NOT NULL,
    reminder_sent_this_month INTEGER NOT NULL,
    last_check_date TEXT,
    paused INTEGER NOT NULL DEFAULT 0,
    discontinued INTEGER NOT NULL DEFAULT 0
);
"""


class StrategyStateStore:
    """Single-row table (id=1) holding the current operational state.
    Single-row by design: Strategy Engine tracks one funding cycle at a
    time, not a history of every day's check - daily check OUTCOMES are
    audit-logged separately (application logging, not this table); this
    table is current-state only, matching how a state machine's persisted
    state is normally modeled (current state, not an event log)."""

    def __init__(self, db_path):
        self._lock = threading.Lock()
        self._conn = db.connect(db_path)
        with self._lock, db.transaction(self._conn):
            self._conn.execute(_SCHEMA)
        # Found during the production verification review: db.connect()'s
        # WAL mode uses SQLite's default synchronous=NORMAL, which protects
        # against corruption but does NOT guarantee the most recent commit
        # survives an actual power loss (as distinct from a process crash --
        # a power loss can lose a commit that hadn't yet been flushed to
        # physical disk). FULL forces an fsync on every commit. This is
        # scoped to StrategyStateStore specifically (not a change to the
        # frozen common/db.py, which every other registry also uses) since
        # this store's writes are low-frequency (at most a few per day) and
        # directly gate real investment decisions -- the fsync cost here is
        # negligible and the durability guarantee matters more than it does
        # for, say, a snapshot registry's higher-frequency writes.
        with self._lock:
            self._conn.execute("PRAGMA synchronous=FULL;")

    def load(self):
        with self._lock:
            row = self._conn.execute("SELECT * FROM strategy_engine_state WHERE id = 1").fetchone()
        if row is None:
            return None
        return StrategyEngineState(
            current_month=row["current_month"],
            funding_state=FundingState(row["funding_state"]),
            reminder_sent_this_month=bool(row["reminder_sent_this_month"]),
            last_check_date=date.fromisoformat(row["last_check_date"]) if row["last_check_date"] else None,
            paused=bool(row["paused"]),
            discontinued=bool(row["discontinued"]),
        )

    def save(self, state):
        with self._lock, db.transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO strategy_engine_state
                    (id, current_month, funding_state, reminder_sent_this_month, last_check_date, paused, discontinued)
                VALUES (1, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    current_month = excluded.current_month,
                    funding_state = excluded.funding_state,
                    reminder_sent_this_month = excluded.reminder_sent_this_month,
                    last_check_date = excluded.last_check_date,
                    paused = excluded.paused,
                    discontinued = excluded.discontinued
                """,
                (
                    state.current_month, state.funding_state.value, int(state.reminder_sent_this_month),
                    state.last_check_date.isoformat() if state.last_check_date else None,
                    int(state.paused), int(state.discontinued),
                ),
            )
        logger.info(
            "Persisted strategy engine state: month=%s funding_state=%s reminder_sent=%s paused=%s discontinued=%s",
            state.current_month, state.funding_state.value, state.reminder_sent_this_month,
            state.paused, state.discontinued,
        )

    def close(self):
        with self._lock:
            self._conn.close()
