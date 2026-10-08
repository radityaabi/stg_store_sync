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
The single store column the STG API uses to key `UpdateRow`/`DeleteRow`; its
value is immutable. Declared per job by the operator, never guessed.
_Avoid_: primary key, id (unqualified), key column.

**Match key**:
The per-interview column (`SbjNum`) used to diff source against store — decides
add vs update vs delete. Distinct from the id/tag column.
_Avoid_: key, identity key.

**Tag value**:
The concrete value in a row's id/tag column; `DeleteRow` takes it as a path
segment.
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

**Projection**:
Selecting the configured columns out of a pulled record into the store shape;
the source of truth after any pull.
_Avoid_: transform.

## Planning & writing

**Plan**:
The classification of every source record and store row into `add` / `update` /
`same` / `delete` / `conflict` actions, computed by key-set difference (never by
position or timestamp).
_Avoid_: diff, delta.

**Dry-run**:
A run that computes the plan and writes nothing. The default.
_Avoid_: preview, simulation.

**Full sync**:
Add + update + delete in one run.
_Avoid_: sync (unqualified).

**Delete policy**:
How rows absent from the pull are treated: `full_history` (delete all absent),
`tracked_only` (delete only rows this system wrote), `manual` (report
candidates, operator ticks).
_Avoid_: deletion mode, cleanup policy.

**Conflict**:
An ambiguity that blocks or skips a row rather than guessing — a match key
duplicated in the store (halts) or in the source (skips), or a record missing its
match key.
_Avoid_: error, collision.

**Quarantine**:
Withholding a record from writes and reporting it, because its identity could
not be resolved safely.
_Avoid_: skip, drop.

**Tracking**:
The record of which match keys this system has written (and a content hash per
row), enabling the `tracked_only` delete policy and idempotency.
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
The single global worker that runs jobs one at a time, with cooperative stop.
_Avoid_: worker pool, thread pool.

**Scheduler**:
The in-process tick loop that enqueues enabled jobs when their interval
`Schedule` (minute/hour/day/week) is due.
_Avoid_: cron, daemon.

**Readiness gate** (preflight):
The check that must pass before any write — id-column existence/uniqueness,
match-key uniqueness, strict-mode parity, and the no-op id probe. A failure
blocks the run.
_Avoid_: validation, sanity check.
