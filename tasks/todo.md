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

## Phase 15 — Live add progress ✅ COMPLETE
> During a live run the run console shows a single `adding rows: n/N` line that
> climbs as adds are written, instead of only an end-of-run count.

- [x] 15.1 `core/writer.py` — `apply_plan(on_add=...)` fires after each applied add
- [x] 15.2 `core/sync.py` — forward as an `add` progress event (`done`/`total`)
- [x] 15.3 `ui/job_detail.py` — `adding rows: n/N`, updated in place (one line)
- [x] 15.4 Tests — writer callback, sync event, in-place UI line (551 green)
- [x] 15.5 Docs — GUI redesign spec §5.2.1

## Phase 16 — Per-job run log ✅ COMPLETE
> The live run log is now per job: each progress event is tagged with its job,
> the controller buffers one log per job, and only the open job's log renders.
> Job A's run never bleeds into job B's panel; opening a running job replays its
> log. Header status is prefixed with the job name.

- [x] 16.1 `core/sync.py` — tag every progress event with `job`
- [x] 16.2 `gui.py` — per-job progress buffer; render only the open job's log
- [x] 16.3 `gui.py` — `_replay_progress` on open/new; header prefixed with job
- [x] 16.4 Tests — event tagging, no cross-job bleed, replay, idle clears (555 green)
- [x] 16.5 Docs — GUI redesign spec §5.2.2

## Out of scope (this iteration)
- Multi-store fan-out; auto-mapping without approval; real-time streaming

## Phase 17 — One-click column auto-map ✅ COMPLETE
> The ⚡ **Auto-map columns** button in the Columns tab fills the grid in one
> click: store columns from a single `GetStore` + the survey index (cached
> pairing reused, else one XML fetch — pairing stored implicitly). Matching is
> exact name, then a question base name with exactly one answer var; ambiguous
> or unknown columns stay blank and are reported. Merge / Replace / Cancel when
> the grid is non-empty. Nothing is saved until Save.

- [x] 17.1 `core/automap.py` — `auto_map(store_cols, index, skip, existing, merge)`
- [x] 17.2 `ui/job_detail.py` + `ui/main_window.py` — ⚡ button + `automap_requested`
- [x] 17.3 `gui.py` — off-GUI worker, merge/replace/cancel dialog, implicit pairing
- [x] 17.4 Tests — core rules, button signal, controller merge/replace/cancel/empty (578 green)
- [x] 17.5 Docs — README feature line, ledger

## Phase 16 — Concurrent runs ✅ COMPLETE
> Spec: `docs/superpowers/specs/2026-10-17-concurrent-runs-design.md`
> Plan: `docs/superpowers/plans/2026-10-17-concurrent-runs.md`
> Runs of different jobs execute in parallel; one run per job, never two against
> one store, per-job stop, multi-active queue display.

- [x] 16.1 `core/run_queue.py` — one thread per run, `QueueState.active: list[str]`
- [x] 16.2 `core/run_queue.py` — same-store refusal
- [x] 16.3 `core/state.py`/`registry.py` — 30 s SQLite lock timeout
- [x] 16.4 `core/scheduler.py` — enqueue carries the store key
- [x] 16.5 `gui.py`/`ui/dashboard.py` — multi-active status, per-run toasts, store-key enqueue
- [x] 16.6 `gui.py`/`ui/main_window.py` — per-job stop
- [x] 16.7 Docs — invariant 10/11, vocabulary, ledger

## Phase 18 — Scale: 1–2k row runs ✅ COMPLETE
> Trigger: a 1569-interview dry run (`add 0, update 2, same 1567`) was slow.
> Measured first (`/tmp` harness: pipeline CPU, per-request latency, SQLite
> fsyncs), then fixed only what the numbers indicted. Network, not CPU, is the
> floor: the local pipeline is ~0.35 s for 1569 rows.

- [x] 18.1 `core/binding.py` — `suggest_aliases` built `set(src_vals)` inside the
  (store_col × source_var) loop: 25 740 rebuilds of ~1.5k values → **6.4 s → 3.2 s**
- [x] 18.2 `core/preflight.py` + `core/sync.py` — `suggestions=False` on the in-run
  gate (it reads only `errors`): run path **0.72 s → 0.35 s**
- [x] 18.3 `core/state.py` — `State.transaction()`; one fsync per row was
  **6.0 s / 1569 rows** → **0.01 s**, durability unchanged (one transaction)
- [x] 18.4 `core/stg_client.py` — `STG_EXPORT_CHUNK` (default 100); 1569 ids is
  16 sequential exports, and chunk dominates wall-clock
- [x] 18.5 `core/stg_client.py`/`fetcher.py`/`sync.py`/`ui/job_detail.py` — live
  pull progress (`pulling interview data: 500/1569`) via `on_chunk`
- [x] 18.6 Tests — 612 green (4 new state/transaction, 6 preflight/chunk, 2 UI);
  both `pytest -q` and `pytest -q -W error`
- [x] 18.7 Docs — README "Tuning a large survey", `.env.example`

## Phase 19 — Pull concurrency + honest pull cost ✅ COMPLETE
> Trigger: on the VDI (different network from the dev box) a live 1572-interview
> run took **307 s** with `counts={}` — i.e. no writes at all, pure reading.
> Same job on the dev box: **68.8 s**, of which `pull` was **67.0 s** (16
> `SimpleExport` requests, ~4.2 s each) and every other stage totalled 1.7 s.
> Per-round-trip latency, not CPU, is the floor — and the VDI is ~4x the dev
> box per round-trip.

- [x] 19.1 Probe (read-only) — server refuses >100 subject IDs per request:
  `200 {"Error": "IncorrectInput", "Description": "Incorrect Input: Too many
  Subject IDs"}` at 101. Not an empty `Subjects` list, but our shape helpers
  turned it into one — see 19.4.
- [x] 19.2 `core/stg_client.py` — parallel chunk pull on a thread pool
  (`STG_EXPORT_WORKERS`, default 4, clamped 1–8); results reassembled in the
  caller's id order, retry per chunk, `Cancelled` still honoured before dispatch.
  Measured: 82.1 s → 26.8 s at 4 workers, 25.8 s at 8, 39.8 s at 12 (saturation).
- [x] 19.3 `core/stg_client.py` — chunk clamped to the server ceiling
  (`MAX_EXPORT_CHUNK = 100`) for both the env knob and a per-call override, so no
  setting can turn a pull into a silent no-op.
- [x] 19.4 `core/fetcher.py` — `PullResult.fallback_reason` / `seconds` /
  `requests`; `on_fallback(reason)` callback. A fallback is ~15x the payload
  (130.9 s vs 8.7 s per 100 ids) and was previously invisible.
- [x] 19.5 `core/sync.py` — emits `pull_fallback` with the reason, and `pull`
  carries `fell_back` / `seconds` / `requests`.
- [x] 19.6 `ui/job_detail.py` — pull line shows `1572 record(s) (16 request(s),
  67.4s)`; a fallback gets its own timeline line and a warning on the pull line.
- [x] 19.7 `gui.py` — `_close_client` uses the new `STGClient.close()`, which
  closes the shared session and every per-thread worker session.
- [x] 19.8 Tests — **634 green** (22 new: clamp/parallel/order/cancel/fallback
  reason/timing + 4 UI lines), both `pytest -q` and `pytest -q -W error`.
- [x] 19.9 Docs — README "Tuning a large survey" rewritten (concurrency is the
  knob, chunk is a ceiling), `.env.example`, AGENTS.md test count.
- [ ] 19.10 Open — `_as_subjects`/`_as_list`/`_as_ids` still fold a
  `{"Error": ...}` body into `[]`. With the clamp in place the known trigger is
  gone, but the swallow remains for any other STG error shape.
