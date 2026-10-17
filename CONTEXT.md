# STG Store Sync

Glossary of the concepts this tool manipulates. It is a vocabulary, not a spec —
for behaviour, read the specs in `docs/superpowers/specs/`. Definitions describe
what a term **is**; implementation detail is deliberately absent.

## STG objects

**SurveyToGo (STG / Dooblo)**:
The survey platform whose REST API this tool drives.
_Avoid_: the server (ambiguous).

**Source survey**:
The STG survey whose interviews are pulled. One job reads exactly one source
survey.
_Avoid_: source, survey (unqualified).

**Interview (subject)**:
One completed survey response, identified by `SbjNum`. The unit of a store row.
_Avoid_: response, record, submission.

**Subject Store (External List)**:
A dynamic table on the STG server read at survey runtime by External List
questions. The sync target.
_Avoid_: store (unqualified), external list (prefer full name on first use),
list.

**Store row**:
One record in a Subject Store, holding the store's column values.
_Avoid_: entry, record.

**Store column**:
A named field of a Subject Store. Columns must pre-exist — the API cannot create
one.
_Avoid_: field, attribute.

## Identity

**id/tag column**:
The single store column the STG API uses to key `UpdateRow`; its value is
immutable. Declared per job by the operator, never guessed.
_Avoid_: primary key, id (unqualified), key column.

**Match key**:
The per-interview column (`SbjNum`) used to diff source against store — decides
add vs update (proposal). Distinct from the id/tag column.
_Avoid_: key, identity key.

**Tag value**:
The concrete value in a row's id/tag column; `UpdateRow` keys on it.
_Avoid_: id value (ambiguous with match key).

**Adoption**:
The one-time backfill of a legacy store that lacks the match column, matching
rows to source on content and writing the match value only when the match is
1:1. Highest-risk step; never automatic.
_Avoid_: migration, backfill.

## Binding

**Binding**:
An operator-approved agreement on which source variable feeds which store
column (plus the match pairing). Never applied silently.
_Avoid_: mapping (except in "mapping upload", the `.xlsx` I/O of a binding),
link.

**Mapped mode**:
The default binding mode: explicit `source_var → store_col` alias pairs.
_Avoid_: loose, alias mode.

**Strict mode**:
Binding mode that additionally requires `source_var == store_col`; a mismatch
blocks the job.
_Avoid_: exact mode.

**Pull term** (question-level base name):
The token given to `SimpleExport`'s `variableNames` filter (`QPM1`, not
`T_QPM1_1`). Derived, read-only, and an optimisation only — never a required
input.
_Avoid_: filter term, base name (prefer pull term).

**Pairing**:
The one-time prep action that reads the survey definition and records, per
column, the question-level pull term (`QPM1`), plus the cached survey index.
Distinct from a run: it happens at prep, once, not on every sync. An unpaired
job is blocked.
_Avoid_: mapping, bind, sync.

**Projection**:
Selecting the configured columns out of a pulled record into the store shape;
the source of truth after any pull.
_Avoid_: transform.

## Planning & writing

**Plan**:
The classification of every source record and store row into `add` / `update` /
`same` / `conflict` actions, computed by key-set difference (never by position or
timestamp). `add` is written automatically; `update` is a proposal.
_Avoid_: diff, delta.

**Dry-run**:
A run that computes the plan and writes nothing. The default.
_Avoid_: preview, simulation.

**Add-only automation**:
The write model: a run writes new rows automatically (`AddRow`) and never writes
an update or a delete on its own. Updates become proposals; deletes do not exist.
_Avoid_: full sync.

**Update proposal**:
A pending, operator-reviewed change to an existing store row. Created only when
at least one mapped column differs byte-for-byte; carries the per-column
`old → new` diffs. Never written until the operator applies it. At most **one
pending proposal exists per match key**: a later run re-proposing a key
**supersedes** the earlier pending row (status `superseded`), so the queue shows
the freshest state rather than one row per run.
_Avoid_: pending update, diff.

**Apply selected**:
The operator action that writes the ticked proposals, each under compare-and-swap
(the store row must still match the recorded `old` values).
_Avoid_: commit, confirm.

**Stale proposal**:
A proposal whose store row changed after the proposal was computed; it is skipped
and reported rather than overwriting the manual revision.
_Avoid_: outdated, conflicted.

**Superseded proposal**:
A pending proposal replaced by a newer one for the same match key. It leaves the
review queue (status `superseded`) but is kept in the DB as history.
_Avoid_: replaced, dropped.

**Conflict**:
An ambiguity that blocks or skips a row rather than guessing — a match key
duplicated in the store (halts) or in the source (skips), or a record missing its
match key.
_Avoid_: error, collision.

**Quarantine**:
Withholding a record from writes and reporting it, because its identity could
not be resolved safely.
_Avoid_: skip, drop.

**Blacklist** (suppression list):
The per-job set of match keys that must not be re-added. A key enters it
automatically when a previously-written key disappears from the store (deleted
by hand), and is cleared automatically when the key reappears in the store. The
operator can also add or remove a key by hand — **removing a key also forgets its
`tracked` record**, so the next run adds it again instead of re-detecting the
same deletion (auto-clear keeps tracking, so a second deletion is still seen).
Suppresses `add` only.
_Avoid_: ignore list, skip list, delete policy.

**OS schedule** (OS task):
The per-job entry in Windows Task Scheduler or Linux crontab that wakes
`run-due --job <name>` often enough for `is_due()` to evaluate. It is a coarse
wake-up, never the schedule itself.
_Avoid_: cron job (that is the OS artifact, not the schedule).

**Trigger**:
The coarse OS-level schedule (`Trigger`) derived from a job's `Schedule` by
`os_scheduler.plan`; `None` for a manual-only or malformed schedule.
_Avoid_: schedule, interval.

**Tracking**:
The record of which match keys this system has written (and a content hash per
row), enabling idempotency and proposal bookkeeping.
_Avoid_: audit trail (that is the run log).

## Running

**Job**:
One registered `source survey → Subject Store` sync configuration.
_Avoid_: task, sync (unqualified), store (it configures a store, but is not one).

**Registry**:
The persisted set of jobs (`jobs` / `job_columns` tables in the SQLite state
file).
_Avoid_: catalog, database.

**Run**:
One execution of a job, recorded with a status (`ok` / `blocked` / `partial` /
`error` / `cancelled`), counts, and conflicts.
_Avoid_: execution, sync (unqualified).

**Run queue**:
The per-run scheduler of work: each accepted job runs on its own thread, one run
per job at a time, never two runs against one store, with cooperative per-run
stop.
_Avoid_: worker pool, thread pool, daemon.

**Scheduler**:
The in-process tick loop that enqueues enabled jobs when their interval
`Schedule` (minute/hour/day/week) is due.
_Avoid_: cron, daemon.

**Readiness gate**:
The check that must pass before any write — id-column existence/uniqueness,
match-key uniqueness, strict-mode parity, pairing completeness, and the no-op id
probe. It runs inside every run, over data the run already fetched; a failure
blocks the run. There is no separate "run preflight" action.
_Avoid_: validation, sanity check, preflight pass.

## Notifying

**PIC** (person in charge):
The per-job list of email addresses that receive the run report. Stored as one
`;`-separated string; entries that are not email addresses are warned about in
the job detail and are never mailed.
_Avoid_: owner, contact, recipient list.

**Notification** (run report):
The HTML summary of one run — its facts (job, store, survey, run, time, counts,
status), summary only — mailed to the job's
PIC. Sent by OWA via Playwright (no SMTP), only for a live run that added at
least one row. A side channel: it never changes a run's status, and every
attempt (sent / skipped / failed / dry_run) is recorded. Mailing is **per-job
opt-out** (`jobs.notify_enabled`, the GUI's "Email PIC on added rows" checkbox,
default ON): a run mails only when that toggle is on and the run was not invoked
with `--no-notify`.
_Avoid_: alert, email, message.
