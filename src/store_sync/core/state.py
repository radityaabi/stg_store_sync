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
from contextlib import contextmanager
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
CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    job         TEXT    NOT NULL,
    match_value TEXT    NOT NULL,
    id_value    TEXT,
    payload     TEXT    NOT NULL,
    diff        TEXT    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    decided_at  TEXT
);
CREATE TABLE IF NOT EXISTS blacklist (
    job         TEXT NOT NULL,
    match_value TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT 'deleted',
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (job, match_value)
);
CREATE TABLE IF NOT EXISTS os_fired (
    job        TEXT NOT NULL,
    minute_key TEXT NOT NULL,
    PRIMARY KEY (job, minute_key)
);
CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL,
    job        TEXT    NOT NULL,
    status     TEXT    NOT NULL,
    recipients TEXT    NOT NULL DEFAULT '',
    subject    TEXT    NOT NULL DEFAULT '',
    body_html  TEXT    NOT NULL DEFAULT '',
    reason     TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS notifications_run ON notifications(run_id);
"""


class State:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self._tx_depth = 0
        self.conn.executescript(_SCHEMA)
        self._commit()

    def close(self) -> None:
        self.conn.close()

    # -- transactions ------------------------------------------------------

    def _commit(self) -> None:
        """Commit unless a `transaction()` block owns the commit.

        Every write helper commits on its own, which is right for one isolated
        write but means **one fsync per row** in a bulk loop: tracking 1569 rows
        took ~6 s on ext4, ~0.01 s inside one transaction. Loops on the run path
        wrap themselves in `transaction()` so the commit happens once.
        """
        if self._tx_depth == 0:
            self.conn.commit()

    @contextmanager
    def transaction(self):
        """Group many writes into a single commit (re-entrant).

        Durability is unchanged — this is one synchronous transaction instead of
        N — so a crash rolls back the whole batch rather than part of it.
        """
        self._tx_depth += 1
        try:
            yield self.conn
        except Exception:
            self._tx_depth -= 1
            if self._tx_depth == 0:
                self.conn.rollback()
            raise
        self._tx_depth -= 1
        if self._tx_depth == 0:
            self.conn.commit()

    # -- runs --------------------------------------------------------------

    def start_run(self, *, job: str, dry_run: bool) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (job, dry_run) VALUES (?, ?)", (job, int(bool(dry_run)))
        )
        self._commit()
        return cur.lastrowid

    def finish_run(self, run_id: int, *, status: str, counts: dict) -> None:
        self.conn.execute(
            "UPDATE runs SET status = ?, counts = ?, finished_at = datetime('now') WHERE id = ?",
            (status, json.dumps(counts, sort_keys=True), run_id),
        )
        self._commit()

    @staticmethod
    def _run_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["dry_run"] = bool(d["dry_run"])
        d["counts"] = json.loads(d["counts"]) if d["counts"] else {}
        return d

    def get_run(self, run_id: int) -> dict | None:
        row = self.conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_row(row) if row else None

    def recover_orphan_runs(self) -> int:
        """Mark runs left unfinished by a crash/exit as ``interrupted``.

        A run row is written at start and finished on every terminal path, so a
        NULL status can only mean the process died mid-run (or was killed). The
        app calls this once at startup so the run log never shows a perpetually
        running row. Returns the number of rows recovered.
        """
        cur = self.conn.execute(
            "UPDATE runs SET status = 'interrupted', "
            "counts = COALESCE(counts, '{}'), finished_at = datetime('now') "
            "WHERE status IS NULL"
        )
        self._commit()
        return cur.rowcount

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
        self._commit()

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
        self._commit()

    def untrack_row(self, *, job: str, match_value) -> None:
        self.conn.execute(
            "DELETE FROM tracked WHERE job = ? AND match_value = ?",
            (job, str(match_value)),
        )
        self._commit()

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

    # -- proposals ---------------------------------------------------------

    @staticmethod
    def _proposal_row(row: sqlite3.Row) -> dict:
        d = dict(row)
        d["payload"] = json.loads(d["payload"])
        d["diff"] = json.loads(d["diff"])
        return d

    def record_proposal(
        self,
        run_id: int,
        *,
        job: str,
        match_value,
        id_value,
        payload: dict,
        diff: list,
    ) -> int:
        """Record a pending update proposal (an `update` action's diff).

        A newer proposal for a key **supersedes** any still-pending proposal for
        the same `(job, match_value)`: the previous row is marked `superseded`
        before the fresh one is inserted, so the review queue holds exactly one
        pending proposal per key (the freshest) instead of one per run. Decided
        rows (`applied`/`stale`/`dismissed`) are left untouched.
        """
        self.conn.execute(
            "UPDATE proposals SET status = 'superseded', decided_at = datetime('now') "
            "WHERE job = ? AND match_value = ? AND status = 'pending'",
            (job, str(match_value)),
        )
        cur = self.conn.execute(
            "INSERT INTO proposals (run_id, job, match_value, id_value, payload, diff) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                run_id,
                job,
                str(match_value),
                "" if id_value is None else str(id_value),
                json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str),
                json.dumps(diff, ensure_ascii=False, default=str),
            ),
        )
        self._commit()
        return cur.lastrowid

    def pending_proposals(self, job: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM proposals WHERE job = ? AND status = 'pending' ORDER BY id",
            (job,),
        ).fetchall()
        return [self._proposal_row(r) for r in rows]

    def proposals_for_run(self, run_id: int) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM proposals WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [self._proposal_row(r) for r in rows]

    def set_proposal_status(self, proposal_id: int, status: str) -> None:
        self.conn.execute(
            "UPDATE proposals SET status = ?, decided_at = datetime('now') WHERE id = ?",
            (status, proposal_id),
        )
        self._commit()

    def get_proposal(self, proposal_id: int) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        return self._proposal_row(row) if row else None

    # -- blacklist ---------------------------------------------------------

    def blacklist_key(self, job: str, match_value, *, reason: str = "manual") -> None:
        # Re-adding an existing key preserves its original `added_at` and never
        # downgrades a manual entry to an auto one (manual provenance wins).
        self.conn.execute(
            "INSERT INTO blacklist (job, match_value, reason, added_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(job, match_value) DO UPDATE SET "
            "reason = CASE WHEN blacklist.reason = 'manual' "
            "              THEN 'manual' ELSE excluded.reason END",
            (job, str(match_value), reason),
        )
        self._commit()

    def unblacklist_key(
        self, job: str, match_value, *, forget_tracking: bool = True
    ) -> None:
        """Remove a key from the blacklist.

        `forget_tracking` (default, the operator path) also drops the `tracked`
        record: reconcile auto-detects deletions as `tracked - store_keys`, so a
        key left tracked would be re-blacklisted by the very next run and the
        operator's "re-sync this" decision would never stick. Auto-clear from
        `reconcile_blacklist` passes `False` — it is not an operator decision,
        and keeping the tracking record means a second deletion is still seen.
        """
        self.conn.execute(
            "DELETE FROM blacklist WHERE job = ? AND match_value = ?",
            (job, str(match_value)),
        )
        if forget_tracking:
            self.conn.execute(
                "DELETE FROM tracked WHERE job = ? AND match_value = ?",
                (job, str(match_value)),
            )
        self._commit()

    def blacklisted_keys(self, job: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT match_value FROM blacklist WHERE job = ?", (job,)
        ).fetchall()
        return {r["match_value"] for r in rows}

    def list_blacklist(self, job: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT match_value, reason, added_at FROM blacklist "
            "WHERE job = ? ORDER BY match_value",
            (job,),
        ).fetchall()
        return [dict(r) for r in rows]

    def reconcile_blacklist(
        self, job: str, store_keys: set[str]
    ) -> tuple[set[str], set[str]]:
        """Auto-blacklist tracked keys gone from the store; auto-clear reappeared.

        `added = tracked - store_keys` (a key the system wrote, now deleted by
        hand); `removed = blacklisted & store_keys` (a suppressed key re-added by
        hand). The two sets are disjoint, so write order is irrelevant.

        Auto-clear keeps the `tracked` record (`forget_tracking=False`): it is not
        an operator decision, so a second hand-deletion of the same key is still
        detected. Only `unblacklist_key` from the GUI/CLI forgets tracking, which
        is what makes "remove from blacklist" mean "re-sync this key".

        An **empty** `store_keys` means the snapshot is *unknown* (an empty or
        malformed `GetStore` response, or a store lacking the match column), not
        a mass deletion — a bad read must never blacklist every tracked key, so
        this is a no-op. Auto-clear is likewise skipped (nothing to compare).
        """
        store_keys = {str(k) for k in store_keys}
        if not store_keys:
            return set(), set()
        tracked = self.tracked_keys(job)
        blacklisted = self.blacklisted_keys(job)
        added = tracked - store_keys
        removed = blacklisted & store_keys
        for key in added:
            self.blacklist_key(job, key, reason="deleted")
        for key in removed:
            self.unblacklist_key(job, key, forget_tracking=False)
        return added, removed

    # -- OS due marker -----------------------------------------------------

    def claim_due(self, job: str, minute_key: str) -> bool:
        """Claim `(job, minute_key)`; True only for the first caller.

        Guards a GUI fire and an OS fire in the same wall-clock minute against
        running a job twice. `minute_key` is `now.strftime("%Y-%m-%dT%H:%M")`.
        """
        cur = self.conn.execute(
            "INSERT INTO os_fired (job, minute_key) VALUES (?, ?) "
            "ON CONFLICT(job, minute_key) DO NOTHING",
            (job, minute_key),
        )
        self._commit()
        return cur.rowcount == 1

    def prune_os_fired(self, keep_after: str) -> int:
        """Delete markers strictly older than `keep_after`; return the count."""
        cur = self.conn.execute(
            "DELETE FROM os_fired WHERE minute_key < ?", (keep_after,)
        )
        self._commit()
        return cur.rowcount

    # -- notifications -----------------------------------------------------

    def record_notification(
        self,
        run_id: int,
        *,
        job: str,
        status: str,
        recipients: str = "",
        subject: str = "",
        body_html: str = "",
        reason: str | None = None,
    ) -> int:
        """Record one notification attempt for a run; returns its row id.

        Written for **every** attempt — sent, skipped, failed or dry_run — so the
        GUI and an operator can see what happened (and why nothing was mailed).
        """
        cur = self.conn.execute(
            "INSERT INTO notifications "
            "(run_id, job, status, recipients, subject, body_html, reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (run_id, job, status, recipients, subject, body_html, reason),
        )
        self._commit()
        return cur.lastrowid

    def notification_for_run(self, run_id: int) -> dict | None:
        """The most recent notification for a run, or None when none exists."""
        row = self.conn.execute(
            "SELECT * FROM notifications WHERE run_id = ? ORDER BY id DESC LIMIT 1",
            (run_id,),
        ).fetchone()
        return dict(row) if row else None

    def notifications_for_run(self, run_id: int) -> list[dict]:
        """Every notification row for a run, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM notifications WHERE run_id = ? ORDER BY id", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]
