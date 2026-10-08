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
| Export answers | `GET /SimpleExport?surveyID=&subjectIDs=` | chunk ≤100 subjectIDs; EAV payload |
| Add row | `POST /SubjectStores/AddRow/{storeID}?<col>=<val>&...` | returns `{"Done": true}` |
| Update row | `POST /SubjectStores/UpdateRow/{storeID}?<col>=<val>&...` | **row selector not yet confirmed** |
| Delete row | `POST /SubjectStores/DeleteRow/{storeID}?...` | params `storeID` + `userTag` (ref `Form1.cs:1037`) |
| Delete all | `POST /SubjectStores/DeleteAllRows/{storeID}` | destructive |

Parameter placement follows the reference testbed
`src/knowledge/assets/STG_RESTAPI/Form1.cs`: a parameter is a **path segment**
when `ParamsAsQueryString=false`, else a **query param** (`AllowQueryParam`).
Observed: `AddRow` takes the columns as query params and the store id in the path.

### 2.2 Export payload shape is EAV, not wide

`GET /SimpleExport` returns
`{"subjects":[{"SubjectID":"54861337","Columns":[{"Var":"SbjNum","Value":54861337}, ...]}, ...]}`.

To select columns we flatten `Columns[]` into a `{Var: Value}` dict per subject.
Observed 910–974 distinct vars per survey.

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

Link **2** (`ebf5e423-...`) writes the same store using vars `I7_1_2`, `I8a_1`,
`Q32` — i.e. the **variable names are survey-specific and will differ** for the
next survey. This is the central design hazard.

Precise join result: indexing the 296 Link-1 subjects by `T_QPM1_2` and joining
to the 295 store rows → **295/295 store rows found a source record**
(0 missing); 296/296 source `T_QPM1_2` values are distinct. Of the rows that
joined, ~109 matched all content columns byte-for-byte and the rest differed
only in format (leading zero / spacing / truncated name) — never in identity.

### 2.5 `No` is not auto-assigned

`AddRow` with no `No` produced a row with `No: 0`. The server does **not**
renumber. Operators therefore add a `No` column as an **order tag (IsTag)** so
row order never shifts when rows are appended.

### 2.6 Pull-window trap (affects delete)

A 60-day `Upload` window returned 296 subjects; **22 store rows had no matching
subject** (their interviews predate the window). Therefore "absent from the pull"
≠ "should be deleted". A delete policy must account for this.

### 2.7 Key-candidate sanity check

Over 296 subjects: `SbjNum` distinct = 296 ✅; `T_QPM1_2`/`QRelID_PanelistID`
distinct = 296 ✅; **`QRelID_RVid` distinct = 248 ❌** (has duplicates);
`SbjVer` = 44 ❌. A key-validity test must reject non-unique candidates.

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

### 4.1 Column roles are separated (never merged)

| Role | Column | Set by | Mutable after set? |
|---|---|---|---|
| Order tag (IsTag) | `No` | **system** (`max(No)+1`) | yes (monotonic append) — **NOT identity** |
| **Identity key** | `SbjNum` | source | **never** rewritten by the system |
| Content | configured columns | source | yes |

Semantics: **per-interview**. Each distinct `SbjNum` = one row. A re-interview
of the same panelist is a **new row**.

### 4.2 Lookup

```
pull all subjects           → source = { SbjNum: record }
GET GetStore/{store_id}     → store rows
index store by SbjNum       → store_by_key

new      = source.keys ∖ store.keys                      → ADD    (No = max+1)
changed  = keys ∩ , values differ                        → UPDATE (identity untouched)
same     = keys ∩ , values equal                         → SKIP
absent   = store.keys ∖ source.keys                      → DELETE (per policy §6)
```

New rows are discovered **by key set difference**, never by position/timestamp.

### 4.3 Fail-closed rules

```
SbjNum missing in a source record      → QUARANTINE that record (report), no write
SbjNum duplicated within source        → CONFLICT, skip those rows, report
SbjNum duplicated within store         → HALT the job, report
Update NEVER writes the identity column
All conflicts → `conflicts` table (SQLite); the run reports and stops
```

Any candidate identity that fails uniqueness over the sample is **rejected
automatically** by preflight (§7).

### 4.4 Adoption of a legacy store

The current store has **no `SbjNum` column** (`No`,`uniqueID`,`I7_1_2`,`I8a_1`,
`Q32`). Before normal sync, a **one-time adoption** assigns identity:

```
for each legacy store row:
    find the source record matching on content columns
    1:1 unique        → write its SbjNum via UpdateRow (once)
    not unique / none → QUARANTINE, do not guess
once every row has a unique SbjNum → normal sync
```

Adoption is interactive + dry-run first; it is the highest-risk step and is not
run automatically.

---

## 5. Binding Gate

Column-name parity is enforced, because store names and source names differ per
survey.

### 5.1 Modes

- **`strict`** — the set of store columns (minus `No` and the identity column)
  must equal the set of source vars. If not → the job is **blocked** and never
  writes.
- **`mapped`** — the operator explicitly approves a per-column mapping
  (required whenever names differ).

### 5.2 Preflight

Runs (once, and whenever the config changes) and produces a review table:

| store col | suggested source var | value overlap | status |
|---|---|---|---|
| `uniqueID` | `T_QPM1_2` | 295/296 | ✅ |
| `I7_1_2` | `T_QPM1_1` | 295/296 | ✅ |
| `I8a_1` | `T_QPM1_3` | 295/296 | ⚠️ format differs |
| `Q32` | `Q32` | 295/295 | ✅ |

Auto-suggestion is computed (highest value-overlap per store column) but is
**only a suggestion** — a human approves. `strict` failures block the job.

### 5.3 Value comparison — strict 100%

Content comparison is **byte-for-byte after no transformation**. Differences are
reported as conflicts, never silently written. Normalization (e.g. phone leading
zero) is **opt-in per column** and off by default. Known real differences:
`store "8151883872"` vs `source "08151883872"`; `store "Jefta"` vs
`source "Jefta Chleresta"`.

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
throttled, with retry. `AddRow` is **not idempotent** → always verify key
existence first. `UpdateRow` is naturally idempotent.

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
store:  { id: 17417, order_tag: "No" }
source: { survey_id: "60824d46-9b2e-41e5-8b47-ae11410ee24d",
          window_days: 0,          # 0 = full history
          date_type: Upload }      # Upload = server-arrival time
binding:
  mode: strict                     # strict | mapped
  identity: { store_col: "SbjNum", source_var: "SbjNum" }
  columns:
    - { store_col: "I7_1_2", source_var: "T_QPM1_1" }
    - { store_col: "I8a_1",  source_var: "T_QPM1_3" }
    - { store_col: "Q32",    source_var: "Q32" }
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
  fetcher.pull(survey, window, dateType=Upload)
    └─ flatten EAV → per-subject {var: value}
  binding.apply()                → {identity, columns, order_tag}
  planner.diff(store_rows, records)
    └─ add / update / delete / conflicts
  writer.apply()                 → POST SubjectStores/{AddRow|UpdateRow|DeleteRow}/...
  state.record_run()             → log + hashes
```

### 7.3 GUI (PySide6)

- Job list (add/edit/delete).
- Preflight view + **Approve binding** dialog.
- **Sync** button; Dry-run toggle (default on).
- Run log + conflicts panel.
- Delete-policy selector.

---

## 8. Risks

| Risk | Level | Mitigation |
|---|---|---|
| `UpdateRow`/`DeleteRow` row-selector semantics unknown | High | Fase 0 blocks; probe first |
| Store may not accept a new `SbjNum` column | High | Fase 0 probe |
| `full_history` delete removes legitimate rows if window is short | High | require full-history window; dry-run; confirm dialog |
| `AddRow` not idempotent → duplicates | High | check key first; hash in state |
| Strict 100% vs real data (phones/truncation) | Medium | report as conflicts; opt-in normalisation |
| 1 request/row × N rows | Medium | throttle + retry + resume |
| Store LIVE ─ writes are production data | High | dry-run default; explicit confirm; audit log |
| Key drift ("No replacing key") | Medium | identity immutable; fail-closed |
| Multiple links writing one store | Medium | filelock; run log |

---

## 9. Phases

**Fase 0 — De-risk (blocks later phases)**
- Confirm `UpdateRow` row selector + payload; confirm `DeleteRow` (`userTag` = `No`?).
- Confirm the store accepts a new `SbjNum` column.
- Confirm `No` convention on add.
- Remove dummy row `uniqueID=999999`.

**Fase 1 — Skeleton** — `pyproject`, `stg_client` (read+write, mock tests), `config`.
**Fase 2 — Binding gate** — `binding.preflight` + key-validity test + approve dialog; block `strict` mismatch.
**Fase 3 — Data** — `fetcher` + EAV flatten + `planner.diff`.
**Fase 4 — Adoption + Write** — `SbjNum` adoption, `writer`, idempotency, `state`.
**Fase 5 — GUI** — job config, preflight, Sync, dry-run, log.
**Fase 6 — Scheduled** — `cli.py` + filelock (optional).

---

## 10. Open Questions

1. Does the STG store accept a genuinely new column (`SbjNum`) via `AddRow`/`UpdateRow`, or must columns be pre-defined in Studio? (Fase 0)
2. Exact `UpdateRow` row selector and `DeleteRow` `userTag` mapping. (Fase 0)
3. Confirm `full_history` window configuration per job (how far back is "full"?).
4. Is `SbjNum` ever absent from an interview export (system/helper subjects)?
