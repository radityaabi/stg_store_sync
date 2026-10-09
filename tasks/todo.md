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
- [x] 1.2 `core/config.py` — job schema + YAML load/save + validation (17 tests)
- [x] 1.3 `core/stg_client.py` — read+write REST methods (mock-tested, 25 tests)

## Checkpoint: Skeleton
- [x] `pytest` green (42 tests); client mock-covered; config validation proven

## Phase 2 — Binding gate
- [x] 2.1 `core/binding.py` — alias pairs + `strict` + auto-suggest + match-key test + id-column declaration check (exists/uniqueness/no-op probe)
- [x] 2.2 Preflight orchestration — review table from live store + sample export + id-column check

## Checkpoint: Binding
- [x] alias pairs human-approved; `strict` blocks on mismatch; id column verified

## Phase 3 — Data
- [x] 3.1 `core/fetcher.py` — pull + `variableNames` filter + chunk + retry + EAV flatten (empty→`""`) + verify + project
- [x] 3.2 `core/planner.py` — diff by match key → add/update/delete/conflicts (fail-closed); empty is a value

## Checkpoint: Data
- [ ] Dry-run plan for real job shows only expected classifications

## Phase 4 — Adoption + Write
- [x] 4.1 `core/state.py` — SQLite: jobs, run log, conflicts, hashes
- [x] 4.2 Legacy `SbjNum` adoption (1:1 only; quarantine ambiguity)
- [x] 4.3 `core/writer.py` — apply + full-row payloads + throttle + retry + re-read verify
- [x] 4.4 `core/sync.py` — orchestrator (index → pull → load → plan → apply → state)
## Checkpoint: Write
- [ ] End-to-end dry-run yields correct plan, zero writes
- [ ] One controlled real write (add/update/delete) verified
- [ ] Human review before enabling full sync

## Phase 5 — GUI
- [x] 5.1 `ui/main_window.py` — jobs, binding editor (2 fields; pull term derived read-only), preflight, Approve dialog, Sync, dry-run, log, conflicts
- [x] 5.2 `ui/mapping_io.py` — Excel template download + upload/validate the column mapping

## Checkpoint: GUI
- [ ] `gui.py` launches; full flow exercised on real job in dry-run

## Phase 6 — Scheduled (optional)
- [x] 6.1 `cli.py` + filelock — headless sync + run-due + audit log

## Phase 7 — Registration & schedule redesign ✅ COMPLETE
> Spec: `docs/superpowers/specs/2026-10-08-registration-and-schedule-redesign-design.md`
> Plan: `docs/superpowers/plans/2026-10-08-registration-and-schedule-redesign.md`
- [x] 1 Registration without a window — `SourceConfig` = `{survey_id}`; `window_days`/`date_type` gone; pulls always full history
- [x] 2 `Schedule` model + registry storage — `Schedule{unit, every, at, weekday}` as 4 columns; legacy cron → manual
- [x] 3 Scheduler + `human()` — `is_due` ordinal/iso-week rules; human labels
- [x] 4 UI + CLI surfaces — unit/every/at/weekday editors; labels in dashboard/overview/CLI
- [x] 5 GUI integration + coverage — schedule tab wiring; visibility tests; 335 tests green

## Phase 8 — Manual update approval + delete removal ✅ COMPLETE
> Plan: `docs/superpowers/plans/2026-10-09-manual-update-approval.md`
> Supersedes base spec §6 + §7.3 (delete-policy selector).
> Automation = AddRow only. Update = proposal + operator Apply (compare-and-swap).
> Delete = removed entirely (no policy, no option, no API call).

- [x] 8.0 (prereq) Fix `test_run_missing_creds_is_1` isolation (`.env` + `load_dotenv`)
- [x] 8.1 Spec + vocabulary delta — base spec §6/§7.3, `AGENTS.md` invariants, `CONTEXT.md`
- [x] 8.2 `core/config.py` — drop `DeletePolicy` + `WriteConfig.delete_policy`
- [x] 8.3 `core/planner.py` — per-column `diffs`, proposal-only update, no delete
- [x] 8.4 `core/writer.py` — add-only `apply_plan` + CAS `apply_updates`
- [x] 8.5 `core/state.py` — `proposals` table + methods
- [x] 8.6 `core/sync.py` — apply adds, record proposals, no delete
- [x] 8.7 `core/registry.py` — drop `delete_policy` column (+ migration)
- [x] 8.8 `core/stg_client.py` — remove `delete_row`/`delete_all_rows`
- [x] 8.9 `ui/job_detail.py` + `ui/dialogs.py` + `ui/models.py` — proposals review panel; drop delete-policy combo (both surfaces)
- [x] 8.10 `gui.py` — wire apply/dismiss handlers; drop `delete_policy` from `_job_from_detail`
- [x] 8.11 `cli.py` — add-only + proposal count
- [x] 8.12 End-to-end acceptance on `DUMMY_AUTO_STORE`
- [x] 8.13 (prereq) Fix `test_run_missing_creds_is_1` isolation (`.env` + `load_dotenv`)

## Out of scope (this iteration)
- Multi-store fan-out; auto-mapping without approval; real-time streaming
