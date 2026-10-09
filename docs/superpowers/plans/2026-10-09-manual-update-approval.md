# Implementation Plan: Manual Update Approval + Delete Removal

> **Supersedes:** `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md` §6
> (Write Model & Delete Policy) and §7.3 (delete-policy selector).
> **Repo:** `stg-store-sync`
> **Status:** Draft for review

## Overview

Change the write model from "full sync (add + update + delete) automated" to
**automation = AddRow only**. Update and delete are no longer executed
automatically by the tool:

- **Add** — automated. New rows (match key in source, absent from store) are
  written by `run_sync` as today.
- **Update** — the tool only **proposes**. A proposal exists **only when at
  least one mapped column differs byte-for-byte**; identical rows never produce
  a proposal. The operator reviews the proposals (with the exact per-column
  `old → new` diffs), ticks the ones to apply, and presses **Apply selected**.
  Application is compare-and-swap: if the store row changed since the proposal
  was computed, the proposal is stale and is **not** written.
- **Delete** — removed entirely. The tool never calls `DeleteRow`, exposes no
  delete option, and has no delete policy. Store rows absent from a pull are
  left untouched (a short/failed pull must never imply deletion).

Rationale (operator): automatic update would clobber a manual revision made
directly in the store with older survey data; automatic delete is unacceptable
because a failed/partial pull would look like "these ids should be removed".

## Baseline (current condition, 2026-10-09)

There is **uncommitted WIP** in the tree (14 files) that is *unrelated* to this
plan: a live run timeline (`_ProgressBridge`, `_STAGE_TEXT`, `set_run_progress`),
toast notifications (`ui/toast.py`, `MainWindow.toast`), removal of the `kind`
column from the binding UI + mapping I/O (`HEADERS = ["store_col","source_var"]`),
a fast-path mapping import (source-var validation deferred to preflight),
`State.recover_orphan_runs`, and a fail-fast `.env` client build. **Build on top
of this state; do not revert it.** Two consequences:

- Tasks 9/10 edit files that the WIP already touches (`ui/job_detail.py`,
  `gui.py`, `ui/job_table_model.py`) — rebase onto the WIP, don't overwrite it.
- `ColumnBinding.kind` still exists in `core/config.py` (dormant; only the UI
  dropped it). This plan does **not** touch `kind`; Task 9 must not re-add a
  Kind column.

Everything this plan targets is still present in the tree: `planner` delete +
`delete_policy` + `delete_candidates`; `writer` add/update/delete execution;
`stg_client.delete_row`/`delete_all_rows`; `config.DeletePolicy` +
`WriteConfig.delete_policy`; `registry.delete_policy` column; `sync` auto-applies
update+delete; no `proposals` table.

**Pre-existing failing test (blocker for the green-suite DoD):**
`tests/test_cli.py::TestRun::test_run_missing_creds_is_1` returns `3`, not `1`.
Cause is environmental, not this plan: a local `.env` exists and `cli.main()`
calls `load_dotenv()` unconditionally, so real creds override the test's
`monkeypatch.delenv`. Fix (Task 0) is test isolation, not product behavior.

## Architecture Decisions

- **Proposal, not action.** `planner` renames the write-bearing `update` action
  to carry an explicit `diffs: list[(col, old, new)]`; an action with no diffs
  stays `same`. Only `add` is auto-applied.
- **Compare-and-swap apply.** `writer.apply_updates` re-reads the store, and for
  each selected proposal verifies every diffed column still equals its recorded
  `old` before writing; a mismatch marks the proposal `stale` and skips it. The
  write payload is the **current** store row overlaid with the proposed new
  values (so unrelated manual edits to non-diffed columns survive).
- **Proposals persist.** A `proposals` table in the state DB stores pending
  proposals so headless/scheduled runs surface the same review queue in the GUI.
- **Delete gone end-to-end.** `planner`, `writer`, `sync`, `config`, `registry`,
  `ui`, `cli`, and `stg_client` drop every delete path (`delete_row`,
  `delete_all_rows`, `delete_policy`, `DeletePolicy`, `delete_candidates`).
- **`dry_run` unchanged** (default true): dry-run applies no adds and records no
  proposals as applied.
- **Headless = add-only.** CLI/scheduler runs apply adds and record proposals;
  they never apply updates.

## Data Model

```sql
CREATE TABLE IF NOT EXISTS proposals (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    job         TEXT    NOT NULL,
    match_value TEXT    NOT NULL,
    id_value    TEXT,
    payload     TEXT    NOT NULL,   -- JSON: full new row (current row + new values)
    diff        TEXT    NOT NULL,   -- JSON: [{col, old, new}, ...]
    status      TEXT    NOT NULL DEFAULT 'pending',  -- pending|applied|stale|dismissed
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    decided_at  TEXT
);
```

`WriteConfig` becomes `{op, dry_run, normalize}` — `delete_policy` and the
`DeletePolicy` enum are removed. A legacy stored `delete_policy` column is
dropped on registry open (reuse `_drop_legacy_columns`); its value is ignored.

## Task List

### Phase 1: Spec + core model (foundation)

- [ ] **Task 0: Fix the pre-existing CLI test isolation** — `cli.main()` calls
  `load_dotenv()` unconditionally, so a local `.env` leaks real creds into
  `tests/test_cli.py::test_run_missing_creds_is_1` (returns `3`, expected `1`).
  Make the test hermetic (e.g. monkeypatch `cli.load_dotenv` to a no-op, or
  point `dotenv` at an empty path) without changing product behavior.
  - Acceptance: `pytest tests/test_cli.py` green; the test proves the missing-cred
    exit code, not the developer's `.env`.
  - Verification: `pytest -q tests/test_cli.py`.
  - Deps: none. Files: 1. Scope: XS.

- [ ] **Task 1: Spec + vocabulary delta** — rewrite base-spec §6 (Write Model:
  Add automated, Update proposed + operator-approved, Delete removed) and remove
  the delete-policy selector from §7.3; update `AGENTS.md` hard invariants
  (#1 full-column payload stays; replace "full sync add+update+delete" framing
  with add-automated / update-proposed / delete-removed; add the CAS rule);
  update `CONTEXT.md` (drop **Delete policy**; add **Update proposal**,
  **Stale proposal**, **Apply selected**).
  - Acceptance: no doc still describes automated update or any delete option.
  - Verification: `grep -ri "delete_policy\|Delete policy\|DeleteRow" docs/ CONTEXT.md AGENTS.md` returns only historical/probe references, not normative text.
  - Deps: none. Files: 3. Scope: S.

- [ ] **Task 2: `core/config.py` — drop delete policy** — remove `DeletePolicy`,
  `WriteConfig.delete_policy`, its `from_dict`/`to_dict`/`validate` handling.
  Keep `op`, `dry_run`, `normalize`.
  - Acceptance: a YAML with a legacy `delete_policy:` key loads and the key is
    ignored; validation no longer mentions it; round-trip omits it.
  - Verification: `pytest tests/core/test_config.py` (update the 4 delete-policy
    tests to assert the key is ignored/absent).
  - Deps: none. Files: 2. Scope: S.

- [ ] **Task 3: `core/planner.py` — proposals, no delete** — remove the
  `delete_policy` parameter, `delete_candidates`, and every `Action("delete",…)`
  branch; remove `tracked`-driven deletion. For `key in src ∩ store`, compute
  per-column `diffs` (`[{"col", "old", "new"}]`, byte-for-byte on `str()`);
  emit `Action("update", …, diffs=[...])` only when `diffs` is non-empty, else
  `Action("same", …)`. `Action` gains a `diffs: list` field.
  - Acceptance: identical row → `same` with empty diffs, **no** proposal; one
    changed column → `update` with exactly that diff; absent store key → no
    action; store-side dup still `conflict` (halts); source-side dup still
    `conflict` (skip).
  - Verification: `pytest tests/core/test_planner.py` (rewrite delete tests to
    "no delete action"; add diff-shape + no-false-positive tests).
  - Deps: none. Files: 2. Scope: M.

- [ ] **Task 4: `core/writer.py` — add-only apply + CAS updates** —
  `apply_plan` handles **only** `add` (remove update/delete execution and the
  delete re-read verification). Add `apply_updates(client, store_id, actions,
  *, id_column, throttle, max_retries, backoff, should_stop)`: re-read store once,
  index by `id_column`, for each action verify all `diffs` columns still equal
  `old` (else `stale`), build the payload from the **current** row + new values
  (+ match col + id col), call `update_row`; return
  `UpdateResult(applied, stale, errors, counts)`.
  - Acceptance: unchanged store row → applied; a manually-changed diffed column →
    `stale`, no write; non-diffed manual edits preserved in the payload; add path
    unchanged; no `DeleteRow` call anywhere.
  - Verification: `pytest tests/core/test_writer.py` (drop delete tests; add CAS
    stale + preserved-column tests).
  - Deps: Task 3. Files: 2. Scope: M.

- [ ] **Task 5: `core/state.py` — proposals table** — add the `proposals` schema
  and methods: `record_proposal(run_id, job, match_value, id_value, payload,
  diff)`, `pending_proposals(job)` (status `pending`), `set_proposal_status(id,
  status)`, `proposals_for_run(run_id)`.
  - Acceptance: record → read pending; status transitions persist; JSON
    round-trips.
  - Verification: `pytest tests/core/test_state.py` (new proposal tests).
  - Deps: none. Files: 2. Scope: S.

- [ ] **Task 6: `core/sync.py` — apply adds, record proposals** — `run_sync`
  applies adds via `apply_plan`; for each `update` action records a proposal in
  `state` (payload + diff) and does **not** call `update_row`; removes delete
  handling and delete-driven tracking/untracking; keeps the readiness gate,
  conflict recording, cancellation, and run-status rules. Tracking records
  applied adds only.
  - Acceptance: a run with a changed row writes the add(s), leaves the store row
    unchanged, and records exactly one `pending` proposal with the right diff;
    dry-run records no applied state and no writes; store-side dup still blocks.
  - Verification: `pytest tests/core/test_sync.py`.
  - Deps: Tasks 3, 4, 5. Files: 1. Scope: M.

### Checkpoint: Core
- [ ] All core tests pass (`pytest -q tests/core`)
- [ ] No `delete` action / `DeleteRow` / `delete_policy` remains in `src/store_sync/core`
- [ ] A dry-run on the real job yields add actions + pending proposals, zero writes

### Phase 2: Surface (registry, client, UI, CLI)

- [ ] **Task 7: `core/registry.py` — drop delete_policy** — remove it from the
  `jobs` schema/`_config_values`/`update`/`get`/`JobEntry`/`_ENTRY_FIELDS`; add
  `delete_policy` to the `_drop_legacy_columns` migration so old DBs migrate.
  - Acceptance: old DB with `delete_policy` opens, column dropped, `get`/entries
    no longer expose it; new DBs have no such column.
  - Verification: `pytest tests/core/test_registry.py` (update legacy-DB fixture
    + assertions).
  - Deps: Task 2. Files: 2. Scope: M.

- [ ] **Task 8: `core/stg_client.py` — remove delete surface** — delete
  `delete_row` and `delete_all_rows` so the tool has no delete capability.
  - Acceptance: no delete method on the client; the module docstring no longer
    documents delete semantics.
  - Verification: `pytest tests/core/test_stg_client.py` (remove delete tests).
  - Deps: none. Files: 2. Scope: S.

- [ ] **Task 9: `ui/job_detail.py` + `ui/dialogs.py` + `ui/models.py` — proposals
  review** — remove the delete-policy combo from **both** the detail config tab
  (`job_detail._config_tab` / `config_fields` / `load`) **and** the `JobDialog`
  (`dialogs.py` `_build_ui` / `load` / `job()`); add a **Proposed updates**
  panel/tab: table of pending proposals (match, id, `col: old → new` summary)
  with a checkbox column, plus **Apply selected** / **Dismiss selected** buttons
  and a refresh on run finish. Emit `apply_proposals_requested(ids)` /
  `dismiss_proposals_requested(ids)`. Do **not** re-add a Kind column (the WIP
  deliberately dropped it).
  - Acceptance: proposals render with their diffs; ticking + Apply emits the
    selected ids; a job with no proposals shows an empty state; no
    `delete_policy_combo` remains in `job_detail` or `dialogs`.
  - Verification: `pytest tests/ui/test_job_detail.py tests/ui/test_run_detail.py`
    (update the `delete_policy_combo` assertion + render/signal tests).
  - Deps: Task 5. Files: 4. Scope: M.

- [ ] **Task 10: `gui.py` — wire apply/dismiss** — controller handlers call
  `writer.apply_updates(...)` for the ticked proposals, update each proposal's
  status (`applied`/`stale`/`dismissed`) via `state`, track applied rows, and
  report stale ones in a toast/status. Remove `delete_policy` from
  `_job_from_detail` (gui.py) and its plumbing through `job_detail.config_fields`.
  - Acceptance: applying a proposal with an unchanged store row writes it and
    marks `applied`; a manually-changed row is marked `stale` and not written;
    dismiss marks `dismissed` without a write; `WriteConfig` is built without
    `delete_policy`.
  - Verification: `pytest tests/ui/test_controller.py` (fake client; assert
    update_row called only for the fresh proposal).
  - Deps: Tasks 4, 5, 9. Files: 2. Scope: M.

- [ ] **Task 11: `cli.py` — add-only + proposal count** — `run`/`run-all` report
  the number of recorded proposals; no delete-policy references (CLI has none
  today); exit codes unchanged (adds ok → 0, blocked → 2, partial/error → 3).
  - Acceptance: a CLI run writes adds, prints the proposal count, and never
    updates/deletes; JSON output includes `proposals`.
  - Verification: `pytest tests/test_cli.py`.
  - Deps: Task 6. Files: 2. Scope: S.

### Checkpoint: Surface
- [ ] Full suite green (`pytest -q` and `pytest -q -W error`)
- [ ] GUI smoke: run a job → review proposals → apply one → store reflects it
- [ ] No delete affordance anywhere in the UI/CLI

### Phase 3: Verification

- [ ] **Task 12: End-to-end acceptance on `DUMMY_AUTO_STORE`** — with a fake or
  live client: add a new subject (auto), change one column of an existing subject
  (proposal only), manually alter that store cell, then Apply → proposal is
  `stale`; re-propose and Apply → applied. Confirm a short pull deletes nothing.
  - Acceptance: documented evidence in `docs/probe/2026-10-09-manual-update-approval-acceptance.md`.
  - Deps: Tasks 6, 10. Files: 1. Scope: S.

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Proposal applied against a row the operator just revised | High | compare-and-swap on every diffed column; stale → skip + report |
| Short/failed pull mistaken for "delete these" | High | delete removed entirely; absent rows untouched |
| False-positive proposals (identical data) | Medium | proposal only when ≥1 byte-for-byte diff; tested |
| Stale pending proposals accumulate | Low | per-job pending queue + dismiss; statuses recorded |
| Legacy `delete_policy` rows break open | Low | dropped in registry migration; value ignored |
| GUI review adds latency vs one-click sync | Low | adds still automatic; only updates need review |

## Open Questions

- Should `apply_updates` re-pull the source at apply time to confirm the survey
  value is still current (not just the store row)? (Leaning: no — the proposal
  already carries the surveyed value; store-side CAS is the safety net.)
- Should dismissed proposals reappear if the same diff recurs next run? (Leaning:
  yes — proposals are per-run, not a suppression list.)

## Definition of Done

- Automation executes **AddRow only**; update is proposal + operator approval;
  delete does not exist.
- `delete_policy`/`DeletePolicy`/`DeleteRow` gone from code, config, UI, CLI, docs.
- Compare-and-swap prevents overwriting a manual revision; stale is reported.
- `pytest -q` and `pytest -q -W error` green; end-to-end acceptance documented.
