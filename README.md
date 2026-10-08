# stg-store-sync

Standalone tool that pulls SurveyToGo (STG / Dooblo) interview data and
full-syncs selected columns into a target **Subject Store** (External List) via
the STG REST API.

- Source of truth: a source survey's interviews (`/SurveyInterviewIDs` +
  `/SimpleExport`).
- Target: an STG Subject Store, written via
  `/SubjectStores/AddRow|UpdateRow|DeleteRow`.
- Identity: `SbjNum` (per-interview, immutable). `No` is an order tag (IsTag),
  never identity.
- Lookup: by key-set difference — never by row position.
- Fail-closed: ambiguous keys are quarantined, never guessed.
- GUI with a **Sync** button; dry-run is the default.

## Status

Design phase. See:

- Spec: `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md`
- Plan: `tasks/plan.md`
- Todo: `tasks/todo.md`

Phase 0 (de-risk probes against the live API) must complete before product code.

## Setup (once Phase 1 lands)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env   # fill STG_SERVER / STG_USER / STG_PASSWORD
```

## Safety

The target store may be used by a **LIVE** survey. Writes change production
data. Dry-run is the default; deletion requires an explicit policy and
confirmation.
