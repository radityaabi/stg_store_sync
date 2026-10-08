# Fase 0 — De-risk Probe Findings

> **Date:** 2026-10-08
> **Stores used:** `DUMMY_AUTO_STORE` (id 17620, usage=[] — disposable) and the
> production `Gran_Turismo_Link2` (id 17417) for read-only / identity probes.
> **Status:** COMPLETE — write model confirmed end-to-end.

These findings **overturn parts of the original spec** (§4, §6). The corrected
model is below.

---

## F1. A store has ONE designated id/tag column; the API keys on it

`UpdateRow` and `DeleteRow` do **not** key on an arbitrary column. Each store has
a designated **id / tag column**, and the API uses that column to find the row.

Evidence:

| Store | Column sent as key | Result |
|---|---|---|
| 17620 | `No` | OK |
| 17620 | `uniqueID` | `500 Error: id column data not found` |
| 17620 | `SbjNum` | `500 Error: id column data not found` |
| 17417 | `uniqueID` | OK (`DeleteRow` removed exactly the dummy row) |
| 17417 | `No` | no effect |

So the id column is **store-specific**: `No` on the dummy store, `uniqueID` on
`Gran_Turismo_Link2`. It must be **declared per job** and **verified in preflight**;
it cannot be assumed.

**Decision:** the user declares the id column (IsTag) in the GUI; the system does
**not** auto-discover it. Preflight verifies the declaration: column exists, values
are unique, and a **no-op `UpdateRow`** (full payload, identical values) returns
`{Done: true}` without changing the row. A wrong declaration yields
`500 Error: id column data not found`, which preflight surfaces.

`DeleteRow` takes the tag value as a **path** segment:

```
POST /SubjectStores/DeleteRow/{storeID}/{userTag}     ✅
POST /SubjectStores/DeleteRow/{storeID}?userTag=...   ❌ 500 Runtime Error
```

`userTag` = the **value** of the store's id column for the target row.

## F2. `UpdateRow` is an UPSERT on the id column, with full-row semantics

- `UpdateRow` with an id value that **exists** → replaces the row.
- `UpdateRow` with an id value that **does not exist** → **INSERTs a new row**
  (`UpdateRow No=999 ...` created a brand-new `No=999` row).
- The id column is **immutable**: `UpdateRow No=6` on an existing `No=5` row did
  **not** rename it — it created a second row `No=6`. The id column is the key.
- A **partial** payload defaults the omitted columns:
  `UpdateRow No=1 I7_1_2=X` alone reset `SbjNum`→0, `uniqueID`→0, `I8a_1`→"",
  `Q32`→0. Therefore an update **must send every column**.
- A **complete** payload preserves everything and is a no-op when values are
  identical (verified on 17417: 295 rows in → 295 rows out, values intact).

Consequence: `UpdateRow` is naturally idempotent **provided the full payload is
sent** and the id value already exists.

## F3. `AddRow` cannot create a new column

```
POST AddRow?ZZ_NEWCOL=...   →  500 Error: Column ZZ_NEWCOL doesn't exist
```

Columns must be **pre-defined in STG Studio**. A generator cannot add a column on
the fly. (Applies to the planned `SbjNum` identity column: it must be added in
Studio before adoption.)

## F4. `DeleteRow` always returns `{Done: true}` — even on no match

`DeleteRow` returned `200 {Done: true}` for a non-existent tag. Success **cannot**
be inferred from the response. Verification requires a follow-up `GetStore`.

## F5. `No` is not auto-assigned; a `No`-less row gets `No = 0`

`AddRow` without `No` produced `No: 0`. When the id column is `No`, the system
must assign it (`max(No)+1`). A row whose tag is `0` is unreachable by
`UpdateRow`/`DeleteRow` on such a store (the `0` maps to "no tag").

## F6. End-to-end reconciliation cycle validated (dummy store 17620)

Config used: id/tag column `No`, match column `SbjNum`, content
`uniqueID`,`I7_1_2`,`I8a_1`,`Q32`.

```
seed:  (No1,SbjNum101,Alice) (No2,SbjNum102,Bob) (No3,SbjNum103,Carol)
source: 101 same, 102 changed, 104 new; 103 absent
plan:  ADD 104 | UPDATE 102 | SAME 101 | DELETE 103
apply: AddRow(No=4,SbjNum=104,...) | UpdateRow(No=2,SbjNum=102,...) | DeleteRow/3
result: (No1,101,Alice) (No2,102,BOB) (No4,104,Dave)   ✅
```

Row order preserved for untouched rows (`No` unchanged); the new row appended
with `No = max+1`.

---

## Corrected write model

| Role | Column | Set by | Notes |
|---|---|---|---|
| **API id / tag** | store-specific (`No` / `uniqueID` / …) | system (add), immutable | keys UpdateRow & DeleteRow |
| **Match key** | `SbjNum` (per-interview) | source | must exist in store AND source |
| Content | configured columns | source | compared value-by-value |

Rules confirmed by probe:

1. `AddRow` must send **all** columns incl. the assigned id/tag value.
2. `UpdateRow` must send **all** columns; the id value must already exist; the id
   value is never changed. When the id column is `No`, keying is closed: any
   insert with an unseen `No` would create a row.
3. `DeleteRow` uses the id column **value**; verify by re-reading the store.
4. Columns (incl. a new `SbjNum`) must be **pre-defined in Studio**.
5. Delegating the identity role to the store's own id column is **safer** than
   inventing a parallel column — but that id column is store-specific and must be
   declared and preflight-verified per job.

## Cleanup

- Dummy row `uniqueID=999999` removed from 17417 → back to **295 rows**.
- `DUMMY_AUTO_STORE` (17620) wiped → **0 rows**, ready for reuse.
