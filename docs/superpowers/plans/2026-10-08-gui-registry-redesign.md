# Store Registry + momo-style GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the single-job form into a registered-store application: operators register N stores, run them on demand or on a minute-granular schedule, watch status/history, and stop a running sync — after first fixing the payload correctness bug that the redesign would multiply.

**Architecture:** Land five core-correctness fixes (full-column write payloads, conflict origin, preflight-gated writes, pull window, transport-safe runs) before touching UI. Then add a SQLite `Registry` (config only; run history stays in `State`), a single-worker `RunQueue`, an in-process `Scheduler`, and rebuild the GUI as a `QStackedWidget` (`Dashboard → Store detail → Run detail`) with the DOM-style models bound to `QTableView`s.

**Tech Stack:** Python 3.12, PySide6, requests, SQLite (`sqlite3`), openpyxl, PyYAML, filelock, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-gui-registry-redesign-design.md` (authoritative for the redesign); domain rules remain in `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md`.

## Global Constraints

- Python `>=3.12`; all imports are bare package paths (`from store_sync.core... import ...`) — editable install, **no `sys.path` hacks**.
- Tests live under `tests/<pkg>/`; the whole suite runs with `pytest` from the repo root.
- Run the suite with `./.venv/bin/python -m pytest -q` before **every** commit; the baseline is **182 passed**.
- STG writes must honour probe F2/F8: **`AddRow`/`UpdateRow` reset any omitted column to its default** — every write payload carries the store's full column set.
- Empty value is a value: an absent/`None` source value is written as `""`, never skipped.
- Delete verification is by **re-reading the store**; `DeleteRow` always returns `{Done: true}`.
- Fail-closed: a store-side duplicate match key halts the job; a source-side duplicate skips those rows.
- The identity/id-column value is **never rewritten** to a different value.
- Resource hygiene (AGENTS §3.4): threads are joined in `closeEvent`; DB connections closed; no fire-and-forget threads.
- Credentials come from `.env` (`STG_SERVER`/`STG_USER`/`STG_PASSWORD`); never hardcode.

## Review Focus

Input classes / failure modes the spec implies but no existing test exercises — most likely to bite first:

1. **A store column that is not mapped** must keep its value on write. Today every fixture's store columns exactly equal the mapped columns, so the reset bug is invisible. → pinned in Task 1.
2. **A source-side duplicate match key** must skip those rows and let the run continue, not halt the whole job. → pinned in Task 2.
3. **A transport error** (`ConnectionError`/`Timeout`) mid-run must not leave the `runs` row with a null status. → pinned in Tasks 3 and 4.
4. **Cancelling a run mid-apply** must keep already-applied writes and their tracking, and mark the run `cancelled`. → pinned in Task 8.
5. **A binding-grid cell edit** must reach the model (today the grid is a detached `QTableWidget`). → pinned in Task 13.

---

## Phase A — Core correctness fixes

### Task 1: Writes carry the store's full column set

**Files:**
- Modify: `src/store_sync/core/planner.py` (`Action`, `plan`, payload builders)
- Modify: `src/store_sync/core/sync.py` (pass `store_columns`)
- Test: `tests/core/test_planner.py`

**Interfaces:**
- Consumes: `binding.project(columns, record) -> dict`, `ColumnBinding`.
- Produces: `plan(*, store_rows, source_records, match, columns, id_column, store_columns=None, delete_policy="full_history", tracked=None) -> Plan`. ADD payload = every `store_columns` key (`""` default) overlaid with projected values + match + id. UPDATE payload = `dict(existing_store_row)` overlaid with projected values + match + id.

- [ ] **Step 1: Write the failing test**

Add to `tests/core/test_planner.py`:

```python
class TestFullColumnPayload:
    def test_add_payload_includes_unmapped_store_column(self):
        rows = [{"No": 1, "SbjNum": "101", "I7_1_2": "x", "Q32": 1, "Extra": "keep"}]
        src = [{"SbjNum": "104", "T_QPM1_1": "Dave", "Q32": 44}]
        p = make_plan(store_rows=rows, source_records=src)
        add = next(a for a in p.actions if a.kind == "add")
        assert add.payload["Extra"] == ""       # unmapped col present, blank
        assert set(add.payload) == {"No", "SbjNum", "I7_1_2", "Q32", "Extra"}

    def test_update_payload_preserves_unmapped_store_column(self):
        rows = [{"No": 2, "SbjNum": "102", "I7_1_2": "Bob", "Q32": 22, "Extra": "keep"}]
        src = [{"SbjNum": "102", "T_QPM1_1": "BOB", "Q32": 99}]
        p = make_plan(store_rows=rows, source_records=src)
        upd = next(a for a in p.actions if a.kind == "update")
        assert upd.payload["Extra"] == "keep"   # untouched store value retained
        assert upd.payload["Q32"] == 99
        assert upd.payload["No"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/core/test_planner.py -k FullColumnPayload -v`
Expected: FAIL — `add.payload["Extra"]` raises `KeyError`.

- [ ] **Step 3: Implement full-column payloads in `src/store_sync/core/planner.py`**

Add `store_columns: list[str] | None = None` to `plan(...)`. Default when `None`:
`store_columns = sorted({k for r in store_rows for k in r})`. Replace `_payload` and the update payload construction with:

```python
def _add_payload(rec, key, id_value):
    payload = {c: "" for c in store_columns}
    payload.update(project(columns, rec))
    payload[match.store_col] = key
    payload[id_column] = id_value
    return payload

# update branch:
payload = dict(row)                 # full existing store row
payload.update(project(columns, rec))
payload[match.store_col] = key
payload[id_column] = id_value
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_planner.py -v`
Expected: PASS (existing `test_action_carries_full_payload` still green — its store columns already equal the mapped set).

- [ ] **Step 5: Pass `store_columns` from `sync.py`**

In `run_sync`, after `store_rows = client.get_store(...)`, add
`store_columns = sorted({k for r in store_rows for k in r})` and pass
`store_columns=store_columns` to `plan(...)`.

- [ ] **Step 6: Run the full suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/store_sync/core/planner.py src/store_sync/core/sync.py tests/core/test_planner.py
git commit -m "fix(planner): write payloads carry the store's full column set"
```

---

### Task 2: Conflicts record their origin; only store-side duplicates halt

**Files:**
- Modify: `src/store_sync/core/planner.py` (`Action.side`, `Plan.blocked`, conflict creation)
- Modify: `src/store_sync/core/sync.py` (source-side conflicts → skipped, not blocked)
- Test: `tests/core/test_planner.py`

**Interfaces:**
- Consumes: `Action`, `Plan`.
- Produces: `Action.side: str = ""` (`"store"` | `"source"` on conflicts). `Plan.blocked` is `True` iff any conflict has `side == "store"`. Store rows missing the match column become a store-side conflict.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_planner.py`:

```python
class TestConflictOrigin:
    def test_source_dup_does_not_block(self):
        src = source_records() + [{"SbjNum": "104", "T_QPM1_1": "Dup", "Q32": 1}]
        p = make_plan(source_records=src)
        assert p.blocked is False
        assert any(a.kind == "conflict" and a.side == "source" for a in p.actions)

    def test_store_dup_blocks(self):
        rows = store_rows() + [{"No": 9, "SbjNum": "101", "I7_1_2": "x", "Q32": 1}]
        p = make_plan(store_rows=rows)
        assert p.blocked is True
        assert any(a.kind == "conflict" and a.side == "store" for a in p.actions)

    def test_store_row_missing_match_col_is_store_conflict(self):
        rows = store_rows() + [{"No": 9, "I7_1_2": "x", "Q32": 1}]
        p = make_plan(store_rows=rows)
        assert p.blocked is True
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/core/test_planner.py -k ConflictOrigin -v`
Expected: FAIL — `Action` has no `side`; `p.blocked` is `True` for the source case.

- [ ] **Step 3: Implement in `planner.py`**

- Add `side: str = ""` to `Action`.
- Store-dup loop: `Action("conflict", key, id_column, reason="duplicate match key in store", side="store")`.
- Source-dup loop: `... side="source"`.
- In the store indexing loop, replace the silent `continue` for a row missing `match.store_col` with a store-side conflict: `Action("conflict", "", id_column, reason="store row missing match column", side="store")` (dedupe once, not per row).
- `Plan.blocked` → `any(a.kind == "conflict" and a.side == "store" for a in self.actions)`.

- [ ] **Step 4: Handle source-side conflicts in `sync.py`**

After `p = plan(...)`, keep recording conflicts. The existing `if p.blocked:` gate now only triggers on store-side conflicts. Ensure the source-side skipped keys do not abort: no change needed beyond `blocked`, but set run status `partial` when applied writes exist (already the behaviour in step 6 of `run_sync`).

- [ ] **Step 5: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS (`test_duplicate_match_key_in_source_conflicts` still passes — it only asserts the conflict exists and no add/update for that key).

- [ ] **Step 6: Commit**

```bash
git add src/store_sync/core/planner.py src/store_sync/core/sync.py tests/core/test_planner.py
git commit -m "fix(planner): source-side duplicates skip rows; only store dups halt"
```

---

### Task 3: Run-state readiness gate + pull window

**Files:**
- Modify: `src/store_sync/core/sync.py` (`_readiness_errors`, `_window_start`, `run_sync`)
- Test: `tests/core/test_sync.py`

**Interfaces:**
- Consumes: `binding.check_id_column(rows, column) -> KeyCheck`, `binding.check_match_key(records, key) -> KeyCheck`.
- Produces: `_readiness_errors(config, store_rows) -> list[str]`; `_window_start(window_days: int) -> str | None`. `run_sync(..., should_stop=None)` passes `date_type` and `date_start` to `survey_interview_ids`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_sync.py` (extend `FakeClient` to record `survey_interview_ids` kwargs):

```python
def test_window_and_date_type_passed(self, st):
    c = FakeClient(store=store_rows(), subjects=subjects())
    cfg = config(dry_run=True)
    cfg.source.window_days = 3
    cfg.source.date_type = "Upload"
    run_sync(cfg, client=c, state=st)
    assert c.id_kwargs["date_type"] == "Upload"
    assert c.id_kwargs["date_start"] is not None

def test_missing_id_column_blocks_before_write(self, st):
    c = FakeClient(store=store_rows(), subjects=subjects())
    res = run_sync(config(dry_run=False, id_column="Nope"), client=c, state=st)
    assert res.status == "blocked"
    assert [x for x in c.calls if x[0] in ("add", "update", "delete")] == []

def test_empty_store_blocks(self, st):
    c = FakeClient(store=[], subjects=subjects())
    res = run_sync(config(dry_run=False), client=c, state=st)
    assert res.status == "blocked"
```

Add to `FakeClient.__init__`: `self.id_kwargs = {}`; in `survey_interview_ids(self, survey_id, **kw)`: `self.id_kwargs = kw`.

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/core/test_sync.py -k "window or missing_id or empty_store" -v`
Expected: FAIL.

- [ ] **Step 3: Implement in `sync.py`**

```python
from datetime import datetime, timedelta
from store_sync.core.binding import check_id_column, check_match_key

def _window_start(window_days: int) -> str | None:
    if not window_days or window_days <= 0:
        return None
    return (datetime.utcnow() - timedelta(days=window_days)).strftime("%Y-%m-%d")

def _readiness_errors(config, store_rows) -> list[str]:
    if not store_rows:
        return ["store has no rows (cannot learn its columns; refusing to write)"]
    errs = []
    idc = check_id_column(store_rows, config.store.id_column)
    if not idc.exists:
        errs.append(f"id column {config.store.id_column!r} not present on the store")
    elif not idc.unique:
        errs.append(f"id column {config.store.id_column!r} is not unique")
    m = config.binding.match
    if m and not check_match_key(store_rows, m.store_col).unique:
        errs.append(f"match key {m.store_col!r} is not unique in the store")
    return errs
```

In `run_sync`, build the id call as
`client.survey_interview_ids(config.source.survey_id, date_type=config.source.date_type, date_start=_window_start(config.source.window_days))`.
After loading `store_rows`, compute `errs = _readiness_errors(config, store_rows)`; if non-empty → record them, `finish_run(status="blocked", counts={})`, return `SyncResult(status="blocked", ...)`.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/sync.py tests/core/test_sync.py
git commit -m "fix(sync): gate writes on id/match readiness; honour pull window"
```

---

### Task 4: Transport errors are retried and never leave a null run status

**Files:**
- Modify: `src/store_sync/core/writer.py` (`_WRITE_RETRYABLE`, `_retry`, `apply_plan`)
- Modify: `src/store_sync/core/sync.py` (guaranteed `finish_run`)
- Test: `tests/core/test_writer.py`, `tests/core/test_sync.py`

**Interfaces:**
- Consumes: `stg_client.STGError`, `requests.exceptions.ConnectionError/Timeout/ChunkedEncodingError`.
- Produces: `_retry` catches `(STGError, ReqConnectionError, Timeout, ChunkedEncodingError)`. `apply_plan` catches the same tuple per action and records an error instead of propagating. `run_sync` finishes the run exactly once on every path.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_writer.py`:

```python
class TestTransportRetry:
    def test_retries_transport_error(self):
        import requests
        c = FakeClient(fail_on={"update_row"}, fail_times=1)
        # make the failure a transport error instead of STGError
        c._exception = requests.ConnectionError("dropped")
        p = plan_with(Action("update", "2", "No", 2, {"No": 2}))
        res = apply_plan(c, 17620, p, dry_run=False, max_retries=3, backoff=0)
        assert res.counts["update"] == 1
```

Set `self._exception = STGError("transient")` in `FakeClient.__init__` and raise `self._exception` in `_maybe_fail`.

Add to `tests/core/test_sync.py`:

```python
def test_transport_error_leaves_run_finished(self, st):
    import requests
    class Boom(FakeClient):
        def update_row(self, store_id, values):
            raise requests.ConnectionError("dropped")
    c = Boom(store=store_rows(), subjects=subjects())
    run_sync(config(dry_run=False), client=c, state=st, backoff=0)
    run = st.recent_runs("link1")[0]
    assert run["status"] in ("partial", "error")
    assert run["finished_at"] is not None
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/core/test_writer.py -k TransportRetry tests/core/test_sync.py -k transport -v`
Expected: FAIL.

- [ ] **Step 3: Implement in `writer.py`**

```python
from requests.exceptions import ConnectionError as ReqConnectionError, Timeout, ChunkedEncodingError
_WRITE_RETRYABLE = (STGError, ReqConnectionError, Timeout, ChunkedEncodingError)
```

`_retry`: catch `_WRITE_RETRYABLE`. `apply_plan` action loop: `except _WRITE_RETRYABLE as e: res.errors.append(...); continue`.

- [ ] **Step 4: Guarantee `finish_run` in `sync.py`**

Wrap the body with a one-shot finisher:

```python
finished = False
def _finish(status_, counts_):
    nonlocal finished
    if not finished:
        state.finish_run(run_id, status=status_, counts=counts_)
        finished = True
```

Every terminal return calls `_finish(...)`; wrap the whole body in `try: ... except Exception as e: errors.append(...); _finish("error", {}) ... return SyncResult(...)` with a `finally: _finish("error", {})` as the last resort.

- [ ] **Step 5: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/store_sync/core/writer.py src/store_sync/core/sync.py tests/core/test_writer.py tests/core/test_sync.py
git commit -m "fix(writer): retry transport errors; never leave a null run status"
```

---

### Task 5: Nits — writer id column, empty-store preflight, mapping half-rows, CLI exit code

**Files:**
- Modify: `src/store_sync/core/writer.py:84` (no `id_col or "No"` fallback)
- Modify: `src/store_sync/core/preflight.py` (explicit empty-store error)
- Modify: `src/store_sync/ui/mapping_io.py:70-73` (half-filled rows are errors)
- Test: `tests/core/test_writer.py`, `tests/core/test_preflight.py`, `tests/ui/test_mapping_io.py`

**Interfaces:**
- Produces: `apply_plan` reports `"id column unknown; delete verification skipped"` when `id_column is None` and deletes were issued (no hardcoded `"No"`). `preflight` adds `"store has no rows"` when `store_rows == []`. `parse_mapping` appends `f"incomplete row {n}: store_col/source_var both required"` for a half-filled row.

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_writer.py
def test_unknown_id_column_does_not_verify_with_hardcoded_No(self):
    c = FakeClient(store_after=[{"No": 3}])
    p = plan_with(Action("delete", "3", "No", 3, {}))
    res = apply_plan(c, 17620, p, dry_run=False, id_column=None)
    assert any("id column unknown" in e for e in res.errors)

# tests/core/test_preflight.py  (add a module-level minimal config helper)
def test_empty_store_reports_no_rows():
    cfg = JobConfig(job="j", store=StoreConfig(id=1, id_column="No"),
                    source=SourceConfig(survey_id="S"),
                    binding=BindingConfig(match=ColumnBinding("SbjNum", "SbjNum"),
                                          columns=[ColumnBinding("V", "A")]),
                    write=WriteConfig())
    rep = preflight(store_rows=[], source_records=[{"SbjNum": "1", "V": "x"}], config=cfg)
    assert any("store has no rows" in e for e in rep.errors)

# tests/ui/test_mapping_io.py
def test_half_filled_row_is_rejected(tmp_path):
    from openpyxl import Workbook
    from store_sync.ui.mapping_io import HEADERS, parse_mapping
    p = tmp_path / "m.xlsx"
    wb = Workbook(); ws = wb.active
    ws.append(HEADERS)
    ws.append(["A", "", "text"])   # store col only -> incomplete
    wb.save(p)
    cols, errors = parse_mapping(p, ["A"], ["V"])
    assert any("incomplete row" in e for e in errors)
```


- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/core/test_writer.py -k hardcoded tests/core/test_preflight.py -k no_rows tests/ui/test_mapping_io.py -k half tests/test_cli.py -k exit_code -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `writer.py`: delete `id_col = id_col or "No"`; when `deleted_tags and id_col is None`, append `"id column unknown; delete verification skipped"` and skip the re-read.
- `preflight.py`: at the top, `if not store_rows: rep.errors.append("store has no rows")`.
- `mapping_io.py`: replace the silent `continue` on a half-filled row with an error append.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/writer.py src/store_sync/core/preflight.py src/store_sync/ui/mapping_io.py tests/
git commit -m "fix: writer/preflight/mapping nits from branch review"
```

---

## Phase B — Registry

### Task 6: `Registry` — schema + config CRUD

**Files:**
- Create: `src/store_sync/core/registry.py`
- Test: `tests/core/test_registry.py`

**Interfaces:**
- Consumes: `JobConfig`, `StoreConfig`, `SourceConfig`, `BindingConfig`, `ColumnBinding`, `WriteConfig`.
- Produces:
  - `@dataclass JobEntry`: `name, store_id, id_column, survey_id, match_source_var, match_store_col, window_days, date_type, delete_policy, dry_run, enabled, schedule, column_count, last_status, last_finished_at`.
  - `class Registry: __init__(path); close()`
  - `add(job: JobConfig, *, schedule: str = "", enabled: bool = False) -> int`
  - `update(name: str, job: JobConfig, *, schedule: str | None = None, enabled: bool | None = None) -> None`
  - `delete(name: str) -> None`
  - `get(name: str) -> JobConfig`
  - `get_entry(name: str) -> JobEntry`
  - `list_entries() -> list[JobEntry]`
  - `names() -> list[str]`
  - `set_enabled(name, enabled: bool) -> None`
  - `set_schedule(name, schedule: str) -> None`

- [ ] **Step 1: Write the failing test**

```python
"""Tests for store_sync.core.registry — persisted store configs."""
from store_sync.core.config import (BindingConfig, ColumnBinding, JobConfig,
    SourceConfig, StoreConfig, WriteConfig)
from store_sync.core.registry import Registry

def a_job(name="link1", store_id=17620):
    return JobConfig(name, StoreConfig(store_id, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig(dry_run=True))

def test_add_get_roundtrip(tmp_path):
    r = Registry(tmp_path / "reg.db")
    r.add(a_job(), schedule="*/5 * * * *", enabled=True)
    job = r.get("link1")
    assert job.store.id == 17620
    assert job.binding.columns[0].store_col == "I7_1_2"
    e = r.get_entry("link1")
    assert e.enabled is True and e.schedule == "*/5 * * * *" and e.column_count == 1
    r.close()

def test_unique_name(tmp_path):
    import pytest
    r = Registry(tmp_path / "reg.db")
    r.add(a_job())
    with pytest.raises(Exception):
        r.add(a_job())
    r.close()

def test_update_replaces_columns(tmp_path):
    r = Registry(tmp_path / "reg.db")
    r.add(a_job())
    r.update("link1", a_job(store_id=9), schedule="0 2 * * *", enabled=False)
    assert r.get("link1").store.id == 9
    assert r.get_entry("link1").schedule == "0 2 * * *"
    r.close()

def test_delete_removes(tmp_path):
    r = Registry(tmp_path / "reg.db")
    r.add(a_job())
    r.delete("link1")
    assert r.names() == []
    r.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/core/test_registry.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `registry.py`**

Create the tables from spec §4 in `_SCHEMA` (execute in `__init__`). `add`/`update` write the `jobs` row and replace all `job_columns` in one transaction. `get` reconstructs a `JobConfig`. `list_entries` returns `JobEntry` rows with `column_count` from a `COUNT` subquery and `last_status`/`last_finished_at` joined from the most recent `runs` row for that job name, ordered by `name`. `set_enabled`/`set_schedule` are single-column `UPDATE`s that also touch `updated_at`.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/registry.py tests/core/test_registry.py
git commit -m "feat(registry): persistent store configs + schedule/enabled"
```

---

## Phase C — Run queue + scheduler

### Task 7: Cancellable pull and apply

**Files:**
- Modify: `src/store_sync/core/stg_client.py` (`Cancelled`, `simple_export(should_stop=...)`)
- Modify: `src/store_sync/core/writer.py` (`WriteResult.cancelled`, `apply_plan(should_stop=...)`)
- Modify: `src/store_sync/core/sync.py` (`run_sync(should_stop=...)`)
- Test: `tests/core/test_stg_client.py`, `tests/core/test_writer.py`, `tests/core/test_sync.py`

**Interfaces:**
- Produces: `class Cancelled(Exception)` in `stg_client`. `simple_export(..., should_stop: Callable[[], bool] | None = None)` raises `Cancelled` between chunks. `WriteResult.cancelled: bool = False`; `apply_plan(..., should_stop=None)` sets it and stops before the next action. `run_sync(..., should_stop=None)` returns `SyncResult(status="cancelled")`; already-applied writes stay tracked.

- [ ] **Step 1: Write the failing tests**

```python
# tests/core/test_writer.py
def test_stop_before_second_action_keeps_first():
    c = FakeClient()
    p = plan_with(Action("add", "1", "No", 1, {"No": 1}),
                  Action("update", "2", "No", 2, {"No": 2}))
    calls = {"n": 0}
    def stop():
        calls["n"] += 1
        return calls["n"] > 1   # allow the first, stop before the second
    res = apply_plan(c, 17620, p, dry_run=False, should_stop=stop)
    assert res.cancelled is True
    assert res.counts["add"] == 1 and res.counts.get("update", 0) == 0

# tests/core/test_sync.py
def test_cancelled_run_status_and_tracking(self, st):
    c = FakeClient(store=store_rows(), subjects=subjects())
    state = {"n": 0}
    def stop():
        state["n"] += 1
        return state["n"] > 1
    res = run_sync(config(dry_run=False), client=c, state=st, should_stop=stop)
    assert res.status == "cancelled"
    assert st.recent_runs("link1")[0]["status"] == "cancelled"
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/core/test_writer.py -k stop_before tests/core/test_sync.py -k cancelled_run -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

- `stg_client.py`: `class Cancelled(Exception)`; in `simple_export`'s chunk loop, `if should_stop and should_stop(): raise Cancelled()`.
- `writer.py`: `WriteResult.cancelled = False`; `apply_plan(..., should_stop=None)`; at the top of each action iteration, `if should_stop and should_stop(): res.cancelled = True; break`.
- `sync.py`: `run_sync(..., should_stop=None)`; pass it into `pull(..., should_stop=should_stop)` and `apply_plan(..., should_stop=should_stop)`. Wrap the pull in `try/except Cancelled:` → finish `cancelled`, return. After apply, if `wres.cancelled` → finish `cancelled`.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/stg_client.py src/store_sync/core/writer.py src/store_sync/core/sync.py tests/
git commit -m "feat(sync): cooperative cancellation between chunks and writes"
```

---

### Task 8: `RunQueue` — single global worker

**Files:**
- Create: `src/store_sync/core/run_queue.py`
- Test: `tests/core/test_run_queue.py`

**Interfaces:**
- Produces:
  - `@dataclass QueueState: active: str | None; pending: list[str]`
  - `class RunQueue: __init__(self, run_fn, *, poll: float = 0.05)`
    where `run_fn(name: str, should_stop: Callable[[], bool]) -> object`.
  - `start()`, `shutdown()`
  - `enqueue(name, *, reason="manual") -> bool` (False if already active/pending)
  - `stop_current()`, `stop_all()`
  - `snapshot() -> QueueState`
  - `subscribe(cb)` — `cb(QueueState)` on every change

- [ ] **Step 1: Write the failing test**

```python
"""Tests for store_sync.core.run_queue — one active run, FIFO, dedupe."""
import threading, time
from store_sync.core.run_queue import RunQueue

def test_runs_one_at_a_time_and_dedupes():
    ran, gate = [], threading.Event()
    def run_fn(name, should_stop):
        ran.append(name)
        gate.wait(2)
        return name
    q = RunQueue(run_fn)
    q.start()
    assert q.enqueue("a") is True
    assert q.enqueue("a") is False        # dedupe
    assert q.enqueue("b") is True
    time.sleep(0.05)
    assert q.snapshot().active == "a"
    assert "b" in q.snapshot().pending
    gate.set()
    time.sleep(0.2)
    q.shutdown()
    assert ran == ["a", "b"]

def test_stop_current_sets_flag():
    seen = {}
    started = threading.Event()
    def run_fn(name, should_stop):
        started.set()
        while not should_stop():
            time.sleep(0.01)
        seen["stopped"] = True
        return name
    q = RunQueue(run_fn)
    q.start()
    q.enqueue("a")
    started.wait(2)
    q.stop_current()
    time.sleep(0.1)
    q.shutdown()
    assert seen.get("stopped") is True
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/core/test_run_queue.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `run_queue.py`**

A `threading.Thread(daemon=True)` worker consuming a `queue.Queue`. Track `_active` and `_pending` (a `dict`/`list`) under a lock; dedupe by membership. `stop_current` sets a `threading.Event` that `should_stop` closes over; cleared before each run. `shutdown` enqueues a sentinel and joins.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/run_queue.py tests/core/test_run_queue.py
git commit -m "feat(run_queue): single global worker with dedupe + stop"
```

---

### Task 9: `Scheduler` — cron, minute granularity

**Files:**
- Create: `src/store_sync/core/scheduler.py`
- Test: `tests/core/test_scheduler.py`

**Interfaces:**
- Produces: `is_due(schedule: str, now: datetime) -> bool` (5-field cron: `*`, `a`, `a-b`, `a,b`, `*/n`, `a-b/n`; `''` → never due). `class Scheduler: __init__(self, registry, enqueue, *, now_fn=None); tick() -> list[str]` (returns the names it enqueued, firing each job at most once per minute); `run_forever(interval=30)`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for store_sync.core.scheduler — minute-granular cron."""
from datetime import datetime
from store_sync.core.scheduler import is_due, Scheduler

def test_is_due_every_five_minutes():
    assert is_due("*/5 * * * *", datetime(2026, 10, 8, 3, 10)) is True
    assert is_due("*/5 * * * *", datetime(2026, 10, 8, 3, 11)) is False

def test_is_due_daily_at_two():
    assert is_due("0 2 * * *", datetime(2026, 10, 8, 2, 0)) is True
    assert is_due("0 2 * * *", datetime(2026, 10, 8, 2, 1)) is False

def test_blank_schedule_never_due():
    assert is_due("", datetime(2026, 10, 8, 2, 0)) is False

class FakeRegistry:
    def __init__(self, entries): self._e = entries
    def list_entries(self): return self._e

class E:
    def __init__(self, name, enabled, schedule):
        self.name, self.enabled, self.schedule = name, enabled, schedule

def test_tick_fires_once_per_minute():
    fired = []
    s = Scheduler(FakeRegistry([E("a", True, "* * * * *")]), fired.append,
                  now_fn=lambda: datetime(2026, 10, 8, 3, 10, 5))
    assert s.tick() == ["a"]
    assert s.tick() == []          # same minute
    s._now = lambda: datetime(2026, 10, 8, 3, 11, 5)
    assert s.tick() == ["a"]

def test_disabled_not_fired():
    s = Scheduler(FakeRegistry([E("a", False, "* * * * *")]), lambda n: None,
                  now_fn=lambda: datetime(2026, 10, 8, 3, 10))
    assert s.tick() == []
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/core/test_scheduler.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `scheduler.py`**

`is_due` parses 5 space-separated fields against `now.minute, now.hour, now.day, now.month, now.weekday()` (Mon=0 → convert cron `0/7`=Sun). A `_field_matches(spec, value)` helper handles `*`, ints, ranges, lists, `*/n`. `tick` iterates enabled entries, computes the current minute key `now.strftime("%Y-%m-%dT%H:%M")`, skips a job already fired at that key (dict `_last`), and returns the names it enqueued.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/scheduler.py tests/core/test_scheduler.py
git commit -m "feat(scheduler): in-app minute-granular cron"
```

---

## Phase D — GUI rebuild

### Task 10: Move `JobTableModel` to its own module + a dashboard list model

**Files:**
- Create: `src/store_sync/ui/job_table_model.py` (move `JobTableModel` from `main_window.py` unchanged)
- Create: `src/store_sync/ui/models.py` (`JobListModel`, `_DictTableModel`)
- Modify: `src/store_sync/ui/main_window.py` (import `JobTableModel` from the new module)
- Test: `tests/ui/test_job_table_model.py`

**Interfaces:**
- Produces: `JobTableModel` (same API as today: `set_columns`, `columns`, `set_pull_terms`, `add_row`, `remove_row`, `setData`). `JobListModel` over `list[JobEntry]` with columns `["Name","Store","Survey","Schedule","Enabled","Last status","Last run"]` and `entry_at(row) -> JobEntry`. `_DictTableModel(fields)` unchanged.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the GUI models (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from store_sync.core.config import ColumnBinding
from store_sync.ui.job_table_model import JobTableModel
from store_sync.ui.models import JobListModel

_app = QApplication.instance() or QApplication([])

def test_job_table_edit_reaches_model():
    m = JobTableModel()
    m.set_columns([ColumnBinding("", "", "text")])
    m.setData(m.index(0, 0), "I7_1_2", Qt.EditRole)
    m.setData(m.index(0, 1), "T_QPM1_1", Qt.EditRole)
    assert m.columns()[0].store_col == "I7_1_2"
    assert m.columns()[0].source_var == "T_QPM1_1"

def test_job_list_model_rows():
    class E:
        name="a"; store_id=1; survey_id="s"; schedule="* * * * *"
        enabled=True; last_status="ok"; last_finished_at="t"
    m = JobListModel()
    m.set_entries([E()])
    assert m.rowCount() == 1
    assert m.entry_at(0).name == "a"
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/ui/test_job_table_model.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement**

Cut `JobTableModel` from `main_window.py` into `job_table_model.py` (verbatim). In `main_window.py`, `from store_sync.ui.job_table_model import JobTableModel`. Add `JobListModel` (read-only `QAbstractTableModel` over `JobEntry`, `Enabled` rendered as `"yes"/"no"`) and `_DictTableModel` to `models.py`; `main_window.py` imports `_DictTableModel` from there.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/ui/job_table_model.py src/store_sync/ui/models.py src/store_sync/ui/main_window.py tests/ui/test_job_table_model.py
git commit -m "refactor(ui): split table models into focused modules"
```

---

### Task 11: Dashboard widget

**Files:**
- Create: `src/store_sync/ui/dashboard.py`
- Test: `tests/ui/test_dashboard.py`

**Interfaces:**
- Produces: `class Dashboard(QWidget)` with signals `new_requested`, `run_requested(str)`, `stop_requested()`, `edit_requested(str)`, `delete_requested(str)`, `schedules_requested`, `refresh_requested`, `job_activated(str)`. Public: `set_entries(list[JobEntry])`, `set_queue(active: str | None, pending: list[str])`, `set_stats(total, enabled, running, failed)`, `selected_name() -> str | None`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the dashboard (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from store_sync.ui.dashboard import Dashboard

_app = QApplication.instance() or QApplication([])

class E:
    def __init__(self, name):
        self.name=name; self.store_id=1; self.survey_id="s"
        self.schedule=""; self.enabled=False
        self.last_status=None; self.last_finished_at=None

def test_rows_and_selection():
    d = Dashboard()
    d.set_entries([E("a"), E("b")])
    assert d.model.rowCount() == 2
    d.table.selectRow(1)
    assert d.selected_name() == "b"

def test_queue_strip_and_stats():
    d = Dashboard()
    d.set_queue("a", ["b", "c"])
    d.set_stats(total=3, enabled=2, running=1, failed=0)
    assert "a" in d.queue_label.text()
    assert "b" in d.queue_label.text()
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/ui/test_dashboard.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `dashboard.py`**

Stat-card row + `queue_label` + a `QTableView` over `JobListModel` (`self.model`, `self.table`) + toolbar (`➕ New`, `▶ Run now`, `⏹ Stop`, `✏ Edit`, `🗑 Delete`, `⏰ Schedules`, `🔄 Refresh`). Toolbar buttons emit the signals; `doubleClicked` emits `job_activated(name)`. Use DESIGN.md tokens (gradient header is in the main window; this is the body).

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/ui/dashboard.py tests/ui/test_dashboard.py
git commit -m "feat(ui): store dashboard (table + stats + queue strip)"
```

---

### Task 12: Store-detail widget (binding `QTableView`)

**Files:**
- Create: `src/store_sync/ui/job_detail.py`
- Test: `tests/ui/test_job_detail.py`

**Interfaces:**
- Consumes: `JobTableModel`, `_DictTableModel`.
- Produces: `class JobDetail(QWidget)` with signals `back`, `save_requested`, `preflight_requested`, `approve_requested`, `run_requested`, `stop_requested`, `import_requested`, `export_requested`. Public: `load(entry, job)`, `bindings() -> list[ColumnBinding]`, `set_pull_terms(dict)`, `set_history(rows)`, `set_preflight(rows)`, `set_conflicts(rows)`, `approve()`, `config_fields() -> dict`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the store-detail pane (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from store_sync.core.config import (BindingConfig, ColumnBinding, JobConfig,
    SourceConfig, StoreConfig, WriteConfig)
from store_sync.ui.job_detail import JobDetail

_app = QApplication.instance() or QApplication([])

def a_job():
    return JobConfig("a", StoreConfig(17620, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig())

class E:
    name="a"; store_id=17620; survey_id="SURV"; schedule=""
    enabled=False; last_status=None; last_finished_at=None; column_count=1

def test_load_and_edit_binding():
    d = JobDetail()
    d.load(E(), a_job())
    assert d.bindings()[0].store_col == "I7_1_2"
    d.binding_model.setData(d.binding_model.index(0, 0), "I8a_1", Qt.EditRole)
    assert d.bindings()[0].store_col == "I8a_1"

def test_approve_enables_and_signals():
    d = JobDetail()
    d.load(E(), a_job())
    seen = []
    d.approve_requested.connect(lambda: seen.append(1))
    d.approve()
    assert seen == [1]
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/ui/test_job_detail.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `job_detail.py`**

Tabbed `QTabWidget`: **Config** (line edits for store id, id column, survey id, match var, window days, date type; delete-policy combo; dry-run checkbox), **Columns** (`QTableView` with `self.binding_model = JobTableModel()` — the view is bound to the model, edits persist), **Schedule** (enabled checkbox + cron line edit + preview label), **History** (`_DictTableModel`), **Conflicts** (`_DictTableModel`). Bottom action rail: Back · Run preflight · Approve binding · Save · Run now · Stop. `load(entry, job)` fills everything.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/ui/job_detail.py tests/ui/test_job_detail.py
git commit -m "feat(ui): store detail with model-bound binding grid"
```

---

### Task 13: Run-detail widget + New/Edit dialog

**Files:**
- Create: `src/store_sync/ui/run_detail.py`
- Create: `src/store_sync/ui/dialogs.py`
- Test: `tests/ui/test_run_detail.py`

**Interfaces:**
- Produces: `class RunDetail(QWidget)` with signal `back`, and `load(run: dict, conflicts: list[dict])`. `class JobDialog(QDialog)` — fields matching `JobEntry` + a `JobTableModel` grid; `dialog.job -> JobConfig`, `dialog.entry_fields() -> dict` (`name, schedule, enabled`), `dialog.schedule`, `dialog.enabled`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for run detail + job dialog (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from store_sync.ui.run_detail import RunDetail
from store_sync.ui.dialogs import JobDialog

_app = QApplication.instance() or QApplication([])

def test_run_detail_loads():
    d = RunDetail()
    d.load({"id": 1, "status": "ok", "counts": {"add": 2}, "errors": []},
           [{"match_value": "1", "reason": "dup"}])
    assert "ok" in d.summary_label.text()

def test_job_dialog_builds_config():
    dlg = JobDialog()
    dlg.name_edit.setText("a")
    dlg.store_id_edit.setText("17620")
    dlg.id_column_edit.setText("No")
    dlg.survey_id_edit.setText("SURV")
    dlg.match_var_edit.setText("SbjNum")
    dlg.binding_model.set_columns([ColumnBinding("V", "A")])
    j = dlg.job()
    assert j.job == "a" and j.store.id == 17620
    assert j.binding.columns[0].store_col == "A"
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/ui/test_run_detail.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`run_detail.py`: summary label + counts/errors/conflicts tables (`_DictTableModel`) + log `QTextEdit`; Back button emits `back`. `dialogs.py`: `JobDialog` with `QLineEdit` fields (`name_edit`, `store_id_edit`, `id_column_edit`, `survey_id_edit`, `match_var_edit`, `window_days_edit`, `date_type_edit`), a delete-policy combo, a dry-run checkbox, an enabled checkbox, a schedule line edit, a `JobTableModel` + `QTableView`, OK/Cancel, and `job()` / `entry_fields()`.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/ui/run_detail.py src/store_sync/ui/dialogs.py tests/ui/test_run_detail.py
git commit -m "feat(ui): run detail pane + new/edit store dialog"
```

---

### Task 14: Threads (`RunThread`, `SchedulerThread`)

**Files:**
- Create: `src/store_sync/ui/run_thread.py`
- Create: `src/store_sync/ui/scheduler_thread.py`
- Test: `tests/ui/test_threads.py`

**Interfaces:**
- Produces: `class RunThread(QThread)` — `__init__(run_fn, name)`, signal `finished_result(object)` (a `SyncResult`), `stop()` sets a `threading.Event`. `class SchedulerThread(QThread)` — `__init__(scheduler)`, signal `due(str)`, `stop()`; its `run()` loops `scheduler.tick()` and emits `due(name)` for each, sleeping between ticks.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the Qt run/scheduler threads (offscreen Qt)."""
import os, time, threading
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication
from store_sync.ui.run_thread import RunThread

_app = QApplication.instance() or QApplication([])

def test_run_thread_emits_result():
    got = []
    def run_fn(name, should_stop):
        return f"done:{name}"
    t = RunThread(run_fn, "a")
    t.finished_result.connect(got.append)
    t.start()
    t.wait(3000)
    QCoreApplication.processEvents()
    assert got == ["done:a"]

def test_run_thread_stop_requests_cancellation():
    started = threading.Event()
    def run_fn(name, should_stop):
        started.set()
        while not should_stop():
            time.sleep(0.01)
        return "stopped"
    t = RunThread(run_fn, "a")
    t.start()
    started.wait(2)
    t.stop()
    assert t.wait(3000)
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/ui/test_threads.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

`RunThread.run()` calls `self._run_fn(self._name, self._stop.is_set)` then emits `finished_result(result)`. `SchedulerThread.run()` loops while not stopped: for each name in `scheduler.tick()` emit `due(name)`, then `time.sleep(interval)` in small slices so `stop()` is responsive.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/ui/run_thread.py src/store_sync/ui/scheduler_thread.py tests/ui/test_threads.py
git commit -m "feat(ui): Qt threads for running and scheduling"
```

---

### Task 15: Rewrite `main_window.py` as a stacked shell

**Files:**
- Modify: `src/store_sync/ui/main_window.py` (replace the flat window)
- Test: `tests/ui/test_main_window.py` (rewrite)

**Interfaces:**
- Consumes: `Dashboard`, `JobDetail`, `RunDetail`.
- Produces: `class MainWindow(QMainWindow)` with `self.dashboard`, `self.job_detail`, `self.run_detail`, `self.stack`; `show_dashboard()`, `show_job(name)`, `show_run(run_id)`; header breadcrumb; `closeEvent` quits+waits owned threads. Signals proxied from the active page: `new_requested`, `run_requested(str)`, `stop_requested`, `edit_requested(str)`, `delete_requested(str)`, `save_requested`, `preflight_requested`, `approve_requested`, `import_requested`, `export_requested`, `refresh_requested`, `back`.

- [ ] **Step 1: Write the failing test (rewrite `tests/ui/test_main_window.py`)**

```python
"""Tests for the stacked main window (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from store_sync.ui.main_window import MainWindow

_app = QApplication.instance() or QApplication([])

def test_stacked_navigation():
    w = MainWindow()
    assert w.stack.currentWidget() is w.dashboard
    w.show_job("a")
    assert w.stack.currentWidget() is w.job_detail
    w.show_run(1)
    assert w.stack.currentWidget() is w.run_detail
    w.show_dashboard()
    assert w.stack.currentWidget() is w.dashboard
    w.close()

def test_run_request_proxied():
    w = MainWindow()
    seen = []
    w.run_requested.connect(seen.append)
    w.dashboard.run_requested.emit("a")
    assert seen == ["a"]
    w.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/ui/test_main_window.py -v`
Expected: FAIL (the old window API is gone).

- [ ] **Step 3: Implement `main_window.py`**

`QMainWindow` with a gradient header (title + breadcrumb + status dot), `QStackedWidget(self.stack)` holding `self.dashboard`, `self.job_detail`, `self.run_detail`, and the action rail on the dashboard/detail pages (owned by those widgets). Connect each page's signals to the window's proxy signals. `closeEvent` calls `self._stop_owned_threads()` (to be set by the controller) and `super().closeEvent(event)`.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS (old single-job tests are replaced by the new ones).

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/ui/main_window.py tests/ui/test_main_window.py
git commit -m "feat(ui): stacked dashboard/detail/run shell"
```

---

### Task 16: Controller wiring (`gui.py`) — Registry + RunQueue + Scheduler

**Files:**
- Modify: `src/store_sync/gui.py` (rewrite `Controller`)
- Test: `tests/ui/test_controller.py`

**Interfaces:**
- Consumes: `Registry`, `State`, `RunQueue`, `Scheduler`, `SchedulerThread`, `MainWindow`, `run_sync`, `run_preflight`, `STGClient`.
- Produces: `class Controller` wiring the dashboard/detail/run pages to the registry and queue. Public: `controller.start()` (starts worker + scheduler threads), `controller.stop()` (idempotent; used by `closeEvent`), `controller.run_now(name)`, `controller.save(name, job, *, schedule, enabled)`, `controller.delete(name)`, `controller.list_entries()`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the GUI controller wiring (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from store_sync.core.config import (BindingConfig, ColumnBinding, JobConfig,
    SourceConfig, StoreConfig, WriteConfig)
from store_sync.core.registry import Registry
from store_sync.ui.main_window import MainWindow
from store_sync.gui import Controller

_app = QApplication.instance() or QApplication([])

def a_job(name="a"):
    return JobConfig(name, StoreConfig(17620, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig(dry_run=True))

def test_save_lists_on_dashboard(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)
    assert c.list_entries()[0].name == "a"
    w.show_dashboard()
    assert w.dashboard.model.rowCount() == 1
    c.stop(); w.close()

def test_run_now_enqueues(tmp_path):
    w = MainWindow()
    calls = []
    c = Controller(w, db_path=tmp_path / "s.db",
                   run_fn=lambda name, stop: calls.append(name))
    c.save("a", a_job(), schedule="", enabled=False)
    c.start()
    c.run_now("a")
    import time; time.sleep(0.2)
    assert calls == ["a"]
    c.stop(); w.close()
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/ui/test_controller.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `Controller`**

`Controller(window, *, db_path=None, run_fn=None, now_fn=None)`. Default `db_path = Path.home()/".stg_store_sync"/"state.db"`; open `State(db_path)` and `Registry(db_path)`. Default `run_fn` builds an `STGClient` from `.env`, loads the job from the registry, and calls `run_sync(job, client, state, should_stop=stop, throttle=0.05)`. Create `RunQueue(run_fn)` and `Scheduler(registry, queue.enqueue)`. Wire: `refresh_requested → _refresh`, `run_requested → run_now`, `stop_requested → queue.stop_current`, `save_requested → save`, `delete_requested → delete`, `approve_requested/preflight_requested → run_preflight`, `import/export_requested → mapping_io`, `job_activated/show_job → push detail`, `queue.subscribe → dashboard.set_queue + set_status`. `start()` starts the queue worker + `SchedulerThread`; `stop()` stops both and closes `State`/`Registry`. Set `window._stop_owned_threads = self.stop`.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/gui.py tests/ui/test_controller.py
git commit -m "feat(gui): controller wires registry + queue + scheduler"
```

---

## Phase E — CLI

### Task 17: Registry-backed CLI

**Files:**
- Modify: `src/store_sync/cli.py` (add subcommands; keep filelock helper)
- Test: `tests/test_cli.py` (rewrite)

**Interfaces:**
- Consumes: `Registry`, `State`, `run_sync`, `run_preflight`, `STGClient`.
- Produces: `build_parser() -> ArgumentParser` with subcommands `list`, `run <name>`, `run-all [--enabled-only]`, `stop <name>`. `main(argv) -> int`: `0` ok · `2` blocked/usage · `3` not-found/partial/error.

- [ ] **Step 1: Write the failing test (rewrite `tests/test_cli.py`)**

```python
"""Tests for store_sync.cli — registry-backed subcommands."""
from store_sync.cli import build_parser, main
from store_sync.core.registry import Registry
from store_sync.core.config import (BindingConfig, ColumnBinding, JobConfig,
    SourceConfig, StoreConfig, WriteConfig)

def a_job(name):
    return JobConfig(name, StoreConfig(17620, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig(dry_run=True))

def test_list_subcommand(tmp_path, capsys, monkeypatch):
    db = tmp_path / "s.db"
    r = Registry(db); r.add(a_job("a")); r.close()
    monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
    rc = main(["list"])
    assert rc == 0
    assert "a" in capsys.readouterr().out

def test_status_exit_codes():
    # blocked -> 2, not-found -> 3
    from store_sync.cli import _exit_for
    assert _exit_for("ok") == 0
    assert _exit_for("blocked") == 2
    assert _exit_for("partial") == 3
    assert _exit_for("error") == 3
```

- [ ] **Step 2: Run to verify failure**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement `cli.py`**

Add `_exit_for(status) -> int` (`ok→0`, `blocked→2`, else `3`). Resolve the DB from `STG_STORE_SYNC_DB` (default `Path.home()/".stg_store_sync"/"state.db"`). `list` prints `name · store · schedule · enabled`. `run <name>` runs the §3.3 readiness check then `run_sync`, prints status+counts, returns `_exit_for`. `run-all [--enabled-only]` loops the registry. `stop <name>` is a no-op notice (the queue is in-app; the CLI cannot reach a running GUI) → returns 0 with a message. Keep `_acquire_lock` for the filelock path.

- [ ] **Step 4: Run suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): registry-backed list/run/run-all/stop subcommands"
```

---

### Task 18: Whole-suite verification + docs

**Files:**
- Create: `docs/superpowers/plans/2026-10-08-gui-registry-redesign-progress.md`
- Modify: `CHANGELOG.md` if present

- [ ] **Step 1: Run the full suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS, no `QThread: Destroyed while running` warnings.

- [ ] **Step 2: Offscreen smoke test**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c "from store_sync.gui import main; main()"`
Expected: opens (do not call `exec` past a point) — replace with a construct-then-close check if `main()` blocks:

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/python -c "
from store_sync.ui.main_window import MainWindow
w = MainWindow(); w.show(); w.close(); print('ok')"
```

- [ ] **Step 3: Write the progress ledger**

Record each task's commit hash and the final `pytest` tail in the progress file.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/plans/
git commit -m "docs: record redesign progress + verification"
```
