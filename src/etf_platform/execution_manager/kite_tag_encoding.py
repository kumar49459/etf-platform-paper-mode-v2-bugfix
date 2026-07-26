"""Kite tag encoding (Milestone 6, Decision 2's approved design).
Deterministic, collision-resistant, alphanumeric-only encoding of
client_reference (cycle_id) into Kite's 20-character tag constraint, with
a locally-persisted mapping table making it reversible for audit and
reconciliation purposes.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def encode_tag(client_reference):
    digest = hashlib.sha256(client_reference.encode()).hexdigest()
    return digest[:20]


class TagMappingStore:
    """The 'reversible via a lookup table' half of Decision 2. Append-only
    JSONL, same durability pattern as EventArchive/CycleLogArchive
    (paper_trading_operations)."""

    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._cache = {}
        if self._path.exists():
            with open(self._path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        row = json.loads(line)
                        self._cache[row["tag"]] = row["client_reference"]

    def record(self, client_reference):
        tag = encode_tag(client_reference)
        if tag not in self._cache:
            with open(self._path, "a") as f:
                f.write(json.dumps({"tag": tag, "client_reference": client_reference}) + "\n")
            self._cache[tag] = client_reference
        return tag

    def resolve(self, tag):
        return self._cache.get(tag)
