# Implementation Plan: STG Store Sync

> **Spec:** `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md`
> **Repo:** `stg-store-sync` (new, standalone)
> **Status:** Draft for review
> **Note:** in a fresh standalone repo these become `tasks/plan.md` / `tasks/todo.md`.

## Overview

A standalone desktop tool (Python 3.12 + PySide6) that pulls all interviews for a
source survey, selects configured columns, and full-syncs them into a target STG
Subject Store (External List): add new rows by identity key, update changed rows,
delete by policy. Fail-closed on ambiguity. GUI with a Sync button; dry-run by
default.

## Architecture Decisions

- **Identity = `SbjNum`**, per-interview, immutable once set; `No` is an order
  tag (IsTag), never identity.
- **Lookup by key-set difference**, never by position.
- **Binding gate**: store↔source names must match (`strict`) or be explicitly
  approved (`mapped`); mismatch blocks the job.
- **Strict 100% value compare**; normalisation opt-in per column.
- **Full sync**, `delete_policy=full_history` default; `dry_run=true` default.
- **EAV flatten** of `/SimpleExport` into `{Var: Value}` per subject.
- Reuse the proven pull flow from `scripts/stg_pull_by_date.py` (chunk ≤100,
  `dateType=Upload`, mid-stream retry) — reimplemented standalone, not imported.
- Reference only: `src/knowledge/assets/STG_RESTAPI/Form1.cs` (not imported).

## Task List

### Phase 0: De-risk (blocks everything)

- [ ] **Task 0.1: Confirm `UpdateRow` semantics**
  - Description: Prove how `UpdateRow` selects a row and what it writes, using a
    throwaway probe against the real server.
  - Acceptance: A documented request/response showing which column(s) select the
    row and that `No`/`SbjNum` are preserved.
  - Verification: manual probe output committed to `docs/probe/`.
  - Deps: None. Files: `/tmp` probe only. Scope: S.

- [ ] **Task 0.2: Confirm `DeleteRow` semantics (`userTag`)**
  - Description: Prove `DeleteRow` removes exactly one intended row and what
    `userTag` maps to (`No`?).
  - Acceptance: documented probe; before/after row counts.
  - Deps: None. Scope: S.

- [ ] **Task 0.3: Confirm a new column (`SbjNum`) can be added**
  - Description: Determine whether `AddRow`/`UpdateRow` accept a genuinely new
    column, or whether columns must be pre-defined in Studio.
  - Acceptance: documented result; if pre-definition required, note the Studio step.
  - Deps: None. Scope: S.

- [ ] **Task 0.4: Remove dummy row `uniqueID=999999`**
  - Description: Delete the probe row added earlier.
  - Acceptance: `GetStore/17417` no longer contains `999999`; row count back to 295.
  - Deps: 0.2. Scope: XS.

### Checkpoint: Phase 0
- [ ] All four probes documented; unknowns in spec §10 answered
- [ ] Review with human before any product code

### Phase 1: Skeleton

- [ ] **Task 1.1: Repo scaffold** — `pyproject.toml` (pyside6, requests,
  python-dotenv, filelock, pytest), `README.md`, `.env.example`, `.gitignore`,
  `src/store_sync/` package, `tests/`.
  - Acceptance: `pip install -e .` succeeds; `python -c "import store_sync"` works.
  - Deps: 0. Files: 6. Scope: S.

- [ ] **Task 1.2: `core/config.py`** — job schema (dataclasses) + YAML load/save +
  validation (required fields, enum validation for `mode`/`op`/`delete_policy`).
  - Acceptance: valid config round-trips; invalid enum raises with a clear message.
  - Verification: `pytest tests/core/test_config.py`.
  - Deps: 1.1. Files: 2. Scope: S.

- [ ] **Task 1.3: `core/stg_client.py`** — Basic auth, retry, methods:
  `get_stores`, `get_store`, `get_store_usage`, `survey_interview_ids`,
  `simple_export`, `add_row`, `update_row`, `delete_row`.
  - Acceptance: all methods unit-tested with a mocked transport; no live calls in tests.
  - Verification: `pytest tests/core/test_stg_client.py`.
  - Deps: 1.1. Files: 2. Scope: M.

### Checkpoint: Skeleton
- [ ] `pytest` green; client covered by mocks; config validation proven

### Phase 2: Binding gate

- [ ] **Task 2.1: `core/binding.py` — parity + key test**
  - Description: `strict` name-parity check; auto-suggest store-col→source-var by
    value overlap; key-validity test (reject non-unique candidates).
  - Acceptance: `QRelID_RVid`-style non-unique candidate is rejected; `strict`
    mismatch returns blocked with a reason.
  - Verification: `pytest tests/core/test_binding.py` (fixtures from real shapes).
  - Deps: 1.2, 1.3. Files: 2. Scope: M.

- [ ] **Task 2.2: Preflight orchestration** — produce the review table
  (store col, suggested var, overlap, status) from a live store + sample export.
  - Acceptance: given the Link-1/`Gran_Turismo_Link2` shapes, the table matches
    spec §5.2.
  - Deps: 2.1. Files: 1. Scope: S.

### Checkpoint: Binding
- [ ] `strict` can block; `mapped` requires explicit approval; preflight table verified

### Phase 3: Data

- [ ] **Task 3.1: `core/fetcher.py`** — pull IDs + `SimpleExport` in chunks,
  retry on mid-stream disconnect, flatten EAV → `{Var: Value}` per subject.
  - Acceptance: EAV payload flattens correctly; chunking + retry covered by tests.
  - Verification: `pytest tests/core/test_fetcher.py`.
  - Deps: 1.3. Files: 2. Scope: M.

- [ ] **Task 3.2: `core/planner.py`** — diff store vs source by identity key →
  add/update/delete/conflicts; fail-closed rules (§4.3).
  - Acceptance: add/update/skip/delete classification proven; missing/dup key →
    conflict, no write.
  - Verification: `pytest tests/core/test_planner.py`.
  - Deps: 2.1, 3.1. Files: 2. Scope: M.

### Checkpoint: Data
- [ ] A dry-run plan for the real job shows only expected classifications

### Phase 4: Adoption + Write

- [ ] **Task 4.1: `core/state.py`** — SQLite: jobs, run log, conflicts, row hashes.
  - Acceptance: run+conflicts persist; hashes stable across runs.
  - Verification: `pytest tests/core/test_state.py`.
  - Deps: 1.1. Files: 2. Scope: M.

- [ ] **Task 4.2: Legacy `SbjNum` adoption** — match legacy rows to source, write
  identity once, quarantine ambiguity.
  - Acceptance: 1:1 matches get `SbjNum`; ambiguous rows are quarantined, not guessed.
  - Deps: 2.1, 3.1, 4.1. Files: 2. Scope: M.

- [ ] **Task 4.3: `core/writer.py`** — apply plan: `AddRow`/`UpdateRow`/`DeleteRow`,
  throttle, retry, verify-before-add, identity never rewritten.
  - Acceptance: idempotent update; add guarded; delete only per policy; identity
    column untouched on update.
  - Verification: `pytest tests/core/test_writer.py` (mock transport).
  - Deps: 3.2, 4.1. Files: 2. Scope: M.

### Checkpoint: Write
- [ ] End-to-end dry-run on real config produces a correct plan with zero writes
- [ ] A single controlled real write (one add / one update / one delete) verified
- [ ] Human review before enabling full sync

### Phase 5: GUI

- [ ] **Task 5.1: `ui/main_window.py`** — job list, config editor, preflight view +
  Approve-binding dialog, Sync button, dry-run toggle, run log, conflicts panel,
  delete-policy selector.
  - Acceptance: operator can configure a job, run preflight, approve binding, and
    run a dry-run Sync without touching code.
  - Verification: manual smoke + `tests/ui/test_main_window.py` (construct + wiring).
  - Deps: 2.2, 3.2, 4.2, 4.3. Files: 2-3. Scope: L (split if it grows).

### Checkpoint: GUI
- [ ] `gui.py` launches; full flow exercised on the real job in dry-run

### Phase 6: Scheduled (optional)

- [ ] **Task 6.1: `cli.py` + filelock** — headless `sync` command + `run-due`-style
  entry for OS cron; single-instance lock; audit log.
  - Acceptance: two concurrent runs do not overlap (lock proven); CLI exit codes
    reflect outcome.
  - Verification: `pytest tests/test_cli.py`.
  - Deps: 4.3. Files: 2. Scope: M.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| `UpdateRow`/`DeleteRow` semantics unknown | High | Phase 0 gate before any writer code |
| Store rejects new `SbjNum` column | High | Task 0.3 gate; fallback = Studio pre-define |
| `full_history` delete removes legitimate rows | High | full-history window required; dry-run; confirm dialog; audit log |
| `AddRow` not idempotent | High | verify key first; hash in state |
| Strict 100% vs real data | Medium | report conflicts; opt-in normalisation |
| 1 request/row × N | Medium | throttle + retry + resume |
| Store LIVE ─ production writes | High | dry-run default; explicit confirm; audit |
| Key drift | Medium | identity immutable; fail-closed |

## Definition of Done

- Phase 0 unknowns answered and documented.
- `pytest` green across core modules.
- Dry-run produces a correct plan for the real job.
- One controlled real sync (add/update/delete) verified and audited.
- GUI runs the full flow without code edits.
- README documents setup, config, and safe-operation steps.

## Open Questions

Carried from spec §10; Task 0.x closes them.
