# STG Store Sync — Survey Pairing Out of the Run

> **Status:** Draft for review
> **Date:** 2026-10-09
> **Repo:** `stg-store-sync`
> **Supersedes (in part):** the survey-XML read at run time in
> `2026-10-08-stg-store-sync-design.md` §2.8 / §7.2 (step 1 of the orchestrator).
> All other domain rules (pull, projection, planning, fail-closed,
> empty-is-a-value, add-only automation) remain authoritative.
> **Further superseded (2026-10-15):** the separate `run_preflight` pass was
> removed — readiness is verified in-run only. See the readiness-gate section of
> the main design; the pairing gate described here now lives solely in
> `_readiness_errors`.
> **Author:** design session

---

## 1. Problem

Every run re-reads the survey definition. `run_sync` starts by calling
`client.get_survey_xml(survey_id)` and running `parse_survey_index` over it to
derive the pull term (`QPM1`) for each configured `source_var` (`T_QPM1_1`). That
XML is a 30 s+ download for a large survey, and it is read **on every single
run** — including scheduled runs that fire repeatedly over a survey whose
variables never change once the project is FW'd (finalised).

The variable names in a FW'd project are stable. Re-reading the definition each
run is pure overhead: the pairing between `source_var` and its question-level
base name does not move. The expensive read belongs to **prep** (once, at
pairing time), not to **automation** (every run).

A secondary consequence: because the definition is read inside the run, the
pull-term column in the Columns tab was only ever populated transiently, and was
lost on the next load — the pairing was never a durable, reviewable artefact.

## 2. Intent (design brief)

**For:** the survey-ops operator who owns a set of STG Subject Stores.
**Outcome:** the survey definition is read **once, at prep** (pairing). After
that, every run pulls and filters using the stored pairing — no XML download.
**Success:** an operator pairs a job in the Columns tab, sees the pull term
persisted per column, and can run (manually or on schedule) without the survey
definition ever being fetched again; an unpaired job is refused before any write
rather than silently doing a heavier full export.

**Constraints (agreed in this session):**

1. Survey XML is read **only** during the explicit pairing action, never in a
   run.
2. Pairing is a **separate feature** from automation — a prep step, GUI-only.
3. An unpaired (or stale) job **blocks** the run (fail closed), never falls back
   to an implicit XML fetch.
4. Existing jobs migrate to "unpaired" (empty) — no silent backfill, no hidden
   XML read on load.
5. The `variableNames` filter's safety net (verify-then-fallback) is preserved by
   caching the survey index, so a run never needs the XML to verify.

## 3. Data model

### 3.1 `ColumnBinding` — stored pull term

```python
@dataclass
class ColumnBinding:
    source_var: str
    store_col: str
    kind: str = "text"
    pull_term: str = ""   # question-level base name; "" = not paired
```

`pull_term` is the durable pairing artefact for the column. `""` means the
column has never been paired (or its `source_var` was absent from the XML at
pairing time). It is persisted in `job_columns` and rendered read-only in the
Columns tab.

### 3.2 `SourceConfig` — cached survey index

```python
@dataclass
class SourceConfig:
    survey_id: str
    survey_index: dict[str, str] = field(default_factory=dict)  # var -> base
    paired_survey_id: str = ""                                  # survey id at pairing
```

`survey_index` is the full `parse_survey_index` output cached at pairing time —
the same mapping the run previously rebuilt each time. `paired_survey_id` records
which survey the index was built from, so a changed `survey_id` is detectable as
stale.

**Why both:** the index is the run's source of truth for deriving terms and for
the verify fallback; `pull_term` per column is the durable, human-reviewable
pairing result and the explicit per-column gate. They are written together by the
pairing action; the index is authoritative if the two ever disagree.

### 3.3 Registry schema

`jobs` gains two columns, `job_columns` gains one:

```sql
ALTER TABLE jobs        ADD COLUMN survey_index     TEXT    NOT NULL DEFAULT '{}';
ALTER TABLE jobs        ADD COLUMN paired_survey_id TEXT    NOT NULL DEFAULT '';
ALTER TABLE job_columns ADD COLUMN pull_term        TEXT    NOT NULL DEFAULT '';
```

`survey_index` is stored as JSON text. Migration follows the existing
`_add_schedule_columns` pattern: `PRAGMA table_info` → `ALTER TABLE ADD COLUMN`
for any missing column. An existing job therefore reads back with an empty index
and empty pull terms — **unpaired**, by design (constraint 4).

`Registry._config_values` / `_replace_columns` / `get` are extended to carry the
three new fields. `JobEntry` is unchanged (the dashboard does not show pairing
state in this iteration).

## 4. Pairing (prep, manual)

A new action, **"🔗 Pair with survey XML"**, on the Columns tab.

1. The controller runs it off the GUI thread in an `_NetworkWorker` (never block).
2. The worker fetches `client.get_survey_xml(job.source.survey_id)` and runs
   `parse_survey_index` over it → `{var: base}`.
3. For every configured column, `pull_term = index.get(source_var, "")`.
4. The worker returns `(index, {source_var: pull_term}, unresolved)` where
   `unresolved` lists columns whose `source_var` is absent from the XML.
5. On the GUI thread: store `survey_index` + `paired_survey_id` on the job config,
   and push the per-column pull terms into `JobTableModel` (read-only column).
6. Report unresolved columns in the status bar / toast so the operator fixes the
   `source_var` and re-pairs.

Pairing writes nothing to the registry by itself; the operator persists the
result through the normal **Save** path, which now carries `pull_term`,
`survey_index` and `paired_survey_id`.

**Why not persist on pairing:** the registry is written through one path (Save).
A pairing that persisted directly would create a second write path and could
store a half-edited job. The operator reviews the filled pull terms, then saves.

## 5. Run flow (`core/sync.py`)

`run_sync` no longer reads the survey definition:

- **Removed:** `index = parse_survey_index(client.get_survey_xml(...))` and the
  `"survey"` progress stage.
- **Index source:** `index = config.source.survey_index` (in-memory, from the
  registry row).

The pull-term derivation and the verify fallback are **unchanged** — they now run
against the cached index. `derive_pull_terms(source_vars, index, match_var=...)`
and the `_base_names` verify still work; the only difference is where `index`
came from.

A new fail-closed gate is added to `_readiness_errors` (before any write, in the
run itself — the CLI's entire safety story):

- `config.source.survey_index` empty → `"survey not paired — run Pairing in the
  Columns tab"`.
- `config.source.paired_survey_id != config.source.survey_id` → `"survey pairing
  is stale — the survey changed since pairing; re-run Pairing"`.
- any column with `pull_term == ""` → `"column <store_col> has no pull term —
  re-run Pairing"`.

When any of these hold the run finishes `blocked` (CLI exit `2`), consistent with
the existing readiness gate. Because the gate lives in `_readiness_errors`, the
CLI is covered without a second fetch.

## 6. Readiness gate

There is no separate preflight pass. `_readiness_errors` (the in-run gate)
carries the pairing error, so an unpaired job's run is `blocked` before any
write. (Originally this section described a `run_preflight` helper; it was
removed on 2026-10-15 — readiness is verified in-run only.)

## 7. CLI

The CLI is unchanged in shape. An unpaired job surfaces as `status == "blocked"`
(exit `2`) via the in-run gate. Pairing stays GUI-only (constraint 2): it is a
prep action, not an automation action.

## 8. Tests (TDD)

- `config`: `ColumnBinding.pull_term` round-trips through `to_dict`/`from_dict`;
  `SourceConfig.survey_index` / `paired_survey_id` round-trip.
- `registry`: new columns exist after migration; `add`/`get`/`update` carry
  `pull_term`, `survey_index`, `paired_survey_id`; a legacy DB (no columns)
  migrates to empty/unpaired.
- `fetcher`: `derive_pull_terms` uses a passed-in cached index (no client).
- `sync`: an unpaired job is `blocked`; a paired job derives terms from the cache
  and **`get_survey_xml` is never called** (assert on a fake client); the verify
  fallback still triggers from the cached index.
- `preflight`: unpaired → `report.ok` is `False` with the pairing error; paired →
  terms come from the cache, no XML read. (Note: the separate `run_preflight`
  helper was removed 2026-10-15; this is the `preflight()` report the in-run gate
  builds.)
- `ui`: the pairing button exists; `JobTableModel` renders the stored pull term;
  pairing fills pull terms and marks unresolved columns; controller pairing
  worker returns and persists on save.
- `cli`: unpaired run → exit `2`.

## 9. Risks and mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| Cached index grows with survey size | Low | One JSON TEXT per job; loaded once per run; large surveys are already downloaded in full for the pull. |
| `survey_id` edited after pairing | Med | `paired_survey_id` gate blocks a stale pairing. |
| Operator forgets to pair | Med | The run blocks with a clear message. |
| `pull_term` drifts from the cached index | Low | The index is authoritative at run time; `pull_term` is display/gate only. |
| Legacy jobs silently do a full export | High | Prevented: unpaired jobs **block**; there is no implicit XML fetch or full-export fallback. |

## 10. Out of scope (this iteration)

- Auto-pairing on load / silent migration backfill.
- Validating `source_var` against the XML inside pairing (pairing only fills
  terms; `source_var` presence is still the readiness gate's job).
- Pairing from the CLI.
- Multi-survey fan-out per job.
