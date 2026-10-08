# AGENTS.md — STG Store Sync

Operating contract for agents working in this repo. Terse by design: what you
must know to change the code without breaking it. Vocabulary lives in
[`CONTEXT.md`](CONTEXT.md); rationale lives in the specs (linked below).

## What this is

Standalone Python 3.12 + PySide6 desktop tool. It pulls SurveyToGo (STG / Dooblo)
interview data for a **source survey** and full-syncs selected columns into an
STG **Subject Store** (External List) via the STG REST API. **Dry-run is the
default.**

Standalone repo — **never import from `stg_automation_suite`** (or any sibling
repo). Reference only.

Status: core + GUI + CLI implemented; **336 tests green**; not yet enabled for a
live write. See `tasks/todo.md` for the phase ledger.

## Stack & layout

- Python 3.12, PySide6, `requests`, `python-dotenv`, `filelock`, `PyYAML`,
  `openpyxl`. Config in `pyproject.toml`; tests via `pytest`.
- `src/` layout, package `store_sync`:
  - `core/` — engine: `config`, `stg_client`, `fetcher`, `binding`, `preflight`,
    `planner`, `writer`, `adoption`, `state`, `registry`, `run_queue`,
    `scheduler`, `sync` (orchestrator).
  - `ui/` — PySide6 views: `main_window`, `dashboard`, `job_detail`,
    `run_detail`, `dialogs`, `job_table_model`, `mapping_io`, `theme`, threads.
  - `cli.py`, `gui.py` — entry points.
- `tests/` mirrors `src/` (`tests/core`, `tests/ui`); 23 test modules.
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
3. **`DeleteRow` always returns `{Done: true}`.** Re-read the store to confirm a
   delete; never trust the response.
4. **The id/tag column is user-declared, never guessed**, and is verified in
   preflight (exists → unique → no-op probe). A wrong declaration answers
   `500 id column data not found`.
5. **Fail closed:** a store-side duplicate match key **halts** the job; a
   source-side duplicate **skips** those rows (run → `partial`). Never guess.
6. **Dry-run is the default.** A live write is an explicit operator choice.
7. **The id value is immutable on update**; adds assign `max(id_column)+1`.
8. **One global run queue** — exactly one sync at a time; never run two.

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
- Pulling a short window makes "absent from the pull" ≠ "should be deleted"; the
  `full_history` delete policy requires a full-population window.
- Value comparison is byte-for-byte; `""` and `" "` are distinct. Normalisation
  is opt-in per column, never silent.
