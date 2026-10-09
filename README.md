# stg-store-sync

Standalone tool that pulls SurveyToGo (STG / Dooblo) interview data and
syncs selected columns into a target **Subject Store** (External List) via
the STG REST API.

- Source of truth: a source survey's interviews (`/SurveyInterviewIDs` +
  `/SimpleExport`).
- Target: an STG Subject Store, written via
  `/SubjectStores/AddRow` (adds) and `/SubjectStores/UpdateRow` (approved
  proposals only). It never deletes.
- Identity: a store **id/tag column declared per job** (e.g. `uniqueID`) keys the
  writes; a separate **match key** (`SbjNum`, per-interview) drives the diff.
- Lookup: by key-set difference — never by row position.
- Fail-closed: ambiguous keys are quarantined, never guessed.
- Write model: **adds are automated**; a changed row becomes an **update
  proposal** the operator reviews and applies (compare-and-swap); **delete does
  not exist**.
- Columns are mapped by an operator-approved `source → store` alias pair (or an
  **Excel mapping upload**); the pull term is derived, not hand-entered.
- **Pairing**: a job must be paired in the **Columns** tab (the 🔗 action) before
  its first run; pairing reads the survey definition once and caches the pull
  terms. An unpaired job is blocked.
- GUI with a **Sync** button; dry-run is the default.

## Status

Phase 0 (de-risk probes against the live API) **complete** — write semantics
verified; see `docs/probe/2026-10-08-fase0-findings.md`. Product code not started.

- Spec: `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md`
- Plan: `tasks/plan.md`
- Todo: `tasks/todo.md`
- Probe findings: `docs/probe/2026-10-08-fase0-findings.md`

### Key write-model facts (verified live)

- A store has one **API id/tag column** (`No`, `uniqueID`, …) that keys
  `UpdateRow`; it is store-specific and must be declared per job.
- `UpdateRow` is an **upsert** on that column and **must receive every column**
  (omitted ones are reset). The id value is immutable.
- `AddRow`/`UpdateRow` **cannot** create a column; columns must pre-exist in
  STG Studio.

### Pull facts (verified live)

- `SimpleExport` accepts `variableNames` so only needed families are pulled —
  ~4.5× smaller payload. Give it the **question-level** base name (`QPM1`, not the
  answer-level `T_QPM1_2`); it returns the whole family (`T_QPM1_*`, `Q42_TEXT`).
  The response is verified and projected to the configured columns; fall back to a
  full export if a needed var cannot be produced.
- Empty source values are **absent** from the export — treat them as `""` and
  write them; never skip.

## Setup (once Phase 1 lands)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill STG_SERVER / STG_USER / STG_PASSWORD
```

## Safety

The target store may be used by a **LIVE** survey. Writes change production
data. Dry-run is the default. Updates are never automatic — a run only proposes
them, and the operator applies each under compare-and-swap; the tool never
deletes.
