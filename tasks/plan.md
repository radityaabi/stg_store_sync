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

- **Two columns, two roles** (probe F1/F2): the store's **API id/tag column**
  (store-specific: `No`, `uniqueID`, …) keys `UpdateRow`/`DeleteRow` and is
  immutable; the **match key** `SbjNum` (per-interview) is used for diffing.
- **Lookup by key-set difference**, never by position.
- **Binding gate**: alias pairs `{source_var → store_col}` approved by a human
  (`mapped`, default); `strict` additionally requires `source == store`.
- **Writes send the full row** — the API resets omitted columns (probe F2).
- **`UpdateRow` is an upsert** on the id column; id value immutable.
- **Columns must be pre-defined in Studio** — the API cannot add one (probe F3).
- **`DeleteRow` always returns `{Done: true}`** — re-read to confirm (probe F4).
- **Strict 100% value compare**; normalisation opt-in per column.
- **Full sync**, `delete_policy=full_history` default; `dry_run=true` default.
- **EAV flatten** of `/SimpleExport` into `{Var: Value}` per subject.
- Reuse the proven pull flow from `scripts/stg_pull_by_date.py` (chunk ≤100,
  `dateType=Upload`, mid-stream retry) — reimplemented standalone, not imported.
- Reference only: `src/knowledge/assets/STG_RESTAPI/Form1.cs` (not imported).

## Task List

### Phase 0: De-risk — ✅ COMPLETE

Findings: `docs/probe/2026-10-08-fase0-findings.md`.

- [x] **Task 0.1: Confirm `UpdateRow` semantics** — upsert on the store id
  column; full-row payload; id value immutable.
- [x] **Task 0.2: Confirm `DeleteRow` semantics** — `userTag` = id-column value,
  passed as a **path** segment; always returns `{Done: true}`.
- [x] **Task 0.3: New column via API?** — **no**; must be pre-defined in Studio.
- [x] **Task 0.4: Remove dummy row `uniqueID=999999`** — done; 17417 back to 295.
- [x] End-to-end add/update/delete cycle validated on `DUMMY_AUTO_STORE` (17620).

### Checkpoint: Phase 0
- [x] All probes documented; spec §10 unknowns answered
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

- [ ] **Task 2.1: `core/binding.py` — alias mapping + key/id verification**
  - Description: `mapped` alias pairs `{source_var → store_col}`; `strict`
    additionally requires `source == store`; auto-suggest store-col→source-var by
    value overlap; match-key uniqueness test; **id-column declaration check**
    (§4.2.1: exists → unique → no-op `UpdateRow` probe).
  - Acceptance: a non-unique match key (e.g. `QRelID_RVid`, 248/296) is rejected;
    `strict` mismatch is blocked with a reason; a wrong `id_column` returns
    "id column data not found" and is flagged.
  - Verification: `pytest tests/core/test_binding.py` (fixtures from real shapes).
  - Deps: 1.2, 1.3. Files: 2. Scope: M.

- [ ] **Task 2.2: Preflight orchestration** — produce the review table
  (store col, suggested var, overlap, status) from a live store + sample export,
  and run the id-column declaration check (§4.2.1).
  - Acceptance: given the Link-1/`Gran_Turismo_Link2` shapes, the table matches
    spec §5.2 and the declared `id_column=uniqueID` is confirmed by the no-op probe.
  - Deps: 2.1. Files: 1. Scope: S.

### Checkpoint: Binding
- [ ] alias pairs approved by a human; `strict` can block; id column verified; `mapped` requires explicit approval

### Phase 3: Data

- [ ] **Task 3.1: `core/fetcher.py`** — pull IDs + `SimpleExport` in chunks with an
  optional `variableNames` filter (from a **pull name** — the question-level base
  name — declared per column or offered by preflight from a live variable list;
  do **not** guess it by stripping the answer var, loop vars like `I_351_Q46a`
  break the pattern), retry on mid-stream disconnect, flatten EAV →
  `{Var: Value}` per subject (missing configured var → `""`), **verify** every
  needed var is present (fall back to full export if not), and **project** to the
  configured columns.
  - Acceptance: EAV payload flattens correctly with empties as `""`; a filter that
    drops a needed var triggers fallback; chunking + retry covered by tests.
  - Verification: `pytest tests/core/test_fetcher.py`.
  - Deps: 1.3. Files: 2. Scope: M.

- [ ] **Task 3.2: `core/planner.py`** — diff store vs source by **match key**
  (`SbjNum`) → add/update/delete/conflicts; fail-closed rules (§4.4). Add assigns
  `next_tag = max(id_column)+1`.
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
  throttle, retry, **full-row payloads**, id column never rewritten, re-read after
  deletes to confirm.
  - Acceptance: update is an idempotent no-op when values match; add sends the
    assigned tag; delete verified by re-read; id column untouched on update.
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
| `UpdateRow`/`DeleteRow` semantics | Resolved | Fase 0 probe documented |
| Store rejects new `SbjNum` column | Resolved | pre-define in Studio (§4.5) |
| `UpdateRow` resets omitted columns | High | always send the full row |
| Upsert on add → accidental insert | Medium | id value assigned `max+1` |
| `DeleteRow` success on no-match | Medium | re-read store after deletes |
| `full_history` delete removes legitimate rows | High | full-history window required; dry-run; confirm dialog; audit log |
| Strict 100% vs real data | Medium | report conflicts; opt-in normalisation |
| 1 request/row × N | Medium | throttle + retry + resume |
| Store LIVE ─ production writes | High | dry-run default; explicit confirm; audit |
| Id column declared wrongly | Medium | preflight verifies it (Task 2.1) |
| Empty treated as "skip" → stale store cells | High | empty = `""`, always written (F7/F8) |
| `variableNames` silently drops a needed var | Medium | verify response; fall back to full export (F9) |

## Definition of Done

- Phase 0 unknowns answered and documented. ✅
- `pytest` green across core modules.
- Dry-run produces a correct plan for the real job.
- One controlled real sync (add/update/delete) verified and audited.
- GUI runs the full flow without code edits.
- README documents setup, config, and safe-operation steps.

## Open Questions

- `full_history` window configuration per job (how far back is "full"?).
- Is `SbjNum` ever absent from an interview export (helper/system subjects)?
