# Survey Pairing Out of the Run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the survey-definition read (`GetSurveyXML` + `parse_survey_index`) out of every run and into a one-time, GUI-only **Pairing** prep action, persisting the result (per-column pull term + cached survey index) so runs never download the XML again.

**Architecture:** `ColumnBinding` gains a stored `pull_term`; `SourceConfig` gains a cached `survey_index` (`var → question-level base name`) and `paired_survey_id`. Both live in the registry. A new Pairing button on the Columns tab fetches the XML once and fills them; `Save`/`Approve` persists them. `run_sync` and `run_preflight` read the cached index instead of fetching; an unpaired or stale job **blocks** before any write (fail closed).

**Tech Stack:** Python 3.12, PySide6, `requests`, SQLite (`sqlite3`), `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-09-survey-pairing-separation-design.md`

## Global Constraints

- Python 3.12; `src/` layout; package `store_sync`.
- Tests must pass both `./.venv/bin/python -m pytest -q` and `./.venv/bin/python -m pytest -q -W error`.
- **Do NOT commit, push, or rewrite history.** Each task ends by running the focused tests; the operator commits when ready. Ignore the template's commit steps.
- Fail closed: an unpaired / stale job blocks the run; there is **no** implicit XML fetch and **no** full-export fallback.
- `run_sync` and `run_preflight` must **never** call `get_survey_xml`.
- Config is plain dataclasses with explicit `from_dict`/`to_dict`; the registry stores a subset and reconstructs the rest from dataclass defaults.
- Migration follows the existing `PRAGMA table_info` → `ALTER TABLE ADD COLUMN` pattern in `Registry._add_schedule_columns`.

## Review Focus

Input classes / failure modes the spec implies but no existing test covers; each gets a test in the task that owns the code:

1. **Unpaired job at run time** → must finish `blocked` (exit 2), and `get_survey_xml` must NOT be called. (Task 4)
2. **Stale pairing** (`survey_id` edited after pairing) → must finish `blocked`, not silently use the old index. (Task 4)
3. **A column with an empty `pull_term`** → must finish `blocked` and name the column. (Task 4)
4. **Pairing where a `source_var` is absent from the XML** → reported as unresolved; that column's `pull_term` stays `""` (never a wrong term). (Task 3, Task 7)
5. **Legacy registry row** (pre-migration, no new columns) → reads back unpaired (`survey_index == {}`, `paired_survey_id == ""`, `pull_term == ""`). (Task 2)

---

### Task 1: Config — stored pull term + cached survey index

**Files:**
- Modify: `src/store_sync/core/config.py` (`ColumnBinding`, `SourceConfig`)
- Test: `tests/core/test_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces:
  - `ColumnBinding(source_var: str, store_col: str, kind: str = "text", pull_term: str = "")`
  - `SourceConfig(survey_id: str, survey_index: dict[str, str] = {}, paired_survey_id: str = "")`

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_config.py`:

```python
def test_column_binding_pull_term_roundtrip():
    c = ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1")
    assert ColumnBinding.from_dict(c.to_dict()) == c
    assert c.to_dict()["pull_term"] == "QPM1"


def test_column_binding_pull_term_defaults_empty():
    assert ColumnBinding.from_dict(
        {"source_var": "v", "store_col": "c"}
    ).pull_term == ""


def test_source_config_survey_index_roundtrip():
    s = SourceConfig("SURV", survey_index={"T_QPM1_1": "QPM1"}, paired_survey_id="SURV")
    assert SourceConfig.from_dict(s.to_dict()) == s


def test_source_config_defaults_unpaired():
    s = SourceConfig.from_dict({"survey_id": "SURV"})
    assert s.survey_index == {}
    assert s.paired_survey_id == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_config.py -q -k "pull_term or survey_index"`
Expected: FAIL — `TypeError`/`KeyError` (fields absent).

- [ ] **Step 3: Implement the fields**

In `src/store_sync/core/config.py`:

```python
@dataclass
class SourceConfig:
    survey_id: str
    survey_index: dict[str, str] = field(default_factory=dict)
    paired_survey_id: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SourceConfig":
        return cls(
            survey_id=str(d.get("survey_id", "")),
            survey_index=dict(d.get("survey_index", {})),
            paired_survey_id=str(d.get("paired_survey_id", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "survey_id": self.survey_id,
            "survey_index": dict(self.survey_index),
            "paired_survey_id": self.paired_survey_id,
        }
```

Add `pull_term` to `ColumnBinding` (last field, default `""`), include it in `from_dict` (`str(d.get("pull_term", ""))`) and `to_dict`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_config.py -q`
Expected: PASS.

---

### Task 2: Registry — persist pairing fields + migrate

**Files:**
- Modify: `src/store_sync/core/registry.py` (`_SCHEMA`, `__init__`, `_config_values`, `_replace_columns`, `get`, `_add_schedule_columns`→ new `_add_pairing_columns`)
- Test: `tests/core/test_registry.py`

**Interfaces:**
- Consumes: `ColumnBinding.pull_term`, `SourceConfig.survey_index`, `SourceConfig.paired_survey_id` (Task 1).
- Produces: `Registry.add`/`update`/`get` round-trip the three fields; `jobs.survey_index TEXT`, `jobs.paired_survey_id TEXT`, `job_columns.pull_term TEXT`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_registry.py`:

```python
def test_roundtrips_pairing_fields(tmp_path):
    r = Registry(tmp_path / "reg.db")
    job = JobConfig(
        "link1", StoreConfig(17620, "No"),
        SourceConfig("SURV", survey_index={"T_QPM1_1": "QPM1"}, paired_survey_id="SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1")]),
        WriteConfig(dry_run=True),
    )
    r.add(job)
    got = r.get("link1")
    assert got.source.survey_index == {"T_QPM1_1": "QPM1"}
    assert got.source.paired_survey_id == "SURV"
    assert got.binding.columns[0].pull_term == "QPM1"
    r.close()


def test_legacy_row_reads_back_unpaired(tmp_path):
    import sqlite3
    db = tmp_path / "reg.db"
    con = sqlite3.connect(db)
    con.executescript(
        "CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT UNIQUE,"
        " store_id INTEGER, id_column TEXT, survey_id TEXT, match_source_var TEXT,"
        " match_store_col TEXT, dry_run INTEGER DEFAULT 1, enabled INTEGER DEFAULT 0);"
        "CREATE TABLE job_columns (job_id INTEGER, store_col TEXT, source_var TEXT,"
        " kind TEXT DEFAULT 'text', ord INTEGER, PRIMARY KEY (job_id, ord));"
    )
    con.execute("INSERT INTO jobs (name, store_id, id_column, survey_id,"
                " match_source_var, match_store_col) VALUES ('old', 1, 'No', 'S', 'SbjNum', 'SbjNum')")
    con.execute("INSERT INTO job_columns (job_id, store_col, source_var, ord)"
                " VALUES (1, 'I7_1_2', 'T_QPM1_1', 0)")
    con.commit(); con.close()
    r = Registry(db)
    got = r.get("old")
    assert got.source.survey_index == {}
    assert got.source.paired_survey_id == ""
    assert got.binding.columns[0].pull_term == ""
    cols = {row["name"] for row in r.conn.execute("PRAGMA table_info(jobs)")}
    assert {"survey_index", "paired_survey_id"} <= cols
    r.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_registry.py -q -k "pairing or legacy_row_reads_back_unpaired"`
Expected: FAIL — fields not stored / columns absent.

- [ ] **Step 3: Implement**

- Add to `_SCHEMA` `jobs` DDL: `survey_index TEXT NOT NULL DEFAULT '{}'` and `paired_survey_id TEXT NOT NULL DEFAULT ''`; add `pull_term TEXT NOT NULL DEFAULT ''` to `job_columns`.
- Add a `_PAIRING_COLUMNS` tuple and a `_add_pairing_columns()` method mirroring `_add_schedule_columns` (guard `PRAGMA table_info`); call it in `__init__` after `_add_schedule_columns()`. For `job_columns`, add `pull_term` if absent.
- `_replace_columns`: insert `pull_term` from `col.pull_term`.
- `_config_values`: append `json.dumps(job.source.survey_index)` and `job.source.paired_survey_id`; add the two columns to the `INSERT`/`UPDATE` column lists and placeholder counts in `add`/`update`.
- `get`: read `row["survey_index"]` via `json.loads(... or "{}")` and `row["paired_survey_id"]`; read `pull_term` in the columns query and pass to `ColumnBinding`.
- Add `import json`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_registry.py -q`
Expected: PASS (existing migration tests still green).

---

### Task 3: Fetcher — pairing helper from a cached index

**Files:**
- Modify: `src/store_sync/core/fetcher.py` (add `pair_columns`)
- Test: `tests/core/test_fetcher.py`

**Interfaces:**
- Consumes: `parse_survey_index` (existing), `index: dict[str, str]`.
- Produces: `pair_columns(source_vars: list[str], index: dict[str, str]) -> tuple[dict[str, str], list[str]]` — returns `({source_var: pull_term}, unresolved)` where `pull_term = index.get(var, "")` and `unresolved` lists vars with no entry.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_fetcher.py`:

```python
class TestPairColumns:
    def test_fills_terms_for_resolvable_vars(self):
        from store_sync.core.fetcher import pair_columns
        idx = parse_survey_index(SURVEY_XML)
        terms, unresolved = pair_columns(["T_QPM1_1", "T_QPM1_3", "Q32"], idx)
        assert terms == {"T_QPM1_1": "QPM1", "T_QPM1_3": "QPM1", "Q32": "Q32"}
        assert unresolved == []

    def test_unresolved_var_gets_empty_term_and_is_reported(self):
        from store_sync.core.fetcher import pair_columns
        idx = parse_survey_index(SURVEY_XML)
        terms, unresolved = pair_columns(["T_QPM1_1", "I_351_Q46a"], idx)
        assert terms == {"T_QPM1_1": "QPM1", "I_351_Q46a": ""}
        assert unresolved == ["I_351_Q46a"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_fetcher.py -q -k PairColumns`
Expected: FAIL — `ImportError: cannot import name 'pair_columns'`.

- [ ] **Step 3: Implement**

In `src/store_sync/core/fetcher.py`:

```python
def pair_columns(
    source_vars: Iterable[str], index: dict[str, str]
) -> tuple[dict[str, str], list[str]]:
    """Per-column pull terms from a cached index; unresolved vars reported.

    `pull_term` is `index[var]` when known, else `""` — never a guessed term.
    """
    terms: dict[str, str] = {}
    unresolved: list[str] = []
    for var in source_vars:
        base = index.get(var, "")
        terms[var] = base
        if not base:
            unresolved.append(var)
    return terms, unresolved
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_fetcher.py -q`
Expected: PASS.

---

### Task 4: Sync — read cached index, drop the XML fetch, add the pairing gate

**Files:**
- Modify: `src/store_sync/core/sync.py` (`run_sync`, `_readiness_errors`)
- Modify: `src/store_sync/ui/job_detail.py` (`_STAGE_TEXT`: drop `"survey"`)
- Test: `tests/core/test_sync.py`

**Interfaces:**
- Consumes: `config.source.survey_index`, `config.source.paired_survey_id`, `ColumnBinding.pull_term`.
- Produces: `run_sync` no longer calls `client.get_survey_xml`; an unpaired/stale job returns `SyncResult(status="blocked")` before the pull; the progress stage list no longer contains `"survey"`.

- [ ] **Step 1: Update the shared test helper + write the failing tests**

In `tests/core/test_sync.py`, replace the `config()` helper's `source=` and default `columns=` so a normal job is **paired**:

```python
from store_sync.core.fetcher import parse_survey_index

_PAIRED_INDEX = parse_survey_index(SURVEY_XML)  # {"T_QPM1_1": "QPM1", "Q32": "Q32"}


def config(*, dry_run=True, id_column="No", mode="mapped", columns=None, paired=True):
    if columns is None:
        columns = [
            ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1"),
            ColumnBinding("Q32", "Q32", pull_term="Q32"),
        ]
    source = SourceConfig(
        survey_id="SURV",
        survey_index=(_PAIRED_INDEX if paired else {}),
        paired_survey_id=("SURV" if paired else ""),
    )
    return JobConfig(
        job="link1",
        store=StoreConfig(id=17620, id_column=id_column),
        source=source,
        binding=BindingConfig(mode=mode,
            match=ColumnBinding("SbjNum", "SbjNum"), columns=columns),
        write=WriteConfig(dry_run=dry_run),
    )
```

Update the two progress-order tests (`test_stage_names_and_order_dry_run`, `test_stage_names_and_order_live`) to the new sequence:

```python
assert stages == [
    "start", "ids", "pull", "store",
    "preflight", "plan", "apply", "applied", "proposals", "done",
]
```

Add new tests:

```python
class TestPairingGate:
    def test_unpaired_job_blocks_without_xml(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False, paired=False), client=c, state=st)
        assert res.status == "blocked"
        assert any("not paired" in e for e in res.errors)
        assert [x for x in c.calls if x[0] in ("add", "update")] == []

    def test_run_never_calls_get_survey_xml(self, st):
        c = FakeClient(store=store_rows(), subjects=subjects())
        called = []
        c.get_survey_xml = lambda sid: called.append(sid) or SURVEY_XML
        run_sync(config(dry_run=True), client=c, state=st)
        assert called == []

    def test_stale_pairing_blocks(self, st):
        cfg = config(dry_run=False)
        cfg.source.survey_id = "SURV2"  # edited after pairing
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(cfg, client=c, state=st)
        assert res.status == "blocked"
        assert any("stale" in e for e in res.errors)

    def test_column_without_pull_term_blocks(self, st):
        cfg = config(dry_run=False, columns=[
            ColumnBinding("T_QPM1_1", "I7_1_2", pull_term=""),  # never paired
        ])
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(cfg, client=c, state=st)
        assert res.status == "blocked"
        assert any("pull term" in e for e in res.errors)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_sync.py -q`
Expected: FAIL — old helper shape / `"survey"` stage / no pairing gate.

- [ ] **Step 3: Implement in `sync.py`**

Add a pairing-errors helper:

```python
def _pairing_errors(config) -> list[str]:
    errors: list[str] = []
    src = config.source
    if not src.survey_index:
        errors.append("survey not paired — run Pairing in the Columns tab")
    elif src.paired_survey_id != src.survey_id:
        errors.append("survey pairing is stale — the survey changed since pairing; re-run Pairing")
    for c in config.binding.columns:
        if not c.pull_term:
            errors.append(f"column {c.store_col!r} has no pull term — re-run Pairing")
    return errors
```

In `run_sync`, after `_emit("start", ...)` and before the pull:

```python
pairing_errs = _pairing_errors(config)
if pairing_errs:
    for err in pairing_errs:
        state.record_conflict(run_id, match_value=None, reason=err)
    _finish("blocked", {})
    _emit("done", status="blocked", counts={}, dry_run=dry_run)
    return SyncResult(plan=p, dry_run=dry_run, status="blocked",
                      counts={}, errors=list(pairing_errs), run_id=run_id)
```

Remove the `_emit("survey")` line and replace:

```python
index = parse_survey_index(client.get_survey_xml(config.source.survey_id))
```

with

```python
index = dict(config.source.survey_index)
```

Drop the now-unused `parse_survey_index` import if nothing else uses it.

- [ ] **Step 4: Implement in `job_detail.py`**

Delete the `"survey": "reading survey definition…",` entry from `_STAGE_TEXT`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_sync.py -q`
Expected: PASS.

---

### Task 5: Preflight — cached index + unpaired error

**Files:**
- Modify: `src/store_sync/core/sync.py` (`run_preflight`)
- Test: `tests/core/test_preflight.py`

**Interfaces:**
- Consumes: `config.source.survey_index`.
- Produces: `run_preflight` never calls `get_survey_xml`; an unpaired job yields a report with `ok is False`.

- [ ] **Step 1: Update the test helper + write the failing tests**

In `tests/core/test_preflight.py`, change `make_config`'s `source=` to a paired one:

```python
from store_sync.core.fetcher import parse_survey_index

_XML = ("<Survey><SQ><QuestionID>762</QuestionID><VariableName>QPM1</VariableName></SQ>"
        "<STo><QuestionID>762</QuestionID><VariableName>T_QPM1_1</VariableName></STo>"
        "<SQ><QuestionID>900</QuestionID><VariableName>Q32</VariableName></SQ></Survey>")

def _paired_source():
    return SourceConfig("S", survey_index=parse_survey_index(_XML), paired_survey_id="S")
```

and set `source=_paired_source()` in `make_config` (and any other helper that should be paired). Give default columns a `pull_term` matching the XML.

Add:

```python
def test_unpaired_config_blocks():
    cfg = make_config()
    cfg.source = SourceConfig("S")  # unpaired
    rep = preflight(store_rows=store_rows(), source_records=source_records(), config=cfg)
    assert rep.ok is False
    assert any("not paired" in e for e in rep.errors)
```

Add a `run_preflight` test in `tests/core/test_sync.py`:

```python
def test_run_preflight_never_reads_xml(st):
    from store_sync.core.sync import run_preflight
    called = []
    c = FakeClient(store=store_rows(), subjects=subjects())
    c.get_survey_xml = lambda sid: called.append(sid) or SURVEY_XML
    report, terms = run_preflight(config(), client=c)
    assert called == []
    assert terms["T_QPM1_1"] == "QPM1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_preflight.py tests/core/test_sync.py -q -k "unpaired or run_preflight_never"`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `run_preflight` (`sync.py`):

```python
index = dict(config.source.survey_index)
columns = config.binding.columns
terms, _ = derive_pull_terms(
    [c.source_var for c in columns], index,
    match_var=config.binding.match.source_var if config.binding.match else None,
)
pull_terms = {c.source_var: index.get(c.source_var, "") for c in columns}
```

Remove the `client.get_survey_xml` call. Add the pairing errors to the report before returning:

```python
report.errors.extend(_pairing_errors(config))
```

(So `report.ok` is `False` when unpaired; `pull_terms` are still returned for display.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_preflight.py tests/core/test_sync.py -q`
Expected: PASS.

---

### Task 6: UI model + JobDetail — render stored pull term, add Pairing button

**Files:**
- Modify: `src/store_sync/ui/job_table_model.py`
- Modify: `src/store_sync/ui/job_detail.py`
- Test: `tests/ui/test_job_table_model.py`, `tests/ui/test_job_detail.py`

**Interfaces:**
- Consumes: `ColumnBinding.pull_term`.
- Produces:
  - `JobTableModel` column 2 renders `col.pull_term`; `set_pull_terms(mapping)` writes each column's `pull_term`; `columns()` returns bindings carrying `pull_term`.
  - `JobDetail.pair_requested = Signal()`; a `🔗 Pair with survey XML` button on the Columns tab; `JobDetail.set_pairing(survey_id, index, terms)`; `config_fields()` includes `survey_index` and `paired_survey_id`; `load` restores them and does **not** wipe stored pull terms.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_job_table_model.py`:

```python
def test_pull_term_renders_from_binding():
    m = JobTableModel()
    m.set_columns([ColumnBinding("T_QPM1_1", "I7_1_2", pull_term="QPM1")])
    assert m.data(m.index(0, 2), Qt.DisplayRole) == "QPM1"


def test_set_pull_terms_fills_column_pull_term():
    m = JobTableModel()
    m.set_columns([ColumnBinding("T_QPM1_1", "I7_1_2")])
    m.set_pull_terms({"T_QPM1_1": "QPM1"})
    assert m.columns()[0].pull_term == "QPM1"
```

Append to `tests/ui/test_job_detail.py` (follow the file's existing `JobDetail`/entry fixtures):

```python
def test_pairing_button_emits_signal(detail):
    seen = []
    detail.pair_requested.connect(lambda: seen.append(1))
    detail.pair_button.click()
    assert seen == [1]


def test_set_pairing_stores_index_and_terms(detail):
    detail.set_pairing("SURV", {"T_QPM1_1": "QPM1"}, {"T_QPM1_1": "QPM1"})
    assert detail.config_fields()["paired_survey_id"] == "SURV"
    assert detail.config_fields()["survey_index"] == {"T_QPM1_1": "QPM1"}
    assert detail.binding_model.columns()[0].pull_term == "QPM1"


def test_load_does_not_wipe_stored_pull_term(detail, entry, job_with_pull_term):
    detail.load(entry, job_with_pull_term)
    assert detail.binding_model.columns()[0].pull_term == "QPM1"
```

(If the file lacks an `entry`/`detail` fixture, add a minimal one building a `JobEntry` + `JobConfig`; `job_with_pull_term` sets `ColumnBinding(..., pull_term="QPM1")` and `SourceConfig("SURV", survey_index={...}, paired_survey_id="SURV")`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/ui/test_job_table_model.py tests/ui/test_job_detail.py -q -k "pull_term or pairing or load_does_not_wipe"`
Expected: FAIL.

- [ ] **Step 3: Implement `job_table_model.py`**

- `data`: for `c == 2`, return `col.pull_term` (drop `self._pull_terms` lookups).
- `set_pull_terms(mapping)`: set `col.pull_term = mapping.get(col.source_var, "")` for each column, then emit `dataChanged` over column 2.
- Keep `flags` read-only for column 2. Keep `_pull_terms` only if still referenced; otherwise remove it.

- [ ] **Step 4: Implement `job_detail.py`**

- Add `pair_requested = Signal()` near the other signals.
- In `_columns_tab`, add `self.pair_button = QPushButton("🔗 Pair with survey XML")`, `setObjectName("btn_warning")`, `clicked.connect(self.pair_requested.emit)`, into the bar (before Import).
- Add state `self._survey_index: dict = {}` and `self._paired_survey_id: str = ""` in `__init__`.
- `load(entry, job)`: set `self._survey_index = dict(job.source.survey_index)`, `self._paired_survey_id = job.source.paired_survey_id`; call `self.binding_model.set_columns(job.binding.columns)`; **remove** the `self.binding_model.set_pull_terms({})` line.
- Add `set_pairing(self, survey_id, index, terms)`: store `self._survey_index = dict(index)`, `self._paired_survey_id = survey_id`, then `self.binding_model.set_pull_terms(terms)`.
- `config_fields()`: add `"survey_index": dict(self._survey_index)` and `"paired_survey_id": self._paired_survey_id`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/ui/test_job_table_model.py tests/ui/test_job_detail.py -q`
Expected: PASS.

---

### Task 7: Controller — pairing worker, wiring, carry pairing on save

**Files:**
- Modify: `src/store_sync/gui.py` (`_wire_signals`, `_job_from_detail`, new `_pair_survey`/`_on_pair_ok`/`_on_pair_error`)
- Modify: `src/store_sync/ui/main_window.py` (relay `pair_requested`)
- Test: `tests/ui/test_controller.py`

**Interfaces:**
- Consumes: `JobDetail.pair_requested`, `JobDetail.set_pairing`, `config_fields()["survey_index"|"paired_survey_id"]`, `fetcher.pair_columns`, `parse_survey_index`.
- Produces: `Controller._pair_survey()` runs off-thread; on success fills the grid + stores the index; `_job_from_detail()` builds `SourceConfig` with `survey_index`/`paired_survey_id` and `ColumnBinding`s with `pull_term`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_controller.py`:

```python
def test_pair_survey_fills_terms_off_thread(tmp_path, monkeypatch):
    import store_sync.gui as gui
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    w.job_detail.load(_preflight_entry(), a_job())

    class Client:
        session = types.SimpleNamespace(close=lambda: None)
        def get_survey_xml(self, survey_id):
            return ("<Survey><SQ><QuestionID>762</QuestionID>"
                    "<VariableName>QPM1</VariableName></SQ>"
                    "<STo><QuestionID>762</QuestionID>"
                    "<VariableName>T_QPM1_1</VariableName></STo></Survey>")

    monkeypatch.setattr(c, "_client", lambda: Client())
    c._pair_survey()
    _drain(c, "_pair_thread")
    assert w.job_detail.binding_model.columns()[0].pull_term == "QPM1"
    assert w.job_detail.config_fields()["paired_survey_id"] == "SURV"
    c.stop(); w.close()


def test_pair_survey_reports_unresolved(tmp_path, monkeypatch):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    w.job_detail.load(_preflight_entry(), a_job())  # column var T_QPM1_1

    class Client:
        session = types.SimpleNamespace(close=lambda: None)
        def get_survey_xml(self, survey_id):
            return "<Survey/>"  # nothing resolves

    monkeypatch.setattr(c, "_client", lambda: Client())
    c._pair_survey()
    _drain(c, "_pair_thread")
    assert "unresolved" in w.status_label.text().lower()
    assert w.job_detail.binding_model.columns()[0].pull_term == ""
    c.stop(); w.close()
```

Also assert `_job_from_detail` carries pairing:

```python
def test_job_from_detail_carries_pairing(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    w.job_detail.load(_preflight_entry(), a_job())
    w.job_detail.set_pairing("SURV", {"T_QPM1_1": "QPM1"}, {"T_QPM1_1": "QPM1"})
    job = c._job_from_detail()
    assert job.source.paired_survey_id == "SURV"
    assert job.source.survey_index == {"T_QPM1_1": "QPM1"}
    assert job.binding.columns[0].pull_term == "QPM1"
    c.stop(); w.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/ui/test_controller.py -q -k "pair_survey or carries_pairing"`
Expected: FAIL — `_pair_survey` absent.

- [ ] **Step 3: Implement**

- Add `_pair_thread: QThread | None = None` next to `_preflight_thread`; join it in `_join_worker` (the loop already covers `_workers`, but reset the attr like the others).
- `_wire_signals`: `w.pair_requested.connect(self._pair_survey)`.
- `MainWindow` (`src/store_sync/ui/main_window.py`): add `pair_requested = Signal()` next to `preflight_requested` (line 51) and relay it beside the others (line 129): `self.job_detail.pair_requested.connect(self.pair_requested.emit)`.
- Implement:

```python
def _pair_survey(self) -> None:
    from store_sync.core.fetcher import pair_columns, parse_survey_index
    w = self.window
    try:
        job = self._job_from_detail()
    except ValueError:
        logger.exception("controller: invalid config for pairing")
        w.set_status("config error", _STATUS_FAIL)
        return
    try:
        client = self._client()
    except Exception:  # noqa: BLE001
        logger.exception("controller: pairing client failed")
        w.set_status("pairing error", _STATUS_FAIL)
        w.toast("Pairing error: could not build STG client (check .env)", kind="fail")
        return
    survey_id = job.source.survey_id
    w.set_status("pairing...", _STATUS_WARN)

    def work():
        try:
            index = parse_survey_index(client.get_survey_xml(survey_id))
            terms, unresolved = pair_columns(
                [c.source_var for c in job.binding.columns], index
            )
            return survey_id, index, terms, unresolved
        finally:
            self._close_client(client)

    worker = _NetworkWorker(work)
    worker.ok.connect(self._on_pair_ok)
    worker.failed.connect(self._on_pair_error)
    self._start_worker("_pair_thread", worker, label="pairing")

def _on_pair_ok(self, result) -> None:
    survey_id, index, terms, unresolved = result
    self.window.job_detail.set_pairing(survey_id, index, terms)
    if unresolved:
        self.window.set_status(
            f"paired — {len(unresolved)} unresolved", _STATUS_WARN
        )
        self.window.toast(
            f"Paired, but unresolved: {', '.join(unresolved)}", kind="warn"
        )
    else:
        self.window.set_status("paired", _STATUS_OK)
        self.window.toast("Survey paired — save to persist", kind="ok")

def _on_pair_error(self, exc) -> None:
    msg = _short(exc)
    self.window.set_status(f"pairing error: {msg}", _STATUS_FAIL)
    self.window.toast(f"Pairing error: {msg}", kind="fail")
```

- `_job_from_detail`: build `SourceConfig(fields["survey_id"], survey_index=fields.get("survey_index", {}), paired_survey_id=fields.get("paired_survey_id", ""))`; pass `pull_term` through when building the binding columns — `self.window.job_detail.bindings()` already returns bindings carrying `pull_term`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/ui/test_controller.py -q`
Expected: PASS.

---

### Task 8: Docs + full acceptance

**Files:**
- Modify: `AGENTS.md` (invariants), `CONTEXT.md` (vocabulary), `README.md` (workflow note), `tasks/todo.md` (phase ledger)
- Test: whole suite

**Interfaces:** none (documentation + verification).

- [ ] **Step 1: Add the invariant to `AGENTS.md`**

Under **Hard invariants**, add: *"The survey definition is read only by the Pairing action (prep), never during a run. A job with no pairing — empty `survey_index`, `paired_survey_id != survey_id`, or any empty `pull_term` — is `blocked` before any write; there is no implicit XML fetch and no full-export fallback."*

- [ ] **Step 2: Add vocabulary to `CONTEXT.md`**

Add a **Pairing** entry under Binding: *"The one-time prep action that reads the survey definition and records, per column, the question-level pull term (`QPM1`), plus the cached survey index. Distinct from a run: it happens at prep, once, not on every sync. An unpaired job is blocked."*

- [ ] **Step 3: Note the workflow change in `README.md` and record the phase in `tasks/todo.md`**

Add a short README line: pair a job in the Columns tab before its first run. Add a `## Phase 9 — Survey pairing out of the run` checklist mirroring the tasks, marking them done as they land.

- [ ] **Step 4: Run the whole suite both ways**

Run: `./.venv/bin/python -m pytest -q`
Expected: all pass.

Run: `./.venv/bin/python -m pytest -q -W error`
Expected: all pass, no warnings.

- [ ] **Step 5: Headless smoke**

Run:
```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c \
  "from PySide6.QtWidgets import QApplication as A; from store_sync.ui.main_window import MainWindow as W; a=A.instance() or A([]); w=W(); w.show(); w.close()"
```
Expected: exits 0, no traceback.

---

## Self-Review

- **Spec coverage:** §3 data model → Tasks 1–2; §4 pairing → Tasks 3, 6, 7; §5 run flow → Task 4; §6 preflight → Task 5; §7 CLI → covered by the run gate (Task 4, exit 2 via `status == "blocked"`); §8 tests → distributed across tasks; §9 risks → Review Focus.
- **Type consistency:** `pull_term`, `survey_index`, `paired_survey_id` names are identical across config, registry, fetcher, sync, UI. `pair_columns` returns `(dict, list)`; `set_pairing(survey_id, index, terms)` matches the controller call.
- **Review Focus:** each of the five lines has a test in the owning task (Task 4: unpaired/stale/empty-term/never-XML; Task 3+7: unresolved; Task 2: legacy row).
