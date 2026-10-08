# Probe Findings — Export Filtering & Empty Values

> **Date:** 2026-10-08
> **Store:** `DUMMY_AUTO_STORE` (17620) for write probes; source survey Link1
> `60824d46-9b2e-41e5-8b47-ae11410ee24d` for export.
> **Status:** COMPLETE.

---

## F7. Empty source values are ABSENT from the export, not `""`

`GET /SimpleExport` spreads answers as EAV:
`{Subjects:[{SubjectID, Columns:[{Var, Value}]}]}` (note the capital `Subjects`).
Only `Var` and `Value` keys exist — there is **no** empty flag.

An unanswered var — e.g. an others-specify column like `Q42_TEXT` — does **not
appear** in `Columns[]` at all (measured: `present=1/296, empty=0`). Across the
survey, **528 of 910** vars have partial presence (present in some subjects,
absent in others) — normal skip logic.

Consequence: "var missing from the export" means **empty**, not "do not write".
The flatten step must map a missing configured var to `""`.

## F8. Store accepts & preserves empty values; omitted columns are defaulted

Probes on store 17620:

| Action | Result |
|---|---|
| `AddRow … I8a_1=""` | cell stored as `""` (not dropped) |
| `AddRow` **omitting** `I8a_1` | cell set to default `""` — omission ≠ preserve |
| `UpdateRow … I7_1_2=""` | cell **cleared** to `""` |
| `AddRow … I7_1_2=" "` | whitespace stored **verbatim** — `" "` ≠ `""` |

Consequence: writes must send **every** column; an empty configured value must be
written as `""` (a full-row payload also clears it on update). `""` and whitespace
are distinct; normalisation must be explicit per column.

## F9. `SimpleExport` can return only the needed variables

`SimpleExport` accepts `variableNames` (optional). Observed for the Link1 job:

| `variableNames` | total vars returned | needed vars present |
|---|---|---|
| *(none — default)* | 429 | all |
| `"T_QPM1_1,T_QPM1_2,T_QPM1_3,Q32,SbjNum"` (answer-level) | 91 | **T_QPM1_* missing** ❌ |
| `"QPM1,Q32,SbjNum"` (question-level) | 94 | **all present** ✅ |
| full + local projection | 429 | all |

**Matching rule (empirical, corrected):** the term matches the **base /
question-level variable name**, and the response includes that variable's
**whole family** — answer-level (`T_*`), loop-expanded (`I_*`), and
others-specify (`_TEXT`).

| term | returned | why |
|---|---|---|
| `QPM1` | `T_QPM1_1`, `T_QPM1_2`, `T_QPM1_3` | `QPM1` is the question variable |
| `T_QPM1_2` | none | answer-level name |
| `T_QPM1` / `QPM1_2` | none | not a base name |
| `Q42` | `Q42`, `Q42_TEXT` | base name; family incl. others-specify |
| `Q46a` | all `I_*_Q46a` + `_TEXT` | base name; loop-expanded family |
| `TEXT` | none | a suffix, not a base name |

XML structure that explains it (question `762`):

```
<QuestionID>762  ShortID=QPM1  VariableName=QPM1  Text="QPM1. "     ← question level
   ├─ <STo> VariableName=T_QPM1_1  (answer "Nama")
   ├─ <STo> VariableName=T_QPM1_2  (answer "Unique ID")
   └─ <STo> VariableName=T_QPM1_3  (answer "No HP")
```

So the filter must be given the **question-level** name (`QPM1`), **not** the
answer-level name the store maps (`T_QPM1_1`). The tool derives the pull term from
each configured source var by stripping the answer/loop/suffix adornment to the
base question name.

## F10. ~90 metadata variables always come

Even with `variableNames`, ~90 STG metadata vars are always returned (`SbjNum`,
`SbjNam`, `Date`, `Duration`, `Filter`, `Srvyr`, `FlaggedBy*`, `DeviceID`,
`Complete`, `RunningIndex`, `SID`, …). Filtering removes only the ~340
question/derived vars. (Confirmed by the operator: default vars are expected.)

## Recommended pull strategy

```
1. minimal filter = variableNames with a token term per needed var
     e.g. "QPM1,Q32,SbjNum"     (tokens), not exact underscore names
2. VERIFY the response contains every needed var
   (the request is advisory; the server may silently drop a term)
3. if a needed var is missing (no safe token) → fall back to a full export
   and project locally
4. always project to the configured columns AFTER the pull, regardless of filter
```

Effect for the Link1 job: ~4.5× smaller payload (94 vs 429 vars/subject) with the
same correctness. The projection step is the source of truth; the filter is only
an optimisation.
