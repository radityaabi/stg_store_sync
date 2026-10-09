# Acceptance — Manual Update Approval + Delete Removal

> Date: 2026-10-09
> Plan: `docs/superpowers/plans/2026-10-09-manual-update-approval.md`
> Spec: `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md` §6

## Scope

End-to-end verification of the new write model against a fake STG store (the
`DUMMY_AUTO_STORE` stand-in — no live writes). Automation = **AddRow only**;
update = **proposal + operator Apply (compare-and-swap)**; delete **removed**.

## Evidence

Automated: `tests/core/test_acceptance_update_approval.py` (4 tests).

```
$ ./.venv/bin/python -m pytest -q tests/core/test_acceptance_update_approval.py
....                                                                     [100%]
4 passed
```

| # | Scenario | Expected | Result |
|---|----------|----------|--------|
| 1 | Run with 1 new + 1 changed + 1 absent row | add 104 written; 102 left as-is; 1 pending proposal; 103 untouched | ✅ |
| 2 | Apply the proposal (store unchanged) | `update_row` writes `BOB`/`99`; proposal → `applied` | ✅ |
| 3 | Operator edits the store cell before Apply | proposal `stale`, **no write**, manual value preserved | ✅ |
| 4 | Short pull (1 of 3 rows) | store still has all 3 rows; no `delete_row` | ✅ |
| 5 | Dry-run | no writes, no proposals recorded | ✅ |

## Full suite

```
$ ./.venv/bin/python -m pytest -q
384 passed

$ ./.venv/bin/python -m pytest -q -W error
384 passed
```

## Invariants confirmed

- **Add-only automation** — a run never calls `update_row`; `writer.apply_plan`
  handles `add` actions only.
- **Proposal only for a real diff** — identical rows are `same`; no proposal.
- **Compare-and-swap** — a store row changed since the proposal is skipped
  (`stale`) and reported; unrelated manual edits to non-diffed columns survive.
- **No delete** — `STGClient` has no `delete_row`/`delete_all_rows`; the planner
  emits no `delete` action; absent rows are untouched.

## Residual (not covered here)

- Live acceptance against a real STG `DUMMY_AUTO_STORE` (operator-run, dry-run
  first) — pending credentials + a maintenance window.
