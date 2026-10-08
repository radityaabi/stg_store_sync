"""Persistent state: SQLite for jobs, run log, conflicts, and row tracking.

Responsibilities:

* **run log** — one row per sync run (`dry_run`, status, counts, timestamps).
* **conflicts** — quarantined/conflicting keys, linked to a run.
* **tracking** — the match keys this system has written (`tracked_only` delete
  policy) and a content hash per row for cheap change detection/idempotency.

Hashes are canonical (sorted keys) so the same logical record hashes the same
across runs. Pass `":memory:"` for tests.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job TEXT NOT NULL,
    dry_run INTEGER NOT NULL,
    status TEXT,
    counts TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
CREATE TABLE IF NOT EXISTS conflicts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    match_value TEXT,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS tracked (
    job TEXT NOT NULL,
    match_value TEXT NOT NULL,
    row_hash TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (job, match_value)
);
"""


class State:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- runs --------------------------------------------------------------

    def start_run(self, *, job: str, dry_run: bool) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (job, dry_run) VALUES (?, ?)", (job, int(bool(dry_run)))
        )
        self.conn.commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, *, status: str, counts: dict) -> None:
        self.conn.execute(
            "UPDATE runs SET status = ?, counts = ?, finished_at = datetime('now') WHERE id = ?",
            (status, json.dumps(counts, sort_keys=True), run_id),
        )
        self.conn.commit()

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["dry_run"] = bool(d["dry_run"])
        d["counts"] = json.loads(d["counts"]) if d["counts"] else {}
        return d

    def get_run(self, run_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def recent_runs(self, job: str, *, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM runs WHERE job = ? ORDER BY id DESC LIMIT ?", (job, limit)
        ).fetchall()
        return [self._run_row(r) for r in rows]

    # -- conflicts ---------------------------------------------------------

    def record_conflict(self, run_id: int, *, match_value, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO conflicts (run_id, match_value, reason) VALUES (?, ?, ?)",
            (run_id, "" if match_value is None else str(match_value), reason),
        )
        self.conn.commit()

    def conflicts_for_run(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM conflicts WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    # -- tracking ----------------------------------------------------------

    @staticmethod
    def row_hash(record: dict) -> str:
        canon = json.dumps(record, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(canon.encode("utf-8")).hexdigest()

    def track_row(self, *, job: str, match_value, row_hash: str) -> None:
        self.conn.execute(
            "INSERT INTO tracked (job, match_value, row_hash, updated_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(job, match_value) DO UPDATE SET "
            "row_hash = excluded.row_hash, updated_at = excluded.updated_at",
            (job, str(match_value), row_hash),
        )
        self.conn.commit()

    def untrack_row(self, *, job: str, match_value) -> None:
        self.conn.execute(
            "DELETE FROM tracked WHERE job = ? AND match_value = ?",
            (job, str(match_value)),
        )
        self.conn.commit()

    def tracked_keys(self, job: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT match_value FROM tracked WHERE job = ?", (job,)
        ).fetchall()
        return {r["match_value"] for r in rows}

    def tracked_hash(self, job: str, match_value) -> str | None:
        row = self.conn.execute(
            "SELECT row_hash FROM tracked WHERE job = ? AND match_value = ?",
            (job, str(match_value)),
        ).fetchone()
        return row["row_hash"] if row else None
