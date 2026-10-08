# STG Store Sync — Design Spec

> **Status:** Draft for review
> **Date:** 2026-10-08
> **Repo:** `stg-store-sync` (new, standalone — NOT part of `stg_automation_suite`)
> **Author:** design session

---

## 1. Problem

A SurveyToGo (STG) **Subject Store** (a.k.a. External List) is a table on the STG
server. It is read by survey questions (JScript `dblGetSubjectStoreRow`) and is
currently maintained **manually**: operators add/update rows by hand, row by row,
via the STG Studio Subject Store UI or ad-hoc API calls. Manual upkeep at the
minute/hour cadence needed is not sustainable.

We want a standalone tool that:

1. **Pulls** interview data for a source survey (all interviews).
2. **Selects** a configured set of columns.
3. **Writes** the resulting rows into a target STG Subject Store — adding new
   rows, updating changed rows, and (by policy) deleting rows absent from the
   source.
4. Runs on demand via a desktop **GUI + Sync button**, with an optional
   headless/scheduled path.

---

## 2. Verified Findings (from live, read-only probing)

These are empirical, from probing the real STG server. They drive the design.

### 2.1 REST API

Base URL `https://surveytogo.nielseniq.com/api`; **HTTP Basic** auth
(`STG_USER`, `STG_PASSWORD`).

| Purpose | Call | Notes |
|---|---|---|
| List stores | `GET /SubjectStores/GetStores` | array of `{ID, Name, Version, LastModified}` (611 stores observed) |
| Read store | `GET /SubjectStores/GetStore/{storeID}` | array of row objects; columns are dynamic |
| Store usage | `GET /SubjectStores/GetStoreUsage/{storeID}` | array of `{SurveyID, Name}` |
| Interview IDs | `GET /SurveyInterviewIDs?surveyIDs=&dateType=&dateStart=&dateEnd=&statuses=&orderBy=` | array or `{subjects:[...]}` |
| Export answers | `GET /SimpleExport?surveyID=&subjectIDs=&variableNames=` | `variableNames` optional token filter (probe §F9) |
| Add row | `POST /SubjectStores/AddRow/{storeID}?<col>=<val>&...` | insert; columns must be **pre-defined** |
| Update row | `POST /SubjectStores/UpdateRow/{storeID}?<col>=<val>&...` | **UPSERT** on the store id column; full-row |
| Delete row | `POST /SubjectStores/DeleteRow/{storeID}/{userTag}` | `userTag` = **value of the store id column** |
| Delete all | `POST /SubjectStores/DeleteAllRows/{storeID}` | destructive |

Parameter placement follows the reference testbed
`src/knowledge/assets/STG_RESTAPI/Form1.cs`: a parameter is a **path segment**
when `ParamsAsQueryString=false`, else a **query param** (`AllowQueryParam`).
Observed: `AddRow`/`UpdateRow` take the columns as query params and the store id
in the path; `DeleteRow` takes the tag **value as a path segment**.

> **Probe-corrected (Fase 0).** The write semantics above were **verified live**;
> see `docs/probe/2026-10-08-fase0-findings.md`. In summary: each store has one
> designated **id/tag column** (`No` on the dummy store, `uniqueID` on
> `Gran_Turismo_Link2`); `UpdateRow` is an **upsert** keyed on it (unseen value →
> insert; the id value is immutable); updates **must send every column** or the
> omitted ones are reset; `AddRow` **cannot** create a new column; `DeleteRow`
> always returns `{Done: true}` (verify by re-reading); a `No`-less row gets tag
> `0`, which is then unreachable by tag.

### 2.2 Export payload shape is EAV, not wide

`GET /SimpleExport` returns
`{"Subjects":[{"SubjectID":"54861337","Columns":[{"Var":"SbjNum","Value":54861337}, ...]}, ...]}`
(note the capital `Subjects`).

To select columns we flatten `Columns[]` into a `{Var: Value}` dict per subject.
Observed 429–974 distinct vars per survey (429 after a token filter; 974 when the
survey is broader).

**Empty values are ABSENT, not empty strings** (probe F7). An unanswered var — e.g.
an others-specify column like `Q42_TEXT` — does **not** appear in `Columns[]` at
all (`present=1/296, empty=0`). Consequently:

- "var missing from the export" means **empty**, not "not to be written".
- 528 of 910 vars have partial presence (present in some subjects, absent in
  others) — normal, driven by skip logic.
- The flatten step must therefore treat a missing var as `""` for the configured
  columns, never as "leave the store value untouched".

### 2.3 External List ↔ Subject Store

ExternalList questions read the store at runtime:

```js
var sr  = SelectedAnswerText(CurrQues);
var row = dblGetSubjectStoreRow(ln, "NAME_ID", sr);   // ln = "SPV_ID"
SetAnswer(I(I1_3), row["ID"]);
SetAnswer(I(I1_4), row["NAME"]);
```

The store is named in `AdvancedScripts` (`var ln = "SPV_ID";`). Therefore
"update an External List" **is** "update a Subject Store row".

### 2.4 Column names differ between the store and the source survey

Target store `Gran_Turismo_Link2` (id `17417`, v126, 295 rows):

| Store column | Source var in Link **1** | Meaning | Match |
|---|---|---|---|
| `uniqueID` | `T_QPM1_2` (= `SbjNam` = `QRelID_PanelistID`) | panelist id | 295/296 values overlap |
| `I7_1_2` | `T_QPM1_1` | name | values overlap (store truncates) |
| `I8a_1` | `T_QPM1_3` | phone | values overlap (leading zero differs) |
| `Q32` | `Q32` | a code | 295/295 |
| `No` | *(not in source)* | row order tag | **store-only** |

On this store the **API id/tag column is `uniqueID`** (probe F1) — `DeleteRow`
with the `uniqueID` value removed a row, while `No` did not. This is **not
universal**: the dummy store uses `No`. See §2.5 and `docs/probe/`.

Link **2** (`ebf5e423-...`) writes the same store using vars `I7_1_2`, `I8a_1`,
`Q32` — i.e. the **variable names are survey-specific and will differ** for the
next survey. This is the central design hazard.

Precise join result: indexing the 296 Link-1 subjects by `T_QPM1_2` and joining
to the 295 store rows → **295/295 store rows found a source record**
(0 missing); 296/296 source `T_QPM1_2` values are distinct. Of the rows that
joined, ~109 matched all content columns byte-for-byte and the rest differed
only in format (leading zero / spacing / truncated name) — never in identity.

### 2.5 The id/tag column is store-specific and is the API key

Probe F1: each store designates one **id/tag column**. `UpdateRow` and
`DeleteRow` key on it; other columns give `500 Error: id column data not found`
(on a store whose id column is `No`) or silently no-op (on a store whose id
column is `uniqueID`). `AddRow` without the id value produces tag `0` (probe F5),
which is then unreachable by tag. So the job must **declare** the id column and
preflight must **verify** it.

### 2.6 Pull-window trap (affects delete)

A 60-day `Upload` window returned 296 subjects; **22 store rows had no matching
subject** (their interviews predate the window). Therefore "absent from the pull"
≠ "should be deleted". A delete policy must account for this.

### 2.7 Key-candidate sanity check

Over 296 subjects: `SbjNum` distinct = 296 ✅; `T_QPM1_2`/`QRelID_PanelistID`
distinct = 296 ✅; **`QRelID_RVid` distinct = 248 ❌** (has duplicates);
`SbjVer` = 44 ❌. A key-validity test must reject non-unique candidates.

### 2.8 Pull only the needed variables (probe F9/F10)

`SimpleExport` accepts `variableNames` (optional). The rule (empirical): the term
is matched against the **base / question-level variable name**, and the response
includes that variable's **whole family** — answer-level (`T_*`), loop-expanded
(`I_*`), and others-specify (`_TEXT`).

| term | result |
|---|---|
| `QPM1` (question var) | `T_QPM1_1`, `T_QPM1_2`, `T_QPM1_3` ✅ |
| `T_QPM1_2` (answer var) | nothing ❌ |
| `Q42` | `Q42`, `Q42_TEXT` ✅ |
| `Q46a` | all `I_*_Q46a` + their `_TEXT` ✅ |
| `TEXT` (a suffix, not a base) | nothing ❌ |

So the filter must be given the **question-level** name (`QPM1`), **not** the
answer-level name the store maps (`T_QPM1_1`). Requesting the answer-level name
silently returns nothing. Measured effect on Link1: `variableNames=QPM1,Q32,SbjNum`
→ **94 vars/subject** (vs 429 full), and the whole `T_QPM1_*` family is present.
~90 STG metadata vars are always returned and are expected.

Because the filter is advisory (a term may silently drop), the response is
**verified** for every needed var, and the result is **projected to the configured
columns** after the pull — the filter is an optimisation, the projection is the
source of truth. Fall back to a full export if a needed var cannot be produced by
any term.

The pull term is the **question-level base name**, which is **not** always
derivable from the answer-level var by a fixed string rule (`T_QPM1_1`→`QPM1`
works, but the loop var `I_351_Q46a`→`Q46a` breaks a simple strip). So the base
name is resolved as follows:

- **derived automatically** from the survey's question/answer structure (read the
  survey XML: each answer var belongs to a question; the question's `VariableName`
  is the base name). This resolves the common answer-level case (`T_QPM1_1`).
- if it cannot be resolved (loop/derived vars), fall back to a **full export** and
  project locally — always correct, just heavier.

**The user declares only two things per column: the store column and the source
variable.** The pull term is an internal optimisation, shown **read-only** in
preflight, never a required input. The filter is never a correctness dependency.

---

## 3. Goals / Non-Goals

**Goals**
- Pull all interviews for one source survey; select configured columns.
- Reconcile against the target store by an **explicit identity key**.
- Add new rows, update changed rows, delete by policy.
- Fail closed on ambiguity (never guess).
- GUI with a **Sync** button; dry-run by default.
- Standalone repo, own deps, own secrets.

**Non-Goals (this design)**
- Editing survey XML/programming.
- Multi-store fan-out from a single survey (1 store = 1 survey).
- Real-time streaming; batch runs are enough.
- Auto-mapping column names without human approval.

---

## 4. Identity & Reconciliation Model

### 4.1 Two distinct columns (roles must never be merged)

| Role | Column | Set by | Mutable? | Used by |
|---|---|---|---|---|
| **API id / tag** | store-specific (`No`, `uniqueID`, …) | system (add), **immutable** | no | keys `UpdateRow`/`DeleteRow` |
| **Match key** | `SbjNum` (per-interview) | source (also written to store) | stable | diff (add vs update) |
| Content | configured columns | source | yes | value compare |

`No` is **not** automatically the id column — the id column is whatever the store
designates (probe §F1). On `Gran_Turismo_Link2` it is `uniqueID`; on the dummy
store it is `No`.

**Decision (2026-10-08):** the id column (IsTag) is **declared by the user** in
the GUI (`store.id_column`, a required field). It is **not** auto-discovered. The
system still **verifies** it in preflight so a wrong value can never cause a
mis-keyed write (§4.2.1).

Semantics: **per-interview**. Each distinct `SbjNum` = one row. A re-interview of
the same panelist is a **new row**.

### 4.2.1 Id-column declaration & verification

The user sets `store.id_column` in the GUI. For safety, preflight runs an
**id-column check** (read-only except one no-op probe):

```
1) column exists on the store?          no  → reject, job cannot write
2) values unique across store rows?     dup → reject (not a 1:1 key)
3) no-op probe on one row:
     UpdateRow with the FULL payload, all values IDENTICAL
       200 {Done:true} and the row is unchanged  → id_column CORRECT ✅
       500 "id column data not found"            → id_column WRONG   ❌
```

The no-op probe is safe: sending identical values changes nothing (verified on
store 17417 — 295 rows in, 295 rows out). It runs **once at preflight**, not on
every sync, and is exempt from the `dry_run` guard (it is a verification step,
not a data change).

### 4.2 Lookup

```
pull all subjects           → source = { SbjNum: record }
GET GetStore/{store_id}     → store rows
index store by SbjNum       → store_by_key

new      = source.keys ∖ store.keys                      → ADD    (tag = next_tag(store))
changed  = keys ∩ , values differ                        → UPDATE (tag preserved)
same     = keys ∩ , values equal                         → SKIP
absent   = store.keys ∖ source.keys                      → DELETE (per policy §6)
```

New rows are discovered **by key set difference**, never by position/timestamp.
`next_tag(store)` = `max(id_column) + 1` over the numeric id values.

### 4.3 Write mechanics (probe-verified)

- **ADD** → `AddRow` with **all** columns incl. the assigned tag value.
- **UPDATE** → `UpdateRow` with **all** columns; the id value must already exist
  and is sent unchanged. Because the API upserts on the id column, an update can
  never accidentally insert (the id value is present).
- **DELETE** → `DeleteRow/{storeID}/{tag_value}`; then re-read to confirm.
- A **new column** (e.g. `SbjNum`) must be **pre-defined in STG Studio** — the API
  rejects unknown columns.

### 4.4 Fail-closed rules

```
match key missing in a source record    → QUARANTINE that record (report), no write
match key duplicated within source      → CONFLICT, skip those rows, report
match key duplicated within store       → HALT the job, report
id/tag column missing in config         → preflight fails, job never writes
Update NEVER writes the id/tag column to a new value
All conflicts → `conflicts` table (SQLite); the run reports and stops
```

Any candidate identity that fails uniqueness over the sample is **rejected
automatically** by preflight (§5).

### 4.5 Adoption of a legacy store

When the match column (`SbjNum`) does not yet exist on the target store:

```
[one-time, in Studio]  add the SbjNum column to the store
[dry-run first]        for each legacy store row:
                          find the source record matching on content columns
                          1:1 unique        → write its SbjNum via UpdateRow (full payload)
                          not unique / none → QUARANTINE, do not guess
                       once every row has a unique SbjNum → normal sync
```

Adoption is interactive + dry-run first; it is the highest-risk step and is never
run automatically.

---

## 5. Binding Gate

Column-name parity is enforced, because store names and source names differ per
survey.

### 5.1 Modes

- **`mapped`** (default) — the operator explicitly approves a per-column
  `{source → store}` alias pair. Store names and source names commonly differ
  (e.g. store `I7_1_2` ← source `T_QPM1_1`), so this is the normal case.
- **`strict`** — `mapped` **plus** a check that every `source == store`. Used as a
  safety net when a job happens to use identical names. A `strict` mismatch
  **blocks** the job.

Auto-mapping is never silent: preflight **suggests** an alias (highest
value-overlap) but a human must approve it.

### 5.2 Preflight

Runs (once, and whenever the config changes) and produces a review table:

| store col | suggested source var | value overlap | status |
|---|---|---|---|
| `uniqueID` | `T_QPM1_2` | 295/296 | ✅ |
| `I7_1_2` | `T_QPM1_1` | 295/296 | ⚠️ format differs |
| `I8a_1` | `T_QPM1_3` | 295/296 | ⚠️ format differs |
| `Q32` | `Q32` | 295/295 | ✅ |

Preflight also runs the **id-column declaration check** (§4.2.1: exists, unique,
no-op probe) and validates the match key's uniqueness (§4.4).

### 5.3 Value comparison — strict 100%

Content comparison is **byte-for-byte after no transformation**. Differences are
reported as conflicts, never silently written. Normalization (e.g. phone leading
zero) is **opt-in per column** and off by default. Known real differences:
`store "8151883872"` vs `source "08151883872"`; `store "Jefta"` vs
`source "Jefta Chleresta"`.

**Empty is a value, not a skip.** A configured column that is absent from a
subject's export is treated as `""` (probe §2.2). Empty is compared like any other
value:

```
source ""  vs  store ""   → SAME   (no write)
source ""  vs  store "x"  → UPDATE (clears the store cell to "")
source "x" vs  store ""   → UPDATE (fills the store cell)
```

`""` and whitespace (`" "`) are **distinct** and both are stored verbatim (probe:
`" "` round-tripped literally). Normalisation, where needed, must be declared per
column; it never silently turns `""` into "skip".

---

## 6. Write Model & Delete Policy

**Full sync** = add + update + delete. `delete_policy` default =
**`full_history`**:

- Because the GUI can pull an arbitrary window, `full_history` requires the pull
  to cover the full population (the operator configures the window accordingly).
- Rows absent from a full-history source are deleted.
- Other selectable policies: `tracked_only` (only rows this system wrote, tracked
  in state), `manual` (report candidates, operator ticks).
- `dry_run: true` is the **default**; no write happens until the operator
  confirms.

Write execution: **one request per row** (`AddRow`/`UpdateRow`/`DeleteRow`),
throttled, with retry.

Probe-corrected write rules (Fase 0):

- Every write sends the **full row** (all columns) — the API resets omitted
  columns.
- A configured column that is empty in the source must be **written as `""`**, not
  omitted (omitting defaults the store cell; probe). Clearing a store cell is done
  by updating it to `""`.
- The id/tag value is assigned on add (`max(id)+1`) and **never changed** on
  update.
- `UpdateRow` is an **upsert**; sending a full payload with an existing id value
  is a safe idempotent no-op.
- `AddRow`/`UpdateRow` return success but `DeleteRow` **always** returns
  `{Done: true}`; after any delete, **re-read the store** to confirm.
- `AddRow` cannot introduce columns; the match column must pre-exist (§4.5).

---

## 7. Architecture

Standalone repo, Python 3.12 + PySide6 + requests (recommended; closest to the
existing reference implementation and pull scripts).

```
stg-store-sync/
├── pyproject.toml                 # deps: pyside6, requests, python-dotenv, filelock, pytest
├── README.md
├── .env.example                   # STG_SERVER, STG_USER, STG_PASSWORD
├── .gitignore
├── src/store_sync/
│   ├── __init__.py
│   ├── gui.py                     # PySide6 entry
│   ├── cli.py                     # headless entry (optional/scheduled)
│   ├── core/
│   │   ├── config.py              # job schema + validation + persistence
│   │   ├── stg_client.py          # REST: stores / export / Add|Update|DeleteRow
│   │   ├── fetcher.py             # pull → flatten EAV
│   │   ├── binding.py             # parity + auto-suggest + preflight key test
│   │   ├── planner.py             # diff → add/update/delete/conflicts
│   │   ├── writer.py              # apply + throttle + retry + idempotency
│   │   └── state.py               # SQLite: jobs, run log, conflicts, hashes
│   └── ui/
│       └── main_window.py
└── tests/
```

### 7.1 Job config (example)

```yaml
job: gran_turismo_link1
store:  { id: 17417, id_column: "uniqueID" }   # user-declared IsTag; preflight-verified
source: { survey_id: "60824d46-9b2e-41e5-8b47-ae11410ee24d",
          variable_names: ["QPM1", "Q32", "SbjNum"] }  # question-level base names (probe F9)
# window_days / date_type removed: every sync pulls full history
# (superseded by 2026-10-08-registration-and-schedule-redesign-design.md)
binding:
  mode: mapped                     # mapped | strict
  match:   { source_var: "SbjNum", store_col: "SbjNum" }   # per-interview key
  columns:                         # user declares: source var → store column (2 fields)
    - { source_var: "T_QPM1_1", store_col: "I7_1_2", kind: text }
    - { source_var: "T_QPM1_3", store_col: "I8a_1",  kind: text }
    - { source_var: "Q32",      store_col: "Q32",    kind: text }
  # pull term (question-level base name) is DERIVED (shown read-only in preflight);
  # not a user input. If it cannot be resolved → full export + local projection.
write:
  op: full_sync                    # add + update + delete
  dry_run: true
  delete_policy: full_history      # full_history | tracked_only | manual
  normalize: {}                    # per-column opt-in, default none
```

### 7.2 Data flow

```
[preflight, once]
  get_store + sample export → binding.preflight → USER APPROVE → persist binding

[each Sync / tick]
  fetcher.pull(survey, window, dateType=Upload, variableNames=<tokens>)
    └─ flatten EAV → per-subject {var: value}   (missing configured var → "")
    └─ VERIFY every needed var is present; else fall back to full export
    └─ PROJECT to the configured columns (source of truth)
  binding.apply()                → {id_column, match, columns}
  planner.diff(store_rows, records)
    └─ add / update / delete / conflicts
  writer.apply()                 → POST SubjectStores/{AddRow|UpdateRow}/... (+DeleteRow/{tag})
  state.record_run()             → log + hashes
```

### 7.3 GUI (PySide6)

- Job list (add/edit/delete).
- **Column binding editor** — the user declares, per column, **two fields only**:
  - **store column** (chosen from the live `GetStore` columns), and
  - **source variable** (chosen from a live searchable list of the survey's vars).
  The pull term (question-level base name) is **derived and shown read-only**;
  it is not an input. A non-derivable column is flagged and simply triggers a full
  export at pull time.
- **Excel import/export of the mapping** — the binding table can be **downloaded as
  `.xlsx`** (template with the store columns pre-filled so the operator just fills
  the source-variable column) and **uploaded back** to populate the job. This
  supports bulk mapping for wide surveys without clicking row by row. Import is
  validated (unknown store columns / source vars, duplicates → rejected with a
  report) and still passes through **Approve binding** before any write.
- Preflight view + **Approve binding** dialog (shows the derived pull terms,
  id-column check, match-key uniqueness, value-overlap suggestions).
- **Sync** button; Dry-run toggle (default on).
- Run log + conflicts panel.
- Delete-policy selector.

---

## 8. Risks

| Risk | Level | Mitigation |
|---|---|---|
| `UpdateRow`/`DeleteRow` semantics | **Resolved** | Fase 0 probe; see `docs/probe/` |
| New `SbjNum` column unsupported by API | **Resolved** | pre-define in Studio (§4.5) |
| `UpdateRow` resets omitted columns | High | always send the **full** row (F2) |
| Upsert on add → accidental insert | Medium | id value assigned `max+1`; add is a distinct call |
| `DeleteRow` reports success on no-match | Medium | re-read the store after deletes (F4) |
| `full_history` delete removes legitimate rows if window is short | High | require full-history window; dry-run; confirm dialog |
| Strict 100% vs real data (phones/truncation) | Medium | report as conflicts; opt-in normalisation |
| 1 request/row × N rows | Medium | throttle + retry + resume |
| Store LIVE ─ writes are production data | High | dry-run default; explicit confirm; audit log |
| Id column declared wrongly | Medium | preflight verifies it against the live store (§5.2) |
| Multiple links writing one store | Medium | filelock; run log |

---

## 9. Phases

**Fase 0 — De-risk — ✅ COMPLETE** (see `docs/probe/2026-10-08-fase0-findings.md`)
- ✅ `UpdateRow` = upsert on the store's id column, full-row payload.
- ✅ `DeleteRow userTag` = value of the store id column, in the **path**.
- ✅ A new column **cannot** be added via the API — must be pre-defined in Studio.
- ✅ `No` convention on add (`max+1`; `No`-less → `0`).
- ✅ Dummy row `uniqueID=999999` removed from 17417 (back to 295 rows).

**Fase 1 — Skeleton** — `pyproject`, `stg_client` (read+write, mock tests), `config`.
**Fase 2 — Binding gate** — `binding.preflight` + id-column verify + match-key
uniqueness test + approve dialog; `strict` mismatch blocks.
**Fase 3 — Data** — `fetcher` + EAV flatten + `planner.diff`.
**Fase 4 — Adoption + Write** — `SbjNum` adoption, `writer`, idempotency, `state`.
**Fase 5 — GUI** — job config, preflight, Sync, dry-run, log.
**Fase 6 — Scheduled** — `cli.py` + filelock (optional).

---

## 10. Open Questions

1. ~~Does the store accept a genuinely new column via the API?~~ **Answered (F2/F3):**
   no — columns must be pre-defined in Studio.
2. ~~Exact `UpdateRow` selector and `DeleteRow` `userTag` mapping?~~ **Answered
   (F1/F2):** upsert on the store's id column; `userTag` = its value, in the path.
3. Confirm `full_history` window configuration per job (how far back is "full"?).
4. Is `SbjNum` ever absent from an interview export (system/helper subjects)?
