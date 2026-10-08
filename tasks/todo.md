# TODO: STG Store Sync

> Plan: `tasks/plan.md`
> Spec: `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md`
> Probe findings: `docs/probe/2026-10-08-fase0-findings.md`
> Repo: `stg-store-sync` (standalone)

## Phase 0 — De-risk ✅ COMPLETE
- [x] 0.1 `UpdateRow` = upsert on the store id column; full-row payload; id immutable
- [x] 0.2 `DeleteRow userTag` = id-column value, in the **path**; always `{Done:true}`
- [x] 0.3 New column via API → **rejected**; must be pre-defined in Studio
- [x] 0.4 Remove dummy row `uniqueID=999999` from store 17417 (back to 295)
- [x] End-to-end add/update/delete cycle validated on `DUMMY_AUTO_STORE` (17620)

## Checkpoint: Phase 0
- [x] All probes documented; spec §10 unknowns answered
- [ ] Human review before any product code

## Phase 1 — Skeleton
- [x] 1.1 Repo scaffold (`pyproject`, README, `.env.example`, `.gitignore`, package, tests)
- [ ] 1.2 `core/config.py` — job schema + YAML load/save + validation
- [ ] 1.3 `core/stg_client.py` — read+write REST methods (mock-tested)

## Checkpoint: Skeleton
- [ ] `pytest` green; client mock-covered; config validation proven

## Phase 2 — Binding gate
- [ ] 2.1 `core/binding.py` — alias pairs + `strict` + auto-suggest + match-key test + id-column verify
- [ ] 2.2 Preflight orchestration — review table from live store + sample export

## Checkpoint: Binding
- [ ] alias pairs human-approved; `strict` blocks on mismatch; id column verified

## Phase 3 — Data
- [ ] 3.1 `core/fetcher.py` — pull + chunk + retry + EAV flatten
- [ ] 3.2 `core/planner.py` — diff by match key → add/update/delete/conflicts (fail-closed)

## Checkpoint: Data
- [ ] Dry-run plan for real job shows only expected classifications

## Phase 4 — Adoption + Write
- [ ] 4.1 `core/state.py` — SQLite: jobs, run log, conflicts, hashes
- [ ] 4.2 Legacy `SbjNum` adoption (1:1 only; quarantine ambiguity)
- [ ] 4.3 `core/writer.py` — apply + full-row payloads + throttle + retry + re-read verify

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
