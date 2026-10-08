# Progress — Registry + momo-style GUI redesign

Plan: [`2026-10-08-gui-registry-redesign.md`](2026-10-08-gui-registry-redesign.md)
Spec: [`../specs/2026-10-08-gui-registry-redesign-design.md`](../specs/2026-10-08-gui-registry-redesign-design.md)

Branch: `feat/phase1-skeleton` (base `a87e855`, the plan commit).

## Status: COMPLETE

All 18 plan tasks plus a GUI-polish pass are implemented, reviewed clean, and
committed. Final suite: **288 passed, 0 warnings** (`-W error` clean).

## Per-task ledger

| Task | Scope | Commits | Review |
| ---: | :--- | :--- | :--- |
| 1 | writer: full-column payloads | `a87e855`..`f086b25` | clean |
| 2 | planner: source-side dups skip, store-side halts | `f086b25`..`1a5b6f1` | clean (1 fix round) |
| 3 | sync: readiness gate + pull window | `1a5b6f1`..`4037ef4` | clean (1 fix round) |
| 4 | writer/sync: transport-safe runs, one-shot finish | `4037ef4`..`5f8e2af` | clean (1 fix round) |
| 5 | nits: writer id col, empty-store preflight, mapping half-rows, CLI exit code | `5f8e2af`..`83805cf` | clean |
| 6 | `Registry` — schema + config CRUD | `83805cf`..`0f57ac7` | clean |
| 7 | `sync`: cooperative cancellation | `0f57ac7`..`d2f0d92` | clean |
| 8 | `RunQueue` — single global worker, dedupe, stop | `d2f0d92`..`7641174` | clean (1 fix round) |
| 9 | `Scheduler` — minute-granular cron | `7641174`..`9a2fddc` | clean |
| 10 | UI models | `9a2fddc`..`c9e2e1c` | clean |
| 11 | Dashboard (table + stats + queue strip) | `c9e2e1c`..`1d3967b` | clean (1 fix round) |
| 12 | Store detail (model-bound binding grid) | `1d3967b`..`61904d3` | clean (1 fix round) |
| 13 | Run detail pane + new/edit dialog | `61904d3`..`2a051f9` | clean (1 fix round) |
| 14 | Qt threads (RunThread / SchedulerThread) | `2a051f9`..`e6ea789` | clean (1 fix round) |
| 15 | Stacked shell (dashboard/detail/run) | `e6ea789`..`fb12084` | clean |
| 16 | `Controller` — registry + queue + scheduler wiring | `fb12084`..`25f6176` | clean (2 fix rounds) |
| — | GUI polish — shared `DESIGN.md` theme | `25f6176`..`a2e7dfd` | clean (1 fix round) |
| 17 | Registry-backed CLI (`list`/`run`/`run-all`/`stop`) | `a2e7dfd`..`d420915` | APPROVED |
| 18 | Whole-suite verification + docs | (this doc) | — |

Phase gates: A (1–5) 198 · B/C (6–9) 230 · D (10–16) 264 · polish 280 · E (17) 288.

## Task 18 verification

### Full suite

```
$ ./.venv/bin/python -m pytest -q
........................................................................ [ 25%]
........................................................................ [ 50%]
........................................................................ [ 75%]
........................................................................ [100%]
288 passed in 4.10s
```

Warnings-as-errors clean:

```
$ ./.venv/bin/python -m pytest -q -W error
288 passed in 4.10s
```

### Offscreen GUI smoke

`main()` blocks on `exec`, so the construct-then-close check is used:

```
$ QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c "
from PySide6.QtWidgets import QApplication
from store_sync.ui.main_window import MainWindow
app = QApplication.instance() or QApplication([])
w = MainWindow(); w.show(); w.close(); print('smoke-ok')"
smoke-ok
```

No `QThread: Destroyed while running` warnings.

## Design system

`DESIGN.md` (commit `9b3c5ae`) mirrors the sibling `stg_automation_suite`
token set plus store-sync-specific components (stat-card, queue-strip,
action-rail, run-summary, status-cancelled). `src/store_sync/ui/theme.py` is the
single styling source; widgets set object names only.

## Deferred (non-blocking) — for the whole-branch review

Collected from the per-task reviews; none blocks the redesign:

- Task 1: unasserted full-row `same` payload; `sync.store_columns` wiring untested.
- Task 3: `_readiness_errors` duplicates preflight logic (spec wanted a shared
  `check_ready`); store-dup match key trips both readiness and planner blocked.
- Task 5: CLI partial/error collapse (now resolved by Task 17 per spec §7).
- Task 6: `update` rename orphaning run history; `match=None` round-trips to `("","")`.
- Task 7: dry-run `apply_plan` returns before the loop (should_stop unpolled).
- Task 9: cron DOW/range/list/step paths untested; naive local-clock schedules.
- Task 13: `DELETE_POLICIES`/`_dict_view`/int helpers duplicated across
  `dialogs.py`/`job_detail.py`/`main_window.py`.
- Task 14: `RunThread` possibly dead code (production worker is `RunQueue`);
  `SchedulerThread.due` — only one enqueue path wired (correct).
- Task 16: `delete()`/`list_entries()` can raise post-stop; `dashboard.schedules_requested`
  unwired; `RunDetail`/history/conflicts partly inert; save add-vs-update name asymmetry.
- GUI-polish: dead `_T["heading_size"]`; theme docstring "mirrored 1:1" with 3 extras.
- Task 17: dead `DEFAULT_STATE` const; `Registry` conn leak if `State(db)` ctor
  raises; dead `_run_one` KeyError branch + `_worst_code` `1 in codes`.

## Rollout next step (optional)

Controlled real write / adoption for store `17417` — requires adding an `SbjNum`
column in STG Studio first. Disposable verification store: `17620`.
