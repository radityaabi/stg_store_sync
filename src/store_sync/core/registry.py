"""Registry — persisted store configurations (spec §4).

Same SQLite file as :class:`store_sync.core.state.State` (separate tables,
separate connection). ``State`` keeps owning ``runs``/``conflicts``/``tracked``;
this class owns ``jobs``/``job_columns`` — the *config* half of a job. Run
history is read-only joined for the dashboard's :class:`JobEntry` rows.

``JobConfig`` stays the runtime contract: :meth:`Registry.get` reconstructs one,
:meth:`Registry.add`/:meth:`Registry.update` persist one. The §4 ``jobs`` schema
stores a subset of ``JobConfig`` (no ``binding.mode``, ``write.op`` or
``write.normalize``); those come back as their dataclass defaults.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from store_sync.core.config import (
    BindingConfig,
    ColumnBinding,
    JobConfig,
    Schedule,
    SourceConfig,
    StoreConfig,
    WriteConfig,
)

_SCHEDULE_COLUMNS = (
    # (column, DDL) — the structured per-list schedule (spec §4.5).
    ("schedule_unit", "TEXT NOT NULL DEFAULT ''"),
    ("schedule_every", "INTEGER NOT NULL DEFAULT 1"),
    ("schedule_at", "TEXT NOT NULL DEFAULT ''"),
    ("schedule_weekday", "INTEGER NOT NULL DEFAULT 0"),
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,
    store_id       INTEGER NOT NULL,
    id_column      TEXT NOT NULL,
    survey_id      TEXT NOT NULL,
    match_source_var TEXT NOT NULL,
    match_store_col  TEXT NOT NULL,
    delete_policy  TEXT NOT NULL DEFAULT 'full_history',
    dry_run        INTEGER NOT NULL DEFAULT 1,
    enabled        INTEGER NOT NULL DEFAULT 0,
    schedule_unit    TEXT NOT NULL DEFAULT '',
    schedule_every   INTEGER NOT NULL DEFAULT 1,
    schedule_at      TEXT NOT NULL DEFAULT '',
    schedule_weekday INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS job_columns (
    job_id     INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
    store_col  TEXT NOT NULL,
    source_var TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'text',
    ord        INTEGER NOT NULL,
    PRIMARY KEY (job_id, ord)
);
"""


@dataclass
class JobEntry:
    """Dashboard row: config summary + enable/schedule + last run."""

    name: str
    store_id: int
    id_column: str
    survey_id: str
    match_source_var: str
    match_store_col: str
    delete_policy: str
    dry_run: bool
    enabled: bool
    schedule: Schedule
    column_count: int
    last_status: str | None
    last_finished_at: str | None


_ENTRY_FIELDS = """
SELECT jobs.name, jobs.store_id, jobs.id_column, jobs.survey_id,
       jobs.match_source_var, jobs.match_store_col, jobs.delete_policy,
       jobs.dry_run, jobs.enabled,
       jobs.schedule_unit, jobs.schedule_every, jobs.schedule_at,
       jobs.schedule_weekday,
       (SELECT COUNT(*) FROM job_columns c WHERE c.job_id = jobs.id) AS column_count,
"""

_LAST_RUN_JOIN = """
LEFT JOIN runs ON runs.id = (
    SELECT r.id FROM runs r
    WHERE r.job = jobs.name
    ORDER BY r.id DESC LIMIT 1
)
"""


class Registry:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(_SCHEMA)
        self._add_schedule_columns()
        self._drop_legacy_columns(("window_days", "date_type", "schedule"))
        self.conn.commit()

    def _add_schedule_columns(self) -> None:
        """Add the structured schedule columns when an older DB lacks them.

        A legacy cron `schedule` TEXT column is dropped separately (below); a
        cron job cannot be losslessly mapped to the interval model, so it
        migrates to `unit=''` (manual only) — deliberate, documented (§4.5).
        """
        present = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(jobs)")
        }
        for col, ddl in _SCHEDULE_COLUMNS:
            if col not in present:
                self.conn.execute(f"ALTER TABLE jobs ADD COLUMN {col} {ddl}")

    def _drop_legacy_columns(self, columns: tuple[str, ...]) -> None:
        """Drop legacy `jobs` columns when present (SQLite ≥ 3.35)."""
        present = {
            row["name"]
            for row in self.conn.execute("PRAGMA table_info(jobs)")
        }
        for col in columns:
            if col in present:
                self.conn.execute(f"ALTER TABLE jobs DROP COLUMN {col}")

    def close(self) -> None:
        self.conn.close()

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _config_values(job: JobConfig) -> tuple:
        match = job.binding.match
        return (
            job.job,
            int(job.store.id),
            str(job.store.id_column),
            str(job.source.survey_id),
            str(match.source_var) if match else "",
            str(match.store_col) if match else "",
            str(job.write.delete_policy),
            int(bool(job.write.dry_run)),
        )

    def _replace_columns(self, job_id: int, job: JobConfig) -> None:
        self.conn.execute("DELETE FROM job_columns WHERE job_id = ?", (job_id,))
        for ord_, col in enumerate(job.binding.columns):
            self.conn.execute(
                "INSERT INTO job_columns (job_id, store_col, source_var, kind, ord) "
                "VALUES (?, ?, ?, ?, ?)",
                (job_id, str(col.store_col), str(col.source_var),
                 str(col.kind), ord_),
            )

    def _job_id(self, name: str) -> int:
        row = self.conn.execute(
            "SELECT id FROM jobs WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such job: {name!r}")
        return row["id"]

    @staticmethod
    def _schedule_values(schedule: Schedule) -> tuple[str, int, str, int]:
        """Flatten a `Schedule` into the four stored columns (spec §4.5)."""
        return (schedule.unit, int(schedule.every), schedule.at,
                int(schedule.weekday))

    @staticmethod
    def _entry(row: sqlite3.Row) -> JobEntry:
        # Built with the dataclass constructor (DB columns are typed), not
        # `from_dict` — a bad stored row must still yield a Schedule, never
        # crash `list_entries`.
        try:
            schedule = Schedule(
                unit=row["schedule_unit"],
                every=row["schedule_every"],
                at=row["schedule_at"],
                weekday=row["schedule_weekday"],
            )
        except (TypeError, ValueError):
            schedule = Schedule()
        return JobEntry(
            name=row["name"],
            store_id=row["store_id"],
            id_column=row["id_column"],
            survey_id=row["survey_id"],
            match_source_var=row["match_source_var"],
            match_store_col=row["match_store_col"],
            delete_policy=row["delete_policy"],
            dry_run=bool(row["dry_run"]),
            enabled=bool(row["enabled"]),
            schedule=schedule,
            column_count=row["column_count"],
            last_status=row["last_status"],
            last_finished_at=row["last_finished_at"],
        )

    def _has_runs(self) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'runs'"
        ).fetchone()
        return row is not None

    def _entry_select(self) -> str:
        """`runs` is owned by `State`; join only when it exists on this file."""
        if self._has_runs():
            return (
                _ENTRY_FIELDS
                + "runs.status AS last_status, runs.finished_at AS last_finished_at\n"
                + "FROM jobs\n" + _LAST_RUN_JOIN
            )
        return (
            _ENTRY_FIELDS
            + "NULL AS last_status, NULL AS last_finished_at\nFROM jobs\n"
        )

    # -- CRUD --------------------------------------------------------------

    def add(self, job: JobConfig, *, schedule: Schedule = Schedule(),
            enabled: bool = False) -> int:
        """Insert a new job and its columns in one transaction. Returns job id."""
        values = self._config_values(job)
        try:
            with self.conn:  # transaction: rollback on the UNIQUE violation too
                cur = self.conn.execute(
                    "INSERT INTO jobs (name, store_id, id_column, survey_id, "
                    "match_source_var, match_store_col, "
                    "delete_policy, dry_run, enabled, "
                    "schedule_unit, schedule_every, schedule_at, schedule_weekday) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*values, int(bool(enabled)),
                     *self._schedule_values(schedule)),
                )
                job_id = cur.lastrowid
                self._replace_columns(job_id, job)
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"job already exists: {job.job!r}") from exc
        return job_id

    def update(
        self,
        name: str,
        job: JobConfig,
        *,
        schedule: Schedule | None = None,
        enabled: bool | None = None,
    ) -> None:
        """Replace the stored config + columns; touch only given toggles."""
        job_id = self._job_id(name)
        sets = [
            "store_id = ?", "id_column = ?", "survey_id = ?",
            "match_source_var = ?", "match_store_col = ?",
            "delete_policy = ?", "dry_run = ?", "name = ?",
        ]
        # values order matches `sets`; job name for the WHERE is the last value.
        params = list(self._config_values(job)[1:])  # drop the old name slot
        params.append(job.job)
        if schedule is not None:
            sets.append(
                "schedule_unit = ?, schedule_every = ?, "
                "schedule_at = ?, schedule_weekday = ?"
            )
            params.extend(self._schedule_values(schedule))
        if enabled is not None:
            sets.append("enabled = ?")
            params.append(int(bool(enabled)))
        sets.append("updated_at = datetime('now')")
        params.append(job_id)
        with self.conn:
            self.conn.execute(
                f"UPDATE jobs SET {', '.join(sets)} WHERE id = ?", params
            )
            self._replace_columns(job_id, job)

    def delete(self, name: str) -> None:
        """Remove the job and its columns; run history is kept (not joined)."""
        job_id = self._job_id(name)
        with self.conn:
            self.conn.execute("DELETE FROM job_columns WHERE job_id = ?", (job_id,))
            self.conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))

    def get(self, name: str) -> JobConfig:
        row = self.conn.execute(
            "SELECT * FROM jobs WHERE name = ?", (name,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such job: {name!r}")
        cols = self.conn.execute(
            "SELECT store_col, source_var, kind FROM job_columns "
            "WHERE job_id = ? ORDER BY ord",
            (row["id"],),
        ).fetchall()
        return JobConfig(
            job=row["name"],
            store=StoreConfig(row["store_id"], row["id_column"]),
            source=SourceConfig(row["survey_id"]),
            binding=BindingConfig(
                match=ColumnBinding(row["match_source_var"], row["match_store_col"]),
                columns=[
                    ColumnBinding(c["source_var"], c["store_col"], c["kind"])
                    for c in cols
                ],
            ),
            write=WriteConfig(
                dry_run=bool(row["dry_run"]),
                delete_policy=row["delete_policy"],
            ),
        )

    # -- queries -----------------------------------------------------------

    def get_entry(self, name: str) -> JobEntry:
        row = self.conn.execute(
            self._entry_select() + "WHERE jobs.name = ?", (name,)
        ).fetchone()
        if row is None:
            raise KeyError(f"no such job: {name!r}")
        return self._entry(row)

    def list_entries(self) -> list[JobEntry]:
        rows = self.conn.execute(
            self._entry_select() + "ORDER BY jobs.name"
        ).fetchall()
        return [self._entry(r) for r in rows]

    def names(self) -> list[str]:
        rows = self.conn.execute("SELECT name FROM jobs ORDER BY name").fetchall()
        return [r["name"] for r in rows]

    # -- quick toggles -----------------------------------------------------

    def set_enabled(self, name: str, enabled: bool) -> None:
        job_id = self._job_id(name)
        self.conn.execute(
            "UPDATE jobs SET enabled = ?, updated_at = datetime('now') WHERE id = ?",
            (int(bool(enabled)), job_id),
        )
        self.conn.commit()

    def set_schedule(self, name: str, schedule: Schedule) -> None:
        job_id = self._job_id(name)
        self.conn.execute(
            "UPDATE jobs SET schedule_unit = ?, schedule_every = ?, "
            "schedule_at = ?, schedule_weekday = ?, "
            "updated_at = datetime('now') WHERE id = ?",
            (*self._schedule_values(schedule), job_id),
        )
        self.conn.commit()
