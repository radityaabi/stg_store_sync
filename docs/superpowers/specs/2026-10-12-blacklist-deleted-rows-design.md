# STG Store Sync — Blacklist (Suppress Re-Adding Deleted Rows)

> **Status:** Draft for review
> **Date:** 2026-10-12
> **Repo:** `stg-store-sync`
> **Extends:** `2026-10-08-stg-store-sync-design.md` §4.2 (lookup) and §6 (write
> model). The add-only write model and "no delete" rule remain authoritative;
> this spec adds a **suppression list** so a row the operator deleted by hand is
> not silently re-added by the next run.
> **Author:** design session

---

## 1. Problem

The tool is add-only (base spec §6): a source record whose match key is absent
from the store is classified `add` and written. Store rows absent from the source
are left untouched — the tool never deletes.

That model has one gap. An operator **can** delete a store row by hand in STG
Studio. After that deletion the key is absent from the store, so the very next
run sees the key in the source, absent from the store, and **re-adds the row the
operator just deleted**. The automation fights the operator, and the deletion
never sticks.

There is no signal that distinguishes "this key has never been synced" (a
genuine `add`) from "this key was synced, then deleted on purpose" (a re-add to
suppress). The `tracked` table (`core/state.py`) already records every match key
the system has written, so the missing row **is** detectable: a tracked key that
is no longer in the store was deleted by hand.

## 2. Intent (design brief)

**For:** the survey-ops operator who curates a Subject Store by hand as well as
by automation.
**Outcome:** a store row the operator deletes by hand stays deleted; the next run
does not re-add it.
**Success:** the operator deletes a row in STG Studio; the next run (manual or
scheduled, dry-run or live) neither re-adds the row nor counts it as an `add`;
the operator can see the suppressed keys and can undo a suppression.

**Constraints (agreed in this session):**

1. **Automatic, with correction.** A key enters the blacklist automatically when
   a previously-tracked key disappears from the store; the operator can also add
   or remove keys by hand.
2. **Per job.** The blacklist is scoped to a job name, mirroring `tracked` and
   `proposals`.
3. **Suppress `add` only, auto-clear on reappearance.** A blacklisted key is
   never `add`ed. If the key reappears in the store (the operator re-added it),
   the blacklist entry is cleared and the key is planned normally again.
4. **Recorded on dry-run too.** Auto-detection mutates only the local SQLite
   state, never the store, so it runs on dry-run as well — keeping the dry-run
   plan honest (the deleted row does not show up as a planned `add`).
5. **A separate list, not a conflict.** Suppression is an expected, silent
   outcome — it is not a run error and does not change the run status.

## 3. Data model

### 3.1 New table `blacklist` (`core/state.py`)

```sql
CREATE TABLE IF NOT EXISTS blacklist (
    job         TEXT NOT NULL,
    match_value TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT 'deleted',
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (job, match_value)
);
```

`reason` is `"deleted"` for an auto-detected key and `"manual"` for one the
operator added. It is display-only. Scoped by `job`, exactly like `tracked` and
`proposals`; a job rename orphans its blacklist rows the same way it orphans
`tracked` rows (accepted, consistent with the existing model).

New methods on `State`:

- `blacklist_key(job, match_value, *, reason="manual") -> None` — upsert.
- `unblacklist_key(job, match_value) -> None`.
- `blacklisted_keys(job) -> set[str]`.
- `list_blacklist(job) -> list[dict]` — rows ordered by `match_value`, each
  `{match_value, reason, added_at}`.
- `reconcile_blacklist(job, store_keys) -> tuple[set[str], set[str]]` — the
  auto-detection (§4); returns `(added, removed)`.

### 3.2 `Plan` — suppressed keys (`core/planner.py`)

`Plan` gains `suppressed: list[str]` (match keys skipped because they are
blacklisted). `Plan.counts` is unchanged: counts describe **write actions**, and
a suppressed key is not a write action.

## 4. Auto-detection (`State.reconcile_blacklist`)

Given the job and the set of match-key values currently in the store snapshot:

1. `tracked = tracked_keys(job)` — every key the system has written.
2. `blacklisted = blacklisted_keys(job)`.
3. **added** = `tracked - store_keys` — a tracked key gone from the store was
   deleted by hand. Blacklist each (`reason="deleted"`).
4. **removed** = `blacklisted & store_keys` — a blacklisted key back in the store
   was re-added by hand. Unblacklist it (auto-clear).

`added` and `removed` are disjoint by construction (`added ⊆ ¬store_keys`,
`removed ⊆ store_keys`), so the order of the two writes does not matter. Both
happen in one call; the caller passes the store snapshot it already has.

**Only tracked keys are auto-blacklisted.** A store row the system never wrote
(e.g. a legacy row, or one the operator added then deleted in Studio) is not
detectable as "deleted" — there is no record it ever existed. The manual path
(§6) covers those. This is the deliberate, explainable boundary: the system
suppresses what it knows it wrote.

## 5. Run flow (`core/sync.py`)

After the store snapshot is loaded (step 2, `get_store`) and **before**
`plan()`:

```python
store_keys = {str(r.get(match.store_col)) for r in store_rows if match.store_col in r}
added, removed = state.reconcile_blacklist(config.job, store_keys)
blacklisted = state.blacklisted_keys(config.job)
```

- Runs on dry-run and live runs alike (constraint 4) — it touches only SQLite.
- A store-read failure raises before this point, so "absent from the snapshot"
  can never be a truncated read; the snapshot is the full store.
- `plan(...)` is called with `blacklisted=blacklisted`.
- The `plan` progress event and `done` event carry `suppressed=<n>` (count of
  blacklisted keys skipped).
- `SyncResult` gains `suppressed: int`. Suppression does **not** set
  `partial`/`error`; it is silent and expected.

`reconcile_blacklist` is best-effort relative to the run's safety: a failure to
persist a blacklist entry must not crash a run, but it also must not silently
drop the suppression. It is called before `plan`, so any exception propagates
into the existing top-level `except` (run `error`) rather than producing a plan
that ignores the blacklist.

## 6. Planner (`core/planner.py`)

`plan(...)` gains a keyword argument:

```python
blacklisted: set[str] | None = None
```

In the `add` loop (`src_keys - store_keys`), a key present in `blacklisted` is
skipped and appended to `p.suppressed` instead of becoming an `add` action. The
`update`/`same` paths are untouched — auto-clear (§4) has already removed any
blacklist entry for a key that is back in the store, so a blacklisted key never
reaches the intersection loop. Passing `blacklisted=None` (the default) preserves
today's behaviour exactly.

## 7. Operator surfaces

### 7.1 GUI — "Blacklist" tab (`ui/job_detail.py`)

A new tab between **Conflicts** and **Proposed updates**:

- A read-only table (`_DictTableModel`): columns **Match**, **Reason**, **Added**.
- **Remove selected** — unblacklist the selected key(s); the "correction" path.
- A small **Add key** row (line edit + button) — blacklist a key by hand
  (`reason="manual"`).

Signals `blacklist_add_requested(str)` and `blacklist_remove_requested(list)`
are re-exported by `MainWindow` and wired in `Controller` to `State`; both then
call the existing `_load_blacklist(name)` refresh (mirrors `_load_proposals`).
`Controller._open_job` / `_new` load the tab alongside History / Conflicts /
Proposals.

### 7.2 CLI (`cli.py`)

Three subcommands, mirroring the registry-backed shape:

```
stg-store-sync-cli blacklist <name>            # list suppressed keys
stg-store-sync-cli blacklist-add <name> <key>  # add a manual entry
stg-store-sync-cli blacklist-remove <name> <key>
```

`blacklist` prints `key · reason` per row. Exit codes follow the existing
convention: `0` ok, `3` unknown job.

`run`/`run-all` `--json` output gains `"suppressed": <n>`.

## 8. Tests (TDD)

- `state`: `blacklist_key` / `unblacklist_key` / `blacklisted_keys` /
  `list_blacklist` round-trip; `reconcile_blacklist` blacklists a tracked key
  missing from the store, unblacklists a blacklisted key present in the store,
  and leaves an untracked key alone.
- `planner`: a blacklisted key in the source is skipped (no `add` action) and
  recorded in `suppressed`; a non-blacklisted key is unaffected; a blacklisted
  key that is also in the store is planned normally (`same`/`update`);
  `blacklisted=None` is the current behaviour.
- `sync`: a tracked-then-deleted key is not re-added on the next run; the run
  status is `ok` (not `partial`); `SyncResult.suppressed` is correct; the same
  holds under dry-run (blacklist recorded, plan suppressed, no write).
- `sync`: a blacklisted key reappearing in the store clears the entry and is
  planned normally.
- `ui`: the Blacklist tab renders rows; add/remove signals fire; the controller
  persists a manual add and a remove.
- `cli`: `blacklist` lists; `blacklist-add`/`blacklist-remove` mutate; unknown
  job exits `3`; `run --json` includes `suppressed`.

## 9. Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| A genuine new row is suppressed because its key was deleted earlier | Med | Only tracked keys are auto-suppressed; the operator sees the tab and can Remove. A key that is truly new was never tracked, so it is not auto-suppressed. |
| Operator deletes a row they actually want re-synced | Low | Remove the key in the Blacklist tab (or CLI `blacklist-remove`); or re-add the row in STG (auto-clear). |
| A failed store read looks like a mass deletion | High | `get_store` raises before reconciliation; a truncated read never reaches it. Only a full snapshot drives `tracked - store_keys`. |
| Blacklist grows unbounded | Low | Bounded by the number of hand-deleted rows; auto-cleared on reappearance. |
| Rename a job → orphaned blacklist rows | Low | Consistent with `tracked`/`proposals`; out of scope here. |

## 10. Out of scope (this iteration)

- Detecting deletions of rows the system never wrote (manual path only).
- A shared (per-store) blacklist across jobs.
- Blacklisting updates or reads (suppression applies to `add` only).
- Expiry / TTL on blacklist entries.
- Surfacing the blacklist on the dashboard.
