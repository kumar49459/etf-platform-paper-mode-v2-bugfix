"""Durable archives (Milestone 5B, requirement 5/6) -- found as REAL
DEFECTS via this milestone's own 365-day validation run, not preemptive
design. session.py's periodic clearing of both the event recorder AND
SessionState.cycle_log (for memory-boundedness, matching Module 28's
established pattern) discarded data with nothing durable saved first.
The event recorder was found completely empty after a 30-day run,
violating "the complete execution history must be reconstructable."
cycle_log's unbounded growth was found via the 365-day run's own
resource-trend analysis (memory_kb classified GROWING, traced to
cycle_log being the one structure in this session that was never
archived, unlike events and the broker's own order dict).

InMemoryEventRecorder's own docstring already anticipated this need
("NOT suitable on its own for the long-duration simulation... a
long-duration simulation that wants bounded memory calls clear() itself
between measurement checkpoints") -- the missing piece was ever actually
archiving before clearing. Both archives here are that missing piece:
append-only JSONL, written incrementally, readable back for
reconstruction.
"""

from __future__ import annotations

import json
from pathlib import Path


def _event_to_dict(event):
    return {
        "event_type": event.event_type.value,
        "timestamp": event.timestamp.isoformat(),
        "broker_order_id": event.broker_order_id,
        "symbol": event.symbol,
        "details": event.details,
        "correlation_id": event.correlation_id,
        "cycle_id": event.cycle_id,
        "component": event.component,
        "result": event.result,
    }


class EventArchive:
    """Append-only JSONL file. archive_and_clear() is the only write
    path -- always archives BEFORE clearing, never the other way around,
    so a crash between the two calls loses at most the not-yet-cleared
    in-memory events (still sitting in the recorder, not lost), never
    loses already-cleared events (they were archived first)."""

    def __init__(self, archive_path):
        self._path = Path(archive_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def archive_and_clear(self, event_recorder):
        events = event_recorder.events()
        if events:
            with open(self._path, "a") as f:
                for event in events:
                    f.write(json.dumps(_event_to_dict(event)) + "\n")
        event_recorder.clear()
        return len(events)

    def read_all(self):
        if not self._path.exists():
            return []
        rows = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def reconstruct_by_cycle_id(self, cycle_id, live_event_recorder=None):
        archived = [row for row in self.read_all() if row.get("cycle_id") == cycle_id]
        live = []
        if live_event_recorder is not None:
            live = [_event_to_dict(e) for e in live_event_recorder.events() if e.cycle_id == cycle_id]
        combined = archived + live
        combined.sort(key=lambda r: r["timestamp"])
        return combined


class CycleLogArchive:
    """Same append-only-JSONL design as EventArchive, applied to
    SessionState.cycle_log -- found necessary via Milestone 5B's own
    365-day validation run, not designed preemptively. cycle_log is
    small in absolute terms even over a full simulated year (~58KB), but
    the growth pattern was genuinely unbounded, unlike events and the
    broker's own order dict, which were already periodically managed."""

    def __init__(self, archive_path):
        self._path = Path(archive_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def archive_and_trim(self, cycle_log_list):
        if cycle_log_list:
            with open(self._path, "a") as f:
                for entry in cycle_log_list:
                    row = {
                        "execution_id": entry.execution_id, "cycle_id": entry.cycle_id, "symbol": entry.symbol,
                        "quantity_proposed": entry.quantity_proposed, "final_status": entry.final_status.value,
                        "broker_order_id": entry.broker_order_id, "executed_quantity": entry.executed_quantity,
                        "executed_price": entry.executed_price, "limit_price": entry.limit_price,
                        "as_of_date": entry.as_of_date.isoformat(), "rejection_notes": list(entry.rejection_notes),
                    }
                    f.write(json.dumps(row) + "\n")
        archived_count = len(cycle_log_list)
        cycle_log_list.clear()
        return archived_count

    def read_all(self):
        if not self._path.exists():
            return []
        rows = []
        with open(self._path) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows

    def read_in_range(self, start_date, end_date):
        return [r for r in self.read_all() if start_date.isoformat() <= r["as_of_date"] <= end_date.isoformat()]
