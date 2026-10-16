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
- [x] 2.2 Readiness gate — per-column review + id-column check + match-key test over live store + pull

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
- [x] 5.1 `ui/main_window.py` — jobs, binding editor (2 fields; pull term derived read-only), Sync, dry-run, log, conflicts
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

## Phase 9 — Survey pairing out of the run ✅ COMPLETE
> Spec: `docs/superpowers/specs/2026-10-09-survey-pairing-separation-design.md`
> Plan: `docs/superpowers/plans/2026-10-09-survey-pairing-separation.md`
> The survey definition (`GetSurveyXML`) is read once, by the GUI Pairing action
> (prep); runs read the cached index and block when unpaired/stale. No implicit
> XML fetch, no full-export fallback.

- [x] 9.1 `core/config.py` — `ColumnBinding.pull_term` + `SourceConfig.survey_index`/`paired_survey_id`
- [x] 9.2 `core/registry.py` — persist pairing fields + migrate legacy rows
- [x] 9.3 `core/fetcher.py` — `pair_columns` helper from a cached index
- [x] 9.4 `core/sync.py` — `run_sync` reads the cached index, drops the XML fetch, adds the pairing gate
- [x] 9.5 `core/sync.py` — readiness gate reuses the cached index + unpaired error (no separate preflight pass)
- [x] 9.6 `ui/job_table_model.py` + `ui/job_detail.py` — stored pull term + Pairing button
- [x] 9.7 `gui.py` + `ui/main_window.py` — pairing worker, wiring, carry pairing on save
- [x] 9.8 Docs (invariants, vocabulary, README, ledger) + full acceptance

## Phase 10 — Blacklist (deleted rows stay deleted) ✅ COMPLETE
> Spec: `docs/superpowers/specs/2026-10-12-blacklist-deleted-rows-design.md`
> Plan: `docs/superpowers/plans/2026-10-12-blacklist-deleted-rows.md`
> A hand-deleted store row is suppressed from re-add; auto-detected from
> `tracked`, per job, correctable in the GUI tab and the CLI.

- [x] 10.1 `core/state.py` — `blacklist` table + `reconcile_blacklist`
- [x] 10.2 `core/planner.py` — `Plan.suppressed` + `blacklisted` param
- [x] 10.3 `core/sync.py` — reconcile before plan; `SyncResult.suppressed`
- [x] 10.4 `ui/job_detail.py` + `ui/main_window.py` + `gui.py` — Blacklist tab
- [x] 10.5 `cli.py` — `blacklist` / `blacklist-add` / `blacklist-remove`
- [x] 10.6 Docs — vocabulary, invariant 13, base-spec note

## Phase 11 — OS-level scheduler ✅ COMPLETE
> Spec: `docs/superpowers/specs/2026-10-12-os-scheduler-design.md`
> Plan: `docs/superpowers/plans/2026-10-12-os-scheduler.md`
> Jobs run on their per-job schedule with the GUI closed, via one OS task per
> job (Windows `schtasks` / Linux `crontab`) that wakes `run-due --job`.

- [x] 11.1 `core/state.py` — `os_fired` minute marker
- [x] 11.2 `core/os_scheduler.py` — `plan` schedule→trigger
- [x] 11.3 `core/os_scheduler.py` — schtasks/crontab backends + add/remove/status/sync
- [x] 11.4 `cli.py` — `run-due` + CWD-independent `.env`
- [x] 11.5 `cli.py` — `schedule list/add/remove/sync`
- [x] 11.6 GUI — OS badge, Sync action, best-effort auto-sync
- [x] 11.7 Docs — invariant 14, vocabulary, ledger, README

## Phase 12 — Run notification email ✅ COMPLETE
> Spec: `docs/superpowers/specs/2026-10-15-run-notification-email-design.md`
> Plan: `docs/superpowers/plans/2026-10-15-run-notification-email.md`
> After any run that added rows, mail an HTML summary to the job's PIC list,
> over OWA via Playwright (no SMTP) — the same approach as the `momo` tool.

- [x] 12.1 `core/pic.py` — `;`-separated PIC list helpers
- [x] 12.2 `core/owa_mailer.py` — Playwright/OWA transport (ported)
- [x] 12.3 `core/state.py` — `notifications` table + accessors
- [x] 12.4 `core/config.py` + `core/registry.py` — per-job `pic`
- [x] 12.5 `core/notify.py` — trigger rules, HTML report, send
- [x] 12.6 `core/sync.py` — notify after a run, contained
- [x] 12.7 `cli.py` — `send-report`, `--no-notify`, report status
- [x] 12.8 GUI — PIC field + per-run notification line
- [x] 12.9 Docs — invariant 15, vocabulary, README, `[owa]` extra

## Phase 13 — One pending proposal per key ✅ COMPLETE
> A later run re-proposing a key **supersedes** the earlier still-pending row
> (`status='superseded'`) instead of inserting a duplicate, so the GUI queue
> shows each key once — at its latest `old → new`. Fixes repeated/duplicated
> rows in the Proposed-updates tab across runs.

- [x] 13.1 `core/state.py` — `record_proposal` supersedes prior pending rows for the key
- [x] 13.2 Tests — `test_state.py` supersede cases; suite green (532)
- [x] 13.3 Docs — invariant 3, base-spec §6 note, `CONTEXT.md` vocabulary

## Phase 14 — Per-job email on/off ✅ COMPLETE
> The run notification is now an operator choice: a per-job `notify_enabled`
> toggle (GUI checkbox, default ON) gates the mail, on top of the existing
> `--no-notify` invocation flag. Off = no mail, run unchanged.

- [x] 14.1 `core/config.py` — `JobConfig.notify_enabled` (default True, omitted when True)
- [x] 14.2 `core/registry.py` — `jobs.notify_enabled` column + migration (default 1), `JobEntry`
- [x] 14.3 `core/sync.py` — mail only when `notify and config.notify_enabled`
- [x] 14.4 `ui/job_detail.py` + `gui.py` — Config-tab checkbox; `config_fields`/`_job_from_detail`
- [x] 14.5 Tests — config round-trip, registry migration, sync gate, checkbox (543 green)
- [x] 14.6 Docs — invariant 15, spec §10, `CONTEXT.md`
- [x] 14.7 Fixed pre-existing shadowed `TestNotificationWiring` (4 tests uncollected)

## Out of scope (this iteration)
- Multi-store fan-out; auto-mapping without approval; real-time streaming
