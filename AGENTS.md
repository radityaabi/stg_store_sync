# AGENTS.md — STG Store Sync

Operating contract for agents working in this repo. Terse by design: what you
must know to change the code without breaking it. Vocabulary lives in
[`CONTEXT.md`](CONTEXT.md); rationale lives in the specs (linked below).

## What this is

Standalone Python 3.12 + PySide6 desktop tool. It pulls SurveyToGo (STG / Dooblo)
interview data for a **source survey** and syncs selected columns into an
STG **Subject Store** (External List) via the STG REST API. **Dry-run is the
default.**

Standalone repo — **never import from `stg_automation_suite`** (or any sibling
repo). Reference only.

Status: core + GUI + CLI implemented; **489 tests green**; not yet enabled for a
live write. See `tasks/todo.md` for the phase ledger.

## Stack & layout

- Python 3.12, PySide6, `requests`, `python-dotenv`, `filelock`, `PyYAML`,
  `openpyxl`. Config in `pyproject.toml`; tests via `pytest`.
- `src/` layout, package `store_sync`:
  - `core/` — engine: `config`, `stg_client`, `fetcher`, `binding`, `preflight`,
    `planner`, `writer`, `adoption`, `state`, `registry`, `run_queue`,
    `scheduler`, `sync` (orchestrator), `timefmt` (UTC→local display),
    `pic` (PIC list helpers), `owa_mailer` (Playwright/OWA transport),
    `notify` (run-notification domain).
  - `ui/` — PySide6 views: `main_window`, `dashboard`, `job_detail`,
    `run_detail`, `dialogs`, `job_table_model`, `mapping_io`, `theme`, threads.
  - `cli.py`, `gui.py` — entry points.
- `tests/` mirrors `src/` (`tests/core`, `tests/ui`); 28 test modules.
- Runtime state: SQLite at `~/.stg_store_sync/state.db` (override
  `STG_STORE_SYNC_DB`). Tables: `jobs`/`job_columns` (Registry),
  `runs`/`conflicts`/`tracked` (State).

## Commands

```bash
# setup
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill STG_SERVER / STG_USER / STG_PASSWORD

# tests — run BOTH before and after changes
./.venv/bin/python -m pytest -q
./.venv/bin/python -m pytest -q -W error

# run the app
./.venv/bin/python -m store_sync.gui          # or: stg-store-sync
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c \
  "from PySide6.QtWidgets import QApplication as A; from store_sync.ui.main_window import MainWindow as W; a=A.instance() or A([]); w=W(); w.show(); w.close()"

# headless CLI (registry-backed)
stg-store-sync-cli list
stg-store-sync-cli run <name> [--dry-run/--no-dry-run] [--json]
stg-store-sync-cli run-all [--enabled-only] [--json]
stg-store-sync-cli stop <name>
```

CLI exit codes: `0` ok · `2` blocked (preflight) · `3` partial/error · `1` usage.

## Hard invariants

Violating any of these corrupts production data or the safety model:

1. **Writes send the store's FULL column set.** `AddRow`/`UpdateRow` reset any
   omitted column to default. ADD blanks unmapped columns; UPDATE starts from
   the existing store row and overlays mapped columns.
2. **Empty source value = `""`, always written — never skipped.** A configured
   var absent from the export is empty, not "leave untouched".
3. **Automation writes ADD only.** An `update` is a **proposal** recorded for
   operator review; it is never written by a run. Delete does not exist — the
   tool never calls `DeleteRow`, and store rows absent from a pull are left
   untouched (a short/failed pull must never imply deletion).
4. **Update application is compare-and-swap.** `writer.apply_updates` re-reads
   the store and writes a proposal only when every diffed column still equals its
   recorded `old` value; a mismatch is `stale` and skipped. The written payload
   is the current store row overlaid with the new values.
5. **A proposal exists only for a genuine byte-for-byte diff.** Identical rows
   are `same` — no proposal, ever.
6. **The id/tag column is user-declared, never guessed**, and is verified in
   preflight (exists → unique → no-op probe). A wrong declaration answers
   `500 id column data not found`.
7. **Fail closed:** a store-side duplicate match key **halts** the job; a
   source-side duplicate **skips** those rows (run → `partial`). Never guess.
8. **Dry-run is the default.** A live write is an explicit operator choice.
9. **The id value is immutable on update**; adds assign `max(id_column)+1`.
10. **One global run queue** — exactly one sync at a time; never run two.
11. **Headless = add-only.** CLI/scheduler runs write adds and record proposals;
    they never apply updates.
12. **The survey definition is read only by the Pairing action (prep), never
    during a run.** A job with no pairing — empty `survey_index`,
    `paired_survey_id != survey_id`, or any empty `pull_term` — is `blocked`
    before any write; there is no implicit XML fetch and no full-export fallback.
13. **Deleted rows are blacklisted, not re-added.** A match key this system has
    written (`tracked`) that is absent from the store was deleted by hand; the
    next run suppresses its `add` and records it in the per-job `blacklist`
    (state DB). A blacklisted key back in the store is auto-cleared. An empty
    store snapshot is *unknown*, not a mass deletion. Suppression applies to
    `add` only, is never an error, and is recorded on dry-run too.

14. **OS-level scheduling only wakes the run; `is_due` is the schedule.**
    One OS task per enabled, scheduled job (`schedule sync`) runs
    `stg-store-sync-cli run-due --job <name>`. The OS trigger is coarse; the
    precise decision is always `Scheduler.tick()` / `is_due()`. `run-due` is
    add-only and passes `dry_run=None` (each job's configured dry-run applies).
    A per-minute `os_fired` marker stops a GUI fire and an OS fire in the same
    minute from running twice.

15. **A run notification is a side channel.** After a run that added ≥1 row, the
    job's PIC list is mailed an HTML summary (OWA via Playwright, no SMTP).
    It never changes a run's recorded status, never raises into `run_sync`, and
    a dry-run or a clean (0-add) run never mails. A send that fails is recorded
    as `failed` and nothing else changes. Every attempt — sent, skipped, failed
    or dry_run — is a `notifications` row. A PIC entry that is not an email
    address warns in the GUI but never blocks saving, and is never mailed.

## Docs — source of truth

- Spec: `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md`
- GUI/registry redesign: `docs/superpowers/specs/2026-10-08-gui-registry-redesign-design.md`
- Design system (QSS tokens, GUI rules): `DESIGN.md` — single styling source is
  `ui/theme.py`; widgets set object names only.
- Live probe findings: `docs/probe/` (write model, empty values, export filter,
  dry-run acceptance).
- Plan / progress / todo: `tasks/`, `docs/superpowers/plans/`.
- Vocabulary: [`CONTEXT.md`](CONTEXT.md).
- Sibling suite `stg_automation_suite` shares the visual language — **reference
  only, never import**.

## Workflow

- **TDD**: add/adjust tests with every behavior change; keep the suite green.
- Update `README.md` / specs / `tasks/` when behavior changes.
- Don't mark work complete without proving it (tests pass, logs checked).
- **Don't commit, push, or rewrite history unless explicitly asked.**
- **Never read, print, or commit secrets** (`.env`, credential files).
- Don't delete global paths; delete only files this session created.
- Verify claims with real command output before asserting success.

## Threads & resources

- Join every owned QThread in `closeEvent` / `stop()`; no fire-and-forget
  threads. Never drop the last ref to a running QThread
  (`QThread: Destroyed while thread is still running`).
- SQLite connections and `requests.Session` are per-thread; close both on
  shutdown. Marshal worker callbacks back onto the GUI thread before touching
  widgets.

## Gotchas

- `UpdateRow` is an **upsert** on the id column; the id value is immutable.
- New store columns **cannot** be created via the API — pre-define in STG Studio;
  otherwise use one-time **adoption** (`core/adoption.py`, dry-run first).
- The `SimpleExport` `variableNames` filter takes the **question-level base
  name** (`QPM1`), not the answer-level var (`T_QPM1_1`); it is an optimisation —
  verify the response and fall back to a full export if a needed var is missing.
- Pulling a short window makes "absent from the pull" ≠ "should be deleted";
  absent rows are never touched (there is no delete).
- Value comparison is byte-for-byte; `""` and `" "` are distinct. Normalisation
  is opt-in per column, never silent.
