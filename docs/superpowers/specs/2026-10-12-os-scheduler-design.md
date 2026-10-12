# OS-level Scheduler — design

Date: 2026-10-12
Status: approved (design), spec for review
Supersedes: nothing. Extends `2026-10-08-stg-store-sync-design.md` §7 (CLI) and
`2026-10-08-registration-and-schedule-redesign-design.md` §9 (out of scope: the
OS-level scheduler).

## 1. Problem

Scheduling today is **in-app only**. `Scheduler` (`core/scheduler.py`) and
`SchedulerThread` (`ui/scheduler_thread.py`) are constructed and started by
`gui.py` (lines 200, 283-285) and torn down by `Controller.stop()`
(`gui.py:287`). The scheduler therefore runs only while the GUI process is
open. When the app is closed, no run is triggered, and no run is caught up
later.

Observed (2026-10-12, this machine): job `DUMMY_STORE_RED_ECHINO`
(`enabled=1`, `schedule_unit=minute`, `schedule_every=10`) last ran
scheduled at `10:40:49 → 10:53:17` (run id 28); the app was then closed and no
further scheduled run occurred. The only OS-level entry on the machine is
`momo-run-due` for the sibling repo `stg_automation_suite`; there is no
`crontab`, systemd, or autostart entry for this repo.

Operators expect a job to run on its schedule **without the app being open**.

## 2. Goals / Non-Goals

**Goals**

- A job runs on its declared schedule with the GUI closed.
- The declared **per-job** schedule is honoured exactly — the same
  `is_due()` semantics the in-app scheduler uses, with no second, divergent
  schedule model.
- Cross-platform: Windows (the production target) and Linux (this dev
  machine). macOS: explicit "unsupported" message, never a silent no-op.
- Idempotent registration; re-running never duplicates a task.
- Unregistration is clean and preserves unrelated OS entries.
- Headless runs obey existing invariants: **add-only** (invariant 11),
  dry-run default, no delete.
- Registration failures never corrupt state and never block the GUI.

**Non-Goals**

- A distributed scheduler, a queue daemon, or a long-lived background service.
- Changing the schedule model, the GUI scheduler, or `RunQueue`.
- Catch-up of runs missed while the machine was off (see §8).
- Running the GUI automatically at login.
- Multi-user / machine-wide task registration (per-user only; no elevation
  requirement is *sought*).

## 3. Verified Findings

- `Scheduler.tick()` is GUI-independent: it takes a registry-like object and an
  `enqueue` callable, and `is_due(schedule, now)` is a pure function. Both are
  already covered by the existing test suite. Reuse is therefore free of new
  schedule logic.
- `_default_db_path()` / `cli._db_path()` resolve `~/.stg_store_sync/state.db`
  (or `STG_STORE_SYNC_DB`) via `Path.home()` — Windows-safe.
- `filelock.FileLock` and SQLite WAL are Windows-safe. `cli.py` has no
  `subprocess`, `os.fork`, `signal`, or POSIX-only calls.
- The sibling repo `stg_automation_suite` already ships a working
  cross-platform registration pattern in `src/momo/core/cron_setup.py`
  (`crontab` on Linux, `schtasks` on Windows, sentinel-based idempotent
  add/remove). This design mirrors its shape; it is **reference only, never
  imported** (repo rule).

## 4. Architecture

```
                 ┌─────────────────────────────┐
   registry ───▶ │ core/os_scheduler.py        │ ──▶ schtasks / crontab
   (jobs +       │  plan(job) → Trigger        │     (one task per job)
    schedules)   │  add / remove / sync        │
                 └─────────────────────────────┘
                              ▲
        GUI save/delete ──────┤ (best-effort, never blocks)
        CLI `schedule …` ─────┘

   OS task fires ──▶ `stg-store-sync-cli run-due --job <name>`
                        │
                        ▼
                 Scheduler(registry, enqueue=collect).tick()
                        │  (is_due filters the exact schedule)
                        ▼
                 run_sync(job, client, state)   # add-only, dry-run default
```

**Single source of truth.** The OS task is a coarse *wake-up*. The precise
decision — "is this job due right now?" — is always `Scheduler.tick()` /
`is_due()`. The OS task never encodes the interval modulo itself; it only
guarantees a process starts often enough that `is_due()` can evaluate. This
keeps one schedule model.

### 4.1 Components

- **`core/os_scheduler.py`** (new) — pure planning plus thin OS calls.
  - `plan(name, schedule) -> Trigger | None` — maps a `Schedule` to an OS
    trigger; `None` when the job is manual-only (empty/invalid schedule).
  - `add(name, schedule)`, `remove(name)`, `sync(entries)` — idempotent.
  - `status(entries) -> dict[name, str]` — `"registered" | "missing" |
    "stale" | "extra" | "unsupported"` for the GUI badge.
  - All OS interaction goes through module-level helpers
    (`_schtasks_query/_create/_delete`, `_read_crontab/_write_crontab`) so
    tests stub them and never touch the real OS.
- **`cli.py`** — new subcommands (see §5). `run-due` reuses `Scheduler`.
- **`gui.py`** — a "Sync OS schedule" action plus best-effort auto-sync on
  save/delete/enable/disable; a dashboard badge.
- **`state.py`** — a `os_fired(job, minute_key)` marker table (§6).

### 4.2 Schedule → OS trigger mapping

The OS trigger is deliberately coarser than the schedule; `is_due()` filters.

| unit | `every` | anchor | OS trigger | notes |
|---|---|---|---|---|
| `minute` | N (1-59) | — | every N minutes | matches `is_due` exactly |
| `hour` | N (1-23) | — | every N hours at `:00` | matches `is_due` exactly |
| `day` | N (1-31) | `HH:MM` | **daily** at `HH:MM` | `is_due` rejects N-1 of every N days |
| `week` | N (1-52) | `W` `HH:MM` | **weekly** on `W` at `HH:MM` | `is_due` rejects N-1 of every N weeks |

An empty or malformed schedule (unknown unit, `every <= 0`, bad `at`) →
`plan()` returns `None` → no OS task (manual only). This mirrors `is_due`,
which never fires on a malformed schedule.

**Anchor alignment (required for correctness).** `is_due` tests `now.minute %
every` / `now.hour % every`, which are anchored to the clock (minute 0, hour
0). The OS trigger must be anchored identically or `is_due` will reject most
wakes. Concretely:

- `schtasks /sc minute /mo N /st 00:00` (momo uses exactly this) and
  `crontab */N * * * *` both fire at minutes 0, N, 2N… → aligned with
  `now.minute % N == 0`.
- `schtasks /sc hourly /mo N /st 00:00` and `crontab 0 */N * * *` fire at
  hours 0, N, 2N… at minute 0 → aligned with `now.hour % N == 0`.
- `day` / `week` use the literal `HH:MM` / weekday anchor, matching `is_due`.

Without the `00:00` anchor, `minute`/`hour` triggers would drift off the
`is_due` phase and silently never fire.

### 4.3 Names and sentinels

- Windows task name: `StgStoreSync_<sanitised-job>` (non-alphanumerics → `_`,
  truncated to keep within `schtasks` limits).
- Linux crontab sentinel comment: `# stg-store-sync:<job>` on its own line,
  immediately followed by the command line — so removal can find and delete
  exactly that block, preserving unrelated entries.

### 4.4 Command line

The registered command is the CLI entry, invoked with the project's Python:

```
<venv-python> -m store_sync.cli run-due --job <name>
```

- On Windows, prefer `pythonw.exe` so no console window flashes each minute
  (mirrors `momo`).
- The command must not depend on a working directory: the job's config lives
  in the registry DB, not the CWD.
- **Credentials (verified finding).** `cli.main` calls `load_dotenv()`, which
  uses `find_dotenv()` — a *CWD-dependent* upward search. An OS task starts in
  an arbitrary CWD (Windows Task Scheduler defaults to `%SystemRoot%\System32`;
  `cron` starts in `$HOME`), so `find_dotenv()` finds nothing and no credentials
  load. Verified on this machine: from a foreign CWD, `find_dotenv()` returns
  `""` and `STG_SERVER`/`STG_USER`/`STG_PASSWORD` are unset.

  **Decision.** `run-due` resolves `.env` **independently of CWD**: it loads
  `<project-root>/.env` explicitly, where the project root is derived from the
  package location (`Path(store_sync.__file__).resolve().parents[2]`), then
  falls back to `load_dotenv()` for interactive use. Environment variables
  already present (e.g. set by the task definition) always win over the file.
  A missing credentials set is a hard error in `run-due` (exit `1`, clear
  message) — a scheduled run cannot prompt.

## 5. CLI surface

```
stg-store-sync-cli run-due [--job NAME] [--json]
stg-store-sync-cli schedule list
stg-store-sync-cli schedule add <name>
stg-store-sync-cli schedule remove <name>
stg-store-sync-cli schedule sync
```

- **`run-due [--job NAME]`** — build `Scheduler(registry, enqueue=collect)`,
  call `tick()`, then run every collected name via the existing `_run_one`
  path (add-only, per-job file lock). It passes `dry_run=None`, so each job's
  **configured** `write.dry_run` applies — exactly like `run-all`; the operator
  chooses live vs dry per job, and the scheduler never overrides it. With
  `--job NAME`, restrict to that one job: it runs only if it is enabled and
  `is_due()`. Without `--job`, every enabled-and-due job runs.
- **`schedule list`** — print each job, its schedule, and its OS status
  (`registered` / `missing` / `stale` / `extra`).
- **`schedule add <name>`** — register one job's OS task. Refuses (exit 3)
  when the job is unknown, disabled, or manual-only (no OS task to add).
- **`schedule remove <name>`** — unregister; unknown/absent is a clean no-op
  for the OS, but an unknown *job* is exit 3.
- **`schedule sync`** — reconcile every job: add missing, remove extras
  (tasks whose job no longer exists or is disabled/manual), leave unchanged.
  This is the one command an operator runs after install.

### 5.1 Exit codes (consistent with the existing CLI)

| code | meaning |
|---|---|
| `0` | ok, or nothing due / nothing to do |
| `1` | usage / missing credentials |
| `2` | blocked (preflight) |
| `3` | partial/error, unknown job, or refused registration |

## 6. Double-fire guard

Both the in-app scheduler (while the GUI is open) and the OS task can wake the
same job in the same wall-clock minute. The existing in-app `Scheduler._last`
map only dedupes **within the GUI process**; it cannot see the OS task. A shared
per-job, per-minute marker in the state DB closes the cross-process gap:

- `state.claim_due(job, minute_key) -> bool` — `INSERT … ON CONFLICT DO
  NOTHING` into `os_fired(job, minute_key)`; returns True only for the first
  caller in that minute. A second caller in the same minute skips.
- `minute_key = now.strftime("%Y-%m-%dT%H:%M")` — identical to the in-app
  `Scheduler._last` key, so both paths agree on what "this minute" means.
- **Both paths claim it.** `run-due` claims before running a job; the GUI's
  `_on_due` handler claims before enqueueing. Whichever fires first in a minute
  wins; the other skips. `Scheduler._last` is kept as a cheap in-process
  short-circuit, but the DB marker is the authoritative cross-process guard.
- Old rows are pruned opportunistically (e.g. anything older than a day) so the
  table stays small.
- A run skipped because the marker is already set is **not** an error and is
  not counted as fired.
- The existing per-job `filelock` (`timeout=0`) remains the guard for
  *concurrent* runs; the marker guards *sequential* double fires within a
  minute.

## 7. GUI

- **Dashboard**: an "OS" badge per job — `registered` / `missing` / `stale` /
  `extra` / `unsupported` — from `os_scheduler.status()`.
- **"Sync OS schedule"** action: one click runs `schedule sync` on a worker
  thread (never the GUI thread), reports the outcome in the status bar.
- **Auto-sync on save / delete / enable / disable**: best-effort, wrapped so a
  failure logs a warning and shows a non-blocking status message, and the save
  still succeeds. The GUI must never be blocked or failed by `schtasks`
  (e.g. elevation prompts, missing `crontab`).

## 8. Risks and failure modes

| Risk | Level | Mitigation |
|---|---|---|
| Elevated Windows task creation (`schtasks` needs elevation for some options) | Med | Use per-user task options that avoid elevation where possible; on refusal, surface stderr and print the exact manual command. Never mutate state. |
| Credentials absent in the task's environment | High | `run-due` resolves `<project-root>/.env` explicitly (CWD-independent, §4.4) before `load_dotenv()`; verify and exit `1` with a clear message if still missing (a scheduled run cannot prompt). |
| Missed runs while the machine is off | Med | Accepted (non-goal). `is_due` is wall-clock-anchored; the next matching tick runs. No catch-up by design. |
| Poller/task overlaps a long run | Med | Existing per-job `filelock` (`timeout=0`) → the second caller skips with a message. |
| Double fire (GUI + OS) | Low | §6 per-minute marker. |
| Task left behind after job rename/delete | Low | `schedule sync` removes `extra` tasks; delete calls `remove` best-effort. |
| `crontab`/`schtasks` unavailable | Low | Clear message + printed manual command; `status()` reports `unsupported`. |
| macOS | Low | Explicit `unsupported` message; no silent no-op. |

## 9. Testing strategy

TDD throughout; the existing suite stays green.

- **`os_scheduler.plan`** — table-driven: every unit/every/anchor maps to the
  expected trigger; empty/malformed schedule → `None`.
- **`os_scheduler` OS calls** — stub `_schtasks_*` / `_read_crontab` /
  `_write_crontab`; assert argument construction, idempotent add (twice = one),
  remove preserves foreign entries, `sync` adds missing + removes extras.
- **`run-due`** — fake registry + state + clock: only due jobs fire; a
  non-due job does not; `--job X` restricts to X; unknown job → `3`; the
  minute marker prevents a second fire; a held lock skips without error;
  missing credentials → `1`.
- **`state.claim_due`** — first caller True, second in the same minute False,
  different minute True; pruning removes old rows.
- **CWD-independent credentials** — `run-due` loads `<project-root>/.env` when
  run from a foreign CWD (monkeypatch CWD to `tmp_path`); an already-set env var
  wins over the file; missing credentials → exit `1`.
- **GUI** — auto-sync swallows an `os_scheduler` exception and the save still
  succeeds (monkeypatch to raise); `_on_due` claims the minute marker so a GUI
  fire and an OS fire in the same minute produce exactly one run.
- **Reuse** — `is_due` and `Scheduler.tick` keep their existing tests; no new
  schedule logic is introduced.

## 10. Documentation

- New invariant 14 in `AGENTS.md`: **OS-level scheduling wakes the run; `is_due`
  remains the single source of truth for a job's schedule.**
- `CONTEXT.md`: vocabulary for "OS schedule", "trigger", "registered/stale".
- `tasks/todo.md`: Phase 11 ledger.
- `README.md`: an "Automation without the GUI" section (install → `schedule
  sync`, credentials, per-job tasks).

## 11. Out of scope

- Missed-run catch-up; a background service; machine-wide tasks; macOS
  scheduling; changing the schedule model or the in-app scheduler.
