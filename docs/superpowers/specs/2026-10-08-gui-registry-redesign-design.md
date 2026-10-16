# STG Store Sync — Store Registry + GUI Redesign (momo-style)

> **Status:** Draft for review
> **Date:** 2026-10-08
> **Repo:** `stg-store-sync`
> **Supersedes (in part):** §7.4 GUI / single-job form of
> `2026-10-08-stg-store-sync-design.md`. All domain rules in that spec
> (pull, projection, planning, fail-closed, empty-is-a-value) remain authoritative.
> **Author:** design session

---

## 1. Problem

The first cut exposes **one generic job form**: the operator types a store id, a
survey id, a match var, and fills a column grid, then presses Sync. That is not
how the tool will actually be used.

Reality: the operator maintains **many stores**, each with its **own** source
survey, its **own** id/match columns, and its **own** schedule. They want to
**register** those stores once, see them in a list with live status, run any of
them (or all of them) on demand, run scheduled ones automatically, and **stop**
a run that is in flight.

This is the same shape as the existing `momo` tool in `stg_automation_suite`
(dashboard → registered entities → run → monitor → stop). We adopt that pattern.

### 1.1 What changes

| Today | After |
|---|---|
| One in-memory `JobConfig` from a form | A **registry** of N registered stores (persisted) |
| Sync button only | **Run now** per store + **Run due** (scheduler) + **Stop** |
| Flat window, 5 tabs | **Dashboard → Store detail → Run detail** (stacked nav) |
| No history surfaced | Per-store **run history** + global running/pending strip |
| Columns grid is a detached `QTableWidget` | **`QTableView` bound to `JobTableModel`** (edits persist) |

### 1.2 Non-goals

- No multi-user / server deployment. Single-operator desktop tool.
- No changes to the pull → plan → write **domain semantics** (that spec stands).
- No new STG API calls beyond what §2.1 of the base spec already verified.
- No distributed scheduler; the scheduler runs in-process while the GUI is open.

---

## 2. Intent (design brief)

**For:** the survey-ops operator who owns a set of STG Subject Stores.
**Outcome:** register each store once; run it manually or on a minute-granular
schedule; watch status and history; stop a running sync.
**Success:** an operator can add store #2..#N without editing code or YAML,
trigger a run with one click, see it complete or fail with counts, and cancel a
run without leaving orphaned threads or half-written-store surprises.

**Constraints (agreed in this session):**

1. Full momo-style navigation (dashboard → store → run).
2. In-app scheduler, **per-list interval schedules** (minute/hour/day/week —
   superseded from cron/minute granularity; see
   `2026-10-08-registration-and-schedule-redesign-design.md`).
3. **Single global run queue** — one store syncs at a time; others wait. Stop
   cancels the active run and marks it `cancelled`.
4. Registry persisted in **SQLite**, extending the existing `state.db`.
5. **Correctness fixes first**, then the GUI redesign on top.

---

## 3. Core correctness fixes (land before the GUI)

These come from the branch review (`25e61d6..cfb364b`). They are prerequisites:
the new GUI multiplies the number of configured stores, so a payload bug that
resets unmapped columns is now a fleet-wide risk.

### 3.1 A1 — Writes must send the store's **full** column set (Critical)

**Bug.** `planner._payload` (add) and the update branch build the payload from
the configured columns + match + id **only**. STG `AddRow`/`UpdateRow` **reset
any omitted column to its default** (probe F2/F8). So an unmapped store column
(e.g. `No` on store 17417) is wiped on every write.

**Fix.**
- `plan(...)` gains `store_columns: list[str]` (the store's full column set,
  read from the live store snapshot).
- **ADD** → payload contains **every** `store_columns` entry: mapped → projected
  value, unmapped → `""`.
- **UPDATE** → start from a copy of the **existing store row**, then overlay the
  projected mapped columns and the match value. Unmapped columns keep their
  current store value. The id value is preserved.
- The id/tag value remains assigned by the planner (`next_tag` for adds, the
  existing value for updates) and is never rewritten to a different value.

**Regression test:** store with one unmapped column beyond the configured ones;
assert the add payload has the col with `""`, and the update payload retains the
store's existing value for that col.

### 3.2 A2 — Source-side duplicates skip rows; only store-side dup halts (Important)

**Bug.** `Plan.blocked` is `True` for *any* conflict action, so a duplicate match
key **in the source** halts the whole job. Spec §4.4 says source-side duplicates
are **skipped** (rows left untouched), and only a **store**-side duplicate is a
hard halt.

**Fix.**
- `Action` records a `side` (`"store"` | `"source"`) for conflicts.
- `Plan.blocked` is `True` iff there is a **store-side** conflict.
- Source-side duplicate keys: rows are excluded from add/update (already the
  behaviour via `src_dups`), the run continues, and the keys are reported as
  skipped conflicts → run status `partial` when there are applied writes.

### 3.3 A3 — Readiness gates every write path (Important)

**Bug.** `run_sync` never ran a readiness check. It wrote even when the id column
was missing/non-unique or the match key was non-unique in the store.

**Fix (landed; later simplified 2026-10-15).**
- `_readiness_errors(config, store_rows, source_records)` evaluates the full
  preflight error set inside `run_sync`, reusing the already-fetched store
  snapshot and pulled records (no double-fetch).
- `run_sync` runs readiness **before** any write. `report.ok == False` → status
  `blocked`, zero writes, errors recorded.
- CLI runs the same gate; a `blocked` job exits non-zero.
- There is no separate GUI preflight action — the GUI uses the same in-run gate,
  and dry-run is the way to preview. The id-column no-op probe (§4.2.1) runs on a
  live run with adds.

### 3.4 A4 — ~~Honor `source.window_days` / `date_type`~~ (SUPERSEDED)

**Superseded** by
`docs/superpowers/specs/2026-10-08-registration-and-schedule-redesign-design.md`:
registration carries no time configuration — `SourceConfig` is
`{ survey_id }` only and every sync pulls full history.
(`window_days == 0` meant "all history" — that is now the only behavior.)

### 3.5 A6 — Transport errors are retried/translated; run row always finished (Important)

**Bug.** `writer._retry` catches only `STGError`; a `requests.ConnectionError` /
`Timeout` propagates and leaves the `runs` row with a null status.

**Fix.**
- `_retry` also catches the transport errors (`ConnectionError`, `Timeout`,
  `ChunkedEncodingError`) alongside `STGError`.
- `run_sync` wraps the apply step in `try/finally` so `state.finish_run(...)` is
  guaranteed (status `error` with the transport message), no null status left.

### 3.6 Nits folded in

- `writer.apply_plan` must not fall back to a hardcoded `id_col="No"`; take the
  id column from the config or skip verification with an explicit error.
- CLI exit code: `blocked` → non-zero.
- Empty store: the readiness gate reports "no rows to match" as an explicit state
  rather than failing opaquely.
- `mapping_io`: half-filled rows are reported as errors, not silently skipped.
- Delete the dead hidden `QTableWidget` in the binding tab.
- Planner: a store row missing the match column is reported, not silently dropped.

---

## 4. Registry (persistence) — schema block superseded

> **Schema + API below superseded in part** by
> `docs/superpowers/specs/2026-10-08-registration-and-schedule-redesign-design.md`:
> `window_days`/`date_type` columns are dropped (registration carries no time
> config); `schedule TEXT` (cron) is replaced by the 4 structured columns
> `schedule_unit`/`schedule_every`/`schedule_at`/`schedule_weekday`; API
> signatures are `Schedule`-typed, not cron strings. Kept for history.

Same SQLite file as `State` (`~/.stg_store_sync/state.db`). New tables, managed
by a new `core/registry.py: Registry` (separate class, same file; `State` keeps
owning `runs`/`conflicts`/`tracked`).

```sql
CREATE TABLE IF NOT EXISTS jobs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL UNIQUE,       -- operator-facing label
    store_id       INTEGER NOT NULL,
    id_column      TEXT NOT NULL,
    survey_id      TEXT NOT NULL,
    match_source_var TEXT NOT NULL,
    match_store_col  TEXT NOT NULL,
    window_days    INTEGER NOT NULL DEFAULT 0,
    date_type      TEXT NOT NULL DEFAULT 'Upload',
    delete_policy  TEXT NOT NULL DEFAULT 'full_history',
    dry_run        INTEGER NOT NULL DEFAULT 1,
    enabled        INTEGER NOT NULL DEFAULT 0,
    schedule       TEXT NOT NULL DEFAULT '',   -- cron, minute granularity; '' = manual only
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
```

**API (`Registry`).**

| Method | Purpose |
|---|---|
| `add(job: JobConfig, *, schedule: Schedule, enabled: bool) -> int` | insert + columns (transactional) |
| `update(name, job, *, schedule, enabled)` | replace config + columns |
| `delete(name)` | remove job + columns (keep run history) |
| `get(name) -> JobConfig` (+ schedule/enabled) | reconstruct |
| `list() -> list[JobEntry]` | dashboard rows (incl. last run join) |
| `names()` | CLI enumerate |
| `set_enabled(name, bool)` / `set_schedule(name, schedule)` | quick toggles |

(`schedule` is the `Schedule{unit, every, at, weekday}` value of the redesign
spec — `set_schedule(name, cron)` above was the pre-redesign cron form, kept for
history.)

`JobEntry` = config summary + `enabled` + `schedule` + last run (`status`,
`finished_at`, `counts`) joined from `runs` by `run.job = jobs.name`.

`JobConfig` stays the runtime contract — the registry only stores and
reconstructs it, so the core pipeline is unchanged in shape.

---

## 5. Scheduler + Run Queue

### 5.1 Run queue (`core/run_queue.py`)

One worker thread, FIFO, **exactly one active run**.

```
class RunQueue:
    enqueue(name, *, reason)      # reason: "manual" | "schedule" | "cli"
    stop_current()                # request cancel of the active run
    stop_all()                    # cancel active + clear pending
    subscribe(cb)                 # notified on queue/state changes
    snapshot() -> QueueState      # {"active": name|None, "pending": [names]}
```

- A job already active or pending is **not** enqueued twice (dedupe).
- The worker builds the `STGClient` + `JobConfig` from the registry and calls
  `run_sync(..., should_stop=...)`.

### 5.2 Cancellable run (`run_sync(..., should_stop=None)`)

- `should_stop` is a zero-arg callable checked:
  - between export chunks (pass a progress hook into `simple_export`), and
  - before each write action in `apply_plan`.
- On cancel: stop issuing writes, `finish_run(status="cancelled", counts=<partial>)`,
  return `SyncResult(status="cancelled")`. **Writes already applied stay applied**
  (and stay tracked); the store is never left in an unknown state — it is a
  point-in-time partial, reported as such.

### 5.3 Scheduler (`core/scheduler.py`) — superseded (interval model)

> **Superseded** by
> `docs/superpowers/specs/2026-10-08-registration-and-schedule-redesign-design.md`
> §4: schedules are structured `Schedule{unit, every, at, weekday}` values
> (`unit` ""/minute/hour/day/week), stored as 4 columns
> (`schedule_unit`/`schedule_every`/`schedule_at`/`schedule_weekday`), not cron
> strings. Legacy cron jobs migrate to manual (`unit=''`).

- Tick loop (default 30 s) evaluating each **enabled** job's `Schedule` via
  `is_due(schedule, now)`; unit `''` = manual only, never auto-fires.
- Due job → `RunQueue.enqueue(name, reason="schedule")` (dedupe prevents overlap).
- No overlap: a job still active/pending is skipped this tick.
- Manual runs are unaffected by `enabled`; `enabled` gates only the scheduler.

---

## 6. GUI (momo-style)

`QMainWindow` + `QStackedWidget`: **Dashboard → Store detail → Run detail**, with
a breadcrumb header. Shared header keeps the gradient + status dot; console log
stays the dark terminal style (DESIGN.md tokens).

```
ui/
  main_window.py     # QMainWindow, stacked nav, header/breadcrumb, closeEvent cleanup
  dashboard.py       # stat cards + store table + queue strip + toolbar
  job_detail.py      # tabs: Config · Columns · Schedule · History · Conflicts
  run_detail.py      # one run: status, counts, errors, conflicts, log
  job_table_model.py # JobTableModel (moved out of main_window; QTableView-bound)
  run_thread.py      # QThread: run_sync + stop Event; emits progress/finished
  scheduler_thread.py# QThread: scheduler tick; emits due(name)
  dialogs.py         # New/Edit store dialog
gui.py               # Controller: owns Registry + RunQueue + threads; wiring
```

### 6.1 Dashboard

- **Stat cards:** stores total · enabled · running/pending · last-24h failed.
- **Queue strip:** `▶ running: <name>` and `… pending: a, b` (live).
- **Store table** (`QTableView` over a model):

  | Name | Store | Survey | Schedule | Enabled | Last status | Last run | Actions |
  |---|---|---|---|---|---|---|---|

- **Toolbar:** ➕ New · ▶ Run now · ⏹ Stop · ✏ Edit · 🗑 Delete · ⏰ Schedules · 🔄 Refresh.
- Double-click a row → Store detail.

### 6.2 Store detail (`job_detail`)

- **Config:** store id, id col, survey id, match vars, delete policy,
  dry-run.
- **Columns:** `QTableView` bound to `JobTableModel`. Columns: *Store column* ·
  *Source variable* · *Pull term (read-only)* · *Kind*. **Edits go through the
  model** (fix #5). Import/Export `.xlsx` buttons wired to `mapping_io`.
- **Schedule:** enabled checkbox + unit combo (""/minute/hour/day/week) + every
  spin + at/weekday pickers (dynamic visibility) with a human preview
  (`Schedule.human()`).
- **History:** runs for this store (from `State.recent_runs`), double-click → Run
  detail.
- **Conflicts:** latest conflicts for this store.
- Buttons: **Save** · **Run now** · **Stop**. Enabling a job is the Enabled
  checkbox + Save — there is no separate "Approve binding" step, and no separate
  "Run preflight" step (readiness is verified inside every run; dry-run previews).

### 6.3 Run detail

- Status, counts, errors, conflicts, and the captured log for one run id.

### 6.4 Threads & resource hygiene (AGENTS §3.4)

- `RunThread` (QThread) wraps `run_sync`; `stop()` sets the stop `Event` that
  `should_stop` reads. Emits `progress`, `finished(SyncResult)`.
- `SchedulerThread` wraps the tick loop; emits `due(name)`.
- `closeEvent`: `stop_all()`, `quit()` + `wait(timeout)` on both threads, close
  `State`/`Registry` connections, unregister anything from `atexit`.
- No fire-and-forget threads; all are `daemon` or explicitly joined.

---

## 7. CLI

Registry-backed, headless (for external cron / CI):

```
stg-store-sync-cli list
stg-store-sync-cli run <name> [--dry-run/--no-dry-run]
stg-store-sync-cli run-all [--enabled-only]
stg-store-sync-cli stop <name>
```

Exit codes: `0` ok · `2` blocked (readiness gate) · `3` partial/error · `1` usage.
`run` performs the §3.3 readiness check first.

---

## 8. Testing strategy

TDD throughout; the existing suite (182) must stay green.

- **Core fixes (§3):** unit tests with fixtures that include an **unmapped store
  column** (the current fixtures mask A1). Duplicate-origin tests (source vs
  store) for A2. `should_stop` cancellation test that asserts `cancelled` status
  and that no write after the stop point is issued.
- **Registry (§4):** CRUD round-trip → `JobConfig` equivalence; unique name;
  cascade delete of columns; last-run join.
- **Queue/scheduler (§5):** dedupe; single-active invariant; interval "due"
  matching (`is_due(Schedule, now)`); no overlap while active.
- **GUI (§6):** offscreen `QApplication`. Assert `QTableView` edits reach the
  model (regression for #5); dashboard row → detail navigation; Stop sets the
  Event; `closeEvent` stops threads (no `QThread: Destroyed while running`).
- **CLI (§7):** exit codes; `blocked` → 2.

---

## 9. Rollout / sequencing

1. **A — core fixes** (§3): A1, A2, A3, A4, A6 + nits. Suite green.
2. **B — registry** (§4): `Registry` + tests.
3. **C — queue + scheduler** (§5): `should_stop`, `RunQueue`, `Scheduler`.
4. **D — GUI** (§6): models → dashboard → detail → run detail → threads →
   `gui.py` wiring.
5. **E — CLI** (§7): registry-backed commands + exit codes.

Each step commits separately; re-run `./.venv/bin/python -m pytest -q` before
each commit. After E, a controlled real write / adoption for store 17417 (adding
an `SbjNum` column in STG Studio first) validates the end-to-end path.

---

## 10. Deferred (not blockers)

- **Cron format — superseded:** no cron string. Schedules are the structured
  interval + anchor model of
  `docs/superpowers/specs/2026-10-08-registration-and-schedule-redesign-design.md`
  (`Schedule{unit, every, at, weekday}`, unit ""/minute/hour/day/week).
- **Store 17417 adoption (deferred):** add `SbjNum` to the store so identity
  becomes per-interview (currently `ids`/`uniqueID`). Adoption is a documented
  manual STG Studio step for now; a GUI action is a possible follow-up, not in
  this scope.
