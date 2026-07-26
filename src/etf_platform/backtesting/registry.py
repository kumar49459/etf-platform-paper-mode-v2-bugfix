"""SQLite-backed registry for `backtest_runs`, exactly the table already
specified in PHASE1_Architecture_SRS.md §6. Reuses the same WAL-mode +
lock-guarded pattern as Phase 2's SnapshotRegistry (see
data_engine/storage/snapshot_registry.py) rather than inventing a different
persistence approach for what is structurally the same kind of table.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

from etf_platform.common import db
from etf_platform.common.logging_setup import get_logger

logger = get_logger("backtesting.registry")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id            TEXT PRIMARY KEY,
    code_commit_hash  TEXT NOT NULL,
    code_is_dirty     INTEGER NOT NULL,
    config_version    TEXT NOT NULL,
    data_snapshot_id  TEXT NOT NULL,
    start_date        TEXT NOT NULL,
    end_date          TEXT NOT NULL,
    symbols           TEXT NOT NULL,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    metrics_json      TEXT
);
"""


class BacktestRunRegistry:
    def __init__(self, db_path: str | Path) -> None:
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection = db.connect(db_path)
        with self._lock:
            self._conn.executescript(_SCHEMA)

    def register_run_start(self, config, reproducibility_record) -> None:
        repro = reproducibility_record
        cfg = config
        with self._lock, db.transaction(self._conn):
            self._conn.execute(
                """
                INSERT INTO backtest_runs
                    (run_id, code_commit_hash, code_is_dirty, config_version, data_snapshot_id,
                     start_date, end_date, symbols, started_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    repro.run_id, repro.code_commit_hash, int(repro.code_is_dirty), repro.config_version,
                    repro.data_snapshot_id, cfg.start_date.isoformat(), cfg.end_date.isoformat(),
                    ",".join(cfg.symbols), repro.started_at,
                ),
            )
        logger.info("Registered backtest run start: %s", repro.run_id)

    def register_run_finish(self, run_id: str, finished_at: str, metrics: dict) -> None:
        with self._lock, db.transaction(self._conn):
            self._conn.execute(
                "UPDATE backtest_runs SET finished_at = ?, metrics_json = ? WHERE run_id = ?",
                (finished_at, json.dumps(metrics, default=str), run_id),
            )
        logger.info("Registered backtest run finish: %s", run_id)

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM backtest_runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def run_and_register(
    engine,
    bars_by_symbol,
    registry: "BacktestRunRegistry",
    reproducibility_record,
    corporate_actions_by_symbol=None,
):
    """Run a backtest and guarantee `backtest_runs` is always finalized —
    on success with real metrics, on failure with the error recorded —
    rather than leaving a run stuck in 'running' forever if `engine.run()`
    raises partway through. Found during the Phase 4 adversarial review:
    without this wrapper, a caller that forgot the try/except around
    `register_run_finish()` would leave an unfinished row with no record
    of what happened, defeating the auditability this registry exists for.
    """
    from datetime import datetime, timezone

    registry.register_run_start(engine.config, reproducibility_record)

    try:
        result = engine.run(bars_by_symbol, corporate_actions_by_symbol)
        result.reproducibility = reproducibility_record
        metrics = {"status": "succeeded", "warnings": result.warnings, "num_trades": len(result.trades)}
        registry.register_run_finish(reproducibility_record.run_id, datetime.now(timezone.utc).isoformat(), metrics)
        return result
    except Exception as exc:  # noqa: BLE001 — deliberately broad: record ANY failure before re-raising
        metrics = {"status": "failed", "error": str(exc), "error_type": type(exc).__name__}
        registry.register_run_finish(reproducibility_record.run_id, datetime.now(timezone.utc).isoformat(), metrics)
        raise
