"""SQLite storage: one row per record, plus a log of runs.

Records keep their cleaned fields as JSON, so the same database works for any
dataset. A hash of the fields shows when the source later edits a record.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id        INTEGER PRIMARY KEY,
    ran_at        TEXT NOT NULL,
    window_start  TEXT NOT NULL,
    fetched       INTEGER NOT NULL DEFAULT 0,
    new           INTEGER NOT NULL DEFAULT 0,
    updated       INTEGER NOT NULL DEFAULT 0,
    skipped       INTEGER NOT NULL DEFAULT 0,
    issues        TEXT,              -- JSON: column -> count of values that couldn't be read
    status        TEXT NOT NULL,     -- running, ok, failed
    error         TEXT
);

CREATE TABLE IF NOT EXISTS records (
    id               TEXT PRIMARY KEY,
    record_date      TEXT,
    data             TEXT NOT NULL,  -- JSON of the cleaned output columns
    data_hash        TEXT NOT NULL,
    first_seen_run   INTEGER NOT NULL REFERENCES runs(run_id),
    last_changed_run INTEGER NOT NULL REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS ix_records_date ON records(record_date);
CREATE INDEX IF NOT EXISTS ix_records_first_seen ON records(first_seen_run);
"""


def data_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


class Store:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def latest_record_date(self) -> str | None:
        return self.conn.execute("SELECT MAX(record_date) FROM records").fetchone()[0]

    def start_run(self, ran_at: str, window_start: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (ran_at, window_start, status) VALUES (?, ?, 'running')", (ran_at, window_start)
        )
        self.conn.commit()
        return cur.lastrowid

    def upsert(self, run_id: int, record_id: str, record_date: str | None, data: dict) -> str:
        """Save one record. Returns 'new', 'updated' or 'unchanged'."""
        digest = data_hash(data)
        row = self.conn.execute("SELECT data_hash FROM records WHERE id = ?", (record_id,)).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?)",
                (record_id, record_date, json.dumps(data, default=str), digest, run_id, run_id),
            )
            return "new"
        if row[0] == digest:
            return "unchanged"
        self.conn.execute(
            "UPDATE records SET record_date = ?, data = ?, data_hash = ?, last_changed_run = ? WHERE id = ?",
            (record_date, json.dumps(data, default=str), digest, run_id, record_id),
        )
        return "updated"

    def finish_run(self, run_id: int, *, status: str, fetched=0, new=0, updated=0, skipped=0, issues=None, error=None):
        self.conn.execute(
            """UPDATE runs SET status = ?, fetched = ?, new = ?, updated = ?, skipped = ?, issues = ?, error = ?
               WHERE run_id = ?""",
            (status, fetched, new, updated, skipped, json.dumps(issues) if issues else None, error, run_id),
        )
        self.conn.commit()

    def rollback(self) -> None:
        self.conn.rollback()
