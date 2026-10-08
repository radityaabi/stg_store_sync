# Live Dry-Run Acceptance — Link1 → Gran_Turismo_Link2 (17417)

> **Date:** 2026-10-08
> **Mode:** `dry_run=True` against the live API (zero writes).
> **Script:** `/tmp/opencode/live_dryrun.py`

## Result

```
store rows before: 295
store rows after : 295
status           : ok
counts           : {'add': 296}
plan counts      : {'add': 296, 'update': 0, 'same': 0, 'delete': 0, 'conflict': 0}
dry_run          : True
write attempts   : []          # client write methods wrapped to raise on call
errors           : []

DRY-RUN ZERO-WRITE OK: True
```

The orchestrator ran the full §7.2 flow (survey index → derived-filter pull →
GetStore → plan) and made **no write attempt** (verified by wrapping
`add_row`/`update_row`/`delete_row`/`delete_all_rows` to raise).

## Why 296 ADDs is correct (not a bug)

Store 17417 currently has columns `['I7_1_2', 'I8a_1', 'No', 'Q32', 'uniqueID']`
— **there is no `SbjNum` column**. The match key `SbjNum` therefore cannot be
found in any store row, so every pulled subject classifies as ADD. This is the
**adoption-not-done** state described in spec §4.5.

Correct next step for this store: add the `SbjNum` column in STG Studio, then
run the one-time adoption (`core/adoption.py`) — 1:1 content match writes each
legacy row's `SbjNum`; ambiguous rows are quarantined. Adoption is intentionally
**not** wired into the GUI/CLI (highest-risk step, interactive, dry-run first).

## What this proves

- The pull returns all 296 subjects with the derived `variableNames` filter.
- The planner classifies purely by key-set difference (no `SbjNum` in store →
  all adds), never by position/timestamp.
- Dry-run writes nothing, end to end, on a real production store.
