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

### One-command setup (env + deps + .env + verify + OS schedule)

Idempotent — safe to re-run; an existing `.env` is never modified.

```bash
# Linux / macOS / WSL
./scripts/setup.sh

# Windows / VDI (PowerShell)
.\scripts\setup.ps1
```

Each script: finds Python 3.12+, creates/reuses `.venv`, installs the package
+ dev extras, creates `.env` from `.env.example` (prompting for credentials),
runs the test suite, then registers one OS task per job (`schedule sync`).
Non-interactive: pass `-StgServer/-StgUser/-StgPassword` (PS) or
`--stg-server/--stg-user/--stg-password` (bash), plus `-NonInteractive` /
`--non-interactive`. `-SkipTests` / `--skip-tests` and `-SkipSchedule` /
`--skip-schedule` trim steps; skipping tests also skips OS registration.

## Safety

The target store may be used by a **LIVE** survey. Writes change production
data. Dry-run is the default. Updates are never automatic — a run only proposes
them, and the operator applies each under compare-and-swap; the tool never
deletes.

## Automation without the GUI

The in-app scheduler runs only while the app is open. To run jobs on their
schedule with the app closed, register one OS task per job:

```bash
stg-store-sync-cli schedule sync        # reconcile all jobs (run once after install)
stg-store-sync-cli schedule list        # show each job's OS status
stg-store-sync-cli schedule add <name>  # register one job
stg-store-sync-cli schedule remove <name>
```

Each task runs `stg-store-sync-cli run-due --job <name>`; `run-due` decides
whether the job is actually due (the OS trigger is a coarse wake-up) and runs
it add-only, honouring the job's configured dry-run. Credentials come from the
project-root `.env` (resolved independently of the working directory).

## Email notifications

After a run that **added at least one row**, the job's PIC list is mailed an HTML
summary (the run's facts, no row data). Dry-runs and clean (0-add) runs never mail, and
a failed send never changes the run's status.

```bash
pip install -e ".[owa]"          # adds Playwright
export STG_OWA_EMAIL=you@company.com   # the account OWA's picker should click
```

The browser is deliberately **not** headless — the first send opens a Playwright
window; sign in to Outlook Web once and the session persists in
`~/.stg_store_sync/playwright_profile_owa` (override with `STG_OWA_PROFILE_DIR`).
No SMTP, no relay, no app password.

Set the PIC list per job in the job detail's **Config** tab (a `;`-separated
list; non-address entries are flagged but never block saving). Rehearse or
re-send from the CLI:

```bash
stg-store-sync-cli send-report <name> --dry-run --out /tmp   # render only
stg-store-sync-cli send-report <name> --force                # re-send
stg-store-sync-cli run <name> --no-notify                    # skip the mail
```

`--no-notify` is also accepted by `run-all` and `run-due`. Every attempt —
sent, skipped, failed or dry_run — is recorded and shown on the run detail page.
