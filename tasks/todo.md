# TODO: STG Store Sync

> Plan: `tasks/plan-stg-store-sync.md`
> Spec: `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md`
> Repo: `stg-store-sync` (new, standalone)

## Phase 0 — De-risk (blocks everything)
- [ ] 0.1 Confirm `UpdateRow` row-selector + payload semantics (probe)
- [ ] 0.2 Confirm `DeleteRow` semantics + `userTag` mapping (probe)
- [ ] 0.3 Confirm store accepts a new `SbjNum` column
- [ ] 0.4 Remove dummy row `uniqueID=999999` from store 17417

## Checkpoint: Phase 0
- [ ] All probes documented; spec §10 unknowns answered
- [ ] Human review before any product code

## Phase 1 — Skeleton
- [ ] 1.1 Repo scaffold (`pyproject`, README, `.env.example`, `.gitignore`, package, tests)
- [ ] 1.2 `core/config.py` — job schema + YAML load/save + validation
- [ ] 1.3 `core/stg_client.py` — read+write REST methods (mock-tested)

## Checkpoint: Skeleton
- [ ] `pytest` green; client mock-covered; config validation proven

## Phase 2 — Binding gate
- [ ] 2.1 `core/binding.py` — strict parity + auto-suggest + key-validity test
- [ ] 2.2 Preflight orchestration — review table from live store + sample export

## Checkpoint: Binding
- [ ] `strict` blocks on mismatch; `mapped` needs approval; table verified

## Phase 3 — Data
- [ ] 3.1 `core/fetcher.py` — pull + chunk + retry + EAV flatten
- [ ] 3.2 `core/planner.py` — diff → add/update/delete/conflicts (fail-closed)

## Checkpoint: Data
- [ ] Dry-run plan for real job shows only expected classifications

## Phase 4 — Adoption + Write
- [ ] 4.1 `core/state.py` — SQLite: jobs, run log, conflicts, hashes
- [ ] 4.2 Legacy `SbjNum` adoption (1:1 only; quarantine ambiguity)
- [ ] 4.3 `core/writer.py` — apply + throttle + retry + verify-before-add

## Checkpoint: Write
- [ ] End-to-end dry-run yields correct plan, zero writes
- [ ] One controlled real write (add/update/delete) verified
- [ ] Human review before enabling full sync

## Phase 5 — GUI
- [ ] 5.1 `ui/main_window.py` — jobs, preflight, Approve dialog, Sync, dry-run, log, conflicts

## Checkpoint: GUI
- [ ] `gui.py` launches; full flow exercised on real job in dry-run

## Phase 6 — Scheduled (optional)
- [ ] 6.1 `cli.py` + filelock — headless sync + run-due + audit log

## Out of scope (this iteration)
- Multi-store fan-out; auto-mapping without approval; real-time streaming
