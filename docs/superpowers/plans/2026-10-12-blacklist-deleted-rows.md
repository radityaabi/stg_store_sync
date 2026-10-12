# Blacklist (Suppress Re-Adding Deleted Rows) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A store row the operator deletes by hand stays deleted — the next run does not re-add it, and the operator can see and correct the suppression list.

**Architecture:** Add a per-job `blacklist` table to the existing SQLite state DB. Before planning, `run_sync` reconciles it against the store snapshot: a tracked key missing from the store is auto-blacklisted, a blacklisted key back in the store is auto-cleared. `plan()` takes the blacklist and skips `add` for those keys, recording them in `Plan.suppressed`. The operator gets a GUI tab and CLI subcommands to add/remove entries by hand.

**Tech Stack:** Python 3.12, SQLite (`sqlite3`), PySide6, pytest.

**Spec:** `docs/superpowers/specs/2026-10-12-blacklist-deleted-rows-design.md`

## Global Constraints

- Suppression applies to **`add` only**; `update`/`same`/`conflict` classification is untouched.
- Blacklist is **per job** (keyed by job name), like `tracked` and `proposals`.
- **Only tracked keys** are auto-blacklisted (`tracked_keys - store_keys`). A key never written by the system is never auto-suppressed.
- Reconciliation runs on **dry-run and live runs alike** — it mutates only SQLite, never the store.
- A suppressed key is **not** a run error: status stays `ok`, `counts` is unchanged.
- `reason` is `"deleted"` (auto) or `"manual"` (operator).
- Test commands: `./.venv/bin/python -m pytest -q` and `./.venv/bin/python -m pytest -q -W error` (both must pass before and after each task).
- Do **not** commit unless the operator asks; the plan's commit steps are the standard TDD checkpoint — skip them if the session has been told not to commit.

## Review Focus

- **A store row the system never wrote is deleted** (legacy row, or one the operator added then deleted in Studio) → must **not** be auto-blacklisted, because there is no `tracked` record. Only the manual path covers it.
- **A truncated/failed store read** → must never look like a mass deletion. `get_store` raises before reconciliation, so an absent key can only mean a real deletion; reconciliation must run *after* a successful full snapshot.
- **A blacklisted key reappears in the store** (operator re-added it) → the entry must be auto-cleared and the key planned normally again, not left suppressed.
- **A dry run after a manual delete** → must already suppress the re-add and record the blacklist entry, so the dry-run plan is honest.
- **Passing no blacklist** (`blacklisted=None`) → must reproduce today's behaviour exactly (no suppression).

---

### Task 1: `State` blacklist table + methods

**Files:**
- Modify: `src/store_sync/core/state.py` (schema string `_SCHEMA`, new methods after the tracking section)
- Test: `tests/core/test_state.py` (new `TestBlacklist` class)

**Interfaces:**
- Consumes: existing `State.tracked_keys(job) -> set[str]`.
- Produces:
  - `State.blacklist_key(job: str, match_value, *, reason: str = "manual") -> None`
  - `State.unblacklist_key(job: str, match_value) -> None`
  - `State.blacklisted_keys(job: str) -> set[str]`
  - `State.list_blacklist(job: str) -> list[dict]` — each `{"match_value", "reason", "added_at"}`, ordered by `match_value`
  - `State.reconcile_blacklist(job: str, store_keys: set[str]) -> tuple[set[str], set[str]]` — returns `(added, removed)`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_state.py`:

```python
class TestBlacklist:
    def test_add_list_and_remove(self, st):
        st.blacklist_key("j", "104", reason="deleted")
        st.blacklist_key("j", "105")  # manual default
        rows = st.list_blacklist("j")
        assert [r["match_value"] for r in rows] == ["104", "105"]
        assert rows[0]["reason"] == "deleted"
        assert rows[1]["reason"] == "manual"
        assert st.blacklisted_keys("j") == {"104", "105"}
        st.unblacklist_key("j", "104")
        assert st.blacklisted_keys("j") == {"105"}

    def test_blacklist_is_per_job(self, st):
        st.blacklist_key("a", "1")
        assert st.blacklisted_keys("b") == set()

    def test_reconcile_blacklists_tracked_key_missing_from_store(self, st):
        st.track_row(job="j", match_value="104", row_hash="h")
        added, removed = st.reconcile_blacklist("j", {"101", "102"})
        assert added == {"104"}
        assert removed == set()
        assert st.blacklisted_keys("j") == {"104"}
        assert st.list_blacklist("j")[0]["reason"] == "deleted"

    def test_reconcile_clears_blacklisted_key_back_in_store(self, st):
        st.blacklist_key("j", "104", reason="deleted")
        added, removed = st.reconcile_blacklist("j", {"101", "104"})
        assert added == set()
        assert removed == {"104"}
        assert st.blacklisted_keys("j") == set()

    def test_reconcile_leaves_untracked_key_alone(self, st):
        # 103 was never tracked (never written by the system) → not blacklisted.
        added, removed = st.reconcile_blacklist("j", {"101", "102"})
        assert added == set()
        assert st.blacklisted_keys("j") == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_state.py::TestBlacklist -q`
Expected: FAIL with `AttributeError: 'State' object has no attribute 'blacklist_key'`

- [ ] **Step 3: Implement the table + methods in `src/store_sync/core/state.py`**

Append to `_SCHEMA` (after the `proposals` table):

```sql
CREATE TABLE IF NOT EXISTS blacklist (
    job         TEXT NOT NULL,
    match_value TEXT NOT NULL,
    reason      TEXT NOT NULL DEFAULT 'deleted',
    added_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (job, match_value)
);
```

Add a `# -- blacklist` section (mirror the `tracked` methods; upsert via `ON CONFLICT`):

```python
    def blacklist_key(self, job: str, match_value, *, reason: str = "manual") -> None:
        self.conn.execute(
            "INSERT INTO blacklist (job, match_value, reason, added_at) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(job, match_value) DO UPDATE SET "
            "reason = excluded.reason, added_at = excluded.added_at",
            (job, str(match_value), reason),
        )
        self.conn.commit()

    def unblacklist_key(self, job: str, match_value) -> None:
        self.conn.execute(
            "DELETE FROM blacklist WHERE job = ? AND match_value = ?",
            (job, str(match_value)),
        )
        self.conn.commit()

    def blacklisted_keys(self, job: str) -> set[str]:
        rows = self.conn.execute(
            "SELECT match_value FROM blacklist WHERE job = ?", (job,)
        ).fetchall()
        return {r["match_value"] for r in rows}

    def list_blacklist(self, job: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT match_value, reason, added_at FROM blacklist "
            "WHERE job = ? ORDER BY match_value",
            (job,),
        ).fetchall()
        return [dict(r) for r in rows]

    def reconcile_blacklist(
        self, job: str, store_keys: set[str]
    ) -> tuple[set[str], set[str]]:
        """Auto-blacklist tracked keys gone from the store; auto-clear reappeared.

        `added = tracked - store_keys` (a key the system wrote, now deleted by
        hand); `removed = blacklisted & store_keys` (a suppressed key re-added by
        hand). The two sets are disjoint, so write order is irrelevant.
        """
        store_keys = {str(k) for k in store_keys}
        tracked = self.tracked_keys(job)
        blacklisted = self.blacklisted_keys(job)
        added = tracked - store_keys
        removed = blacklisted & store_keys
        for key in added:
            self.blacklist_key(job, key, reason="deleted")
        for key in removed:
            self.unblacklist_key(job, key)
        return added, removed
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_state.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/core/test_state.py src/store_sync/core/state.py
git commit -m "feat(state): blacklist table + reconcile for deleted rows"
```

---

### Task 2: `Plan.suppressed` + `plan(blacklisted=...)`

**Files:**
- Modify: `src/store_sync/core/planner.py` (`Plan` dataclass; `plan` signature; the `add` loop)
- Test: `tests/core/test_planner.py` (new `TestBlacklist` class)

**Interfaces:**
- Consumes: nothing new (uses existing `plan` args).
- Produces: `Plan.suppressed: list[str]`; `plan(..., blacklisted: set[str] | None = None)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_planner.py` (the module already defines `store_rows()`, `source_records()`, `make_plan(**kw)`; source key `104` is the new one, store key `103` is absent):

```python
class TestBlacklist:
    def test_blacklisted_key_is_not_added(self):
        p = make_plan(blacklisted={"104"})
        assert [a.match_value for a in p.actions if a.kind == "add"] == []
        assert p.suppressed == ["104"]

    def test_suppressed_not_counted_as_action(self):
        p = make_plan(blacklisted={"104"})
        assert p.counts["add"] == 0

    def test_blacklisted_key_in_store_is_planned_normally(self):
        # 101 is in the store and in the blacklist → auto-clear is upstream, but
        # the planner itself must not suppress an in-store key.
        p = make_plan(blacklisted={"101"})
        assert [a.match_value for a in p.actions if a.kind == "same"] == ["101"]
        assert p.suppressed == []

    def test_none_blacklist_is_unchanged_behaviour(self):
        p = make_plan(blacklisted=None)
        assert [a.match_value for a in p.actions if a.kind == "add"] == ["104"]
        assert p.suppressed == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_planner.py::TestBlacklist -q`
Expected: FAIL with `TypeError: plan() got an unexpected keyword argument 'blacklisted'`

- [ ] **Step 3: Implement in `src/store_sync/core/planner.py`**

Add the field to `Plan`:

```python
@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    quarantined: list[dict] = field(default_factory=list)
    next_tag: int = 1
    suppressed: list[str] = field(default_factory=list)
```

Add the parameter and initialise it in `plan`:

```python
def plan(
    *,
    store_rows: list[dict],
    source_records: list[dict],
    match: ColumnBinding,
    columns: list[ColumnBinding],
    id_column: str,
    store_columns: list[str] | None = None,
    blacklisted: set[str] | None = None,
) -> Plan:
    ...
    blacklisted = {str(k) for k in (blacklisted or set())}
```

In the `add` loop, skip blacklisted keys (before the existing `src_dups` guard is fine; keep the `next_tag` counter untouched by a skipped key):

```python
    for key in sorted(set(src_keys) - set(store_keys), key=lambda k: src_by_key[k].get(match.source_var, k)):
        if key in src_dups:
            continue
        if key in blacklisted:
            p.suppressed.append(key)
            continue
        p.actions.append(
            Action("add", key, id_column, next_tag, _add_payload(src_by_key[key], key, next_tag))
        )
        next_tag += 1
```

Update the `plan` docstring to mention: "A key in `blacklisted` is skipped and recorded in `Plan.suppressed`; it produces no `add` action and is not counted."

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_planner.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/core/test_planner.py src/store_sync/core/planner.py
git commit -m "feat(planner): suppress add for blacklisted keys"
```

---

### Task 3: `run_sync` reconciliation + `SyncResult.suppressed`

**Files:**
- Modify: `src/store_sync/core/sync.py` (`SyncResult`; `run_sync` between the store load and `plan()`; the `plan`/`done` progress events)
- Test: `tests/core/test_sync.py` (new `TestBlacklist` class; reuse the module's `FakeClient`, `config`, `store_rows`, `subjects`, `st`)

**Interfaces:**
- Consumes: `State.reconcile_blacklist` (Task 1), `State.blacklisted_keys` (Task 1), `plan(..., blacklisted=...)` (Task 2), `Plan.suppressed` (Task 2).
- Produces: `SyncResult.suppressed: int`; `plan` and `done` progress events gain `suppressed=<int>`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_sync.py`:

```python
class TestBlacklist:
    def test_tracked_then_deleted_key_is_not_readded(self, st):
        st.track_row(job="link1", match_value="104", row_hash="h")
        # 104 absent from the store this run -> deleted by hand.
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        kinds = [x[0] for x in c.calls]
        assert "add" not in kinds                    # 104 not re-added
        assert res.status == "ok"                    # suppression is not an error
        assert res.suppressed == 1
        assert st.blacklisted_keys("link1") == {"104"}

    def test_suppression_recorded_on_dry_run(self, st):
        st.track_row(job="link1", match_value="104", row_hash="h")
        c = FakeClient(store=store_rows(), subjects=subjects())
        res = run_sync(config(dry_run=True), client=c, state=st)
        assert res.counts.get("add", 0) == 0
        assert res.suppressed == 1
        assert st.blacklisted_keys("link1") == {"104"}   # state written, store not

    def test_blacklisted_key_back_in_store_is_cleared_and_planned(self, st):
        st.track_row(job="link1", match_value="104", row_hash="h")
        st.blacklist_key("link1", "104", reason="deleted")
        rows = store_rows() + [{"No": 4, "SbjNum": "104", "I7_1_2": "Dave", "Q32": 44}]
        c = FakeClient(store=rows, subjects=subjects())
        res = run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        assert st.blacklisted_keys("link1") == set()     # auto-cleared
        assert res.suppressed == 0

    def test_untracked_absent_key_is_not_blacklisted(self, st):
        # 103 is in the store but not in the source; it was never tracked.
        c = FakeClient(store=store_rows(), subjects=subjects())
        run_sync(config(dry_run=False), client=c, state=st, throttle=0, backoff=0)
        assert st.blacklisted_keys("link1") == set()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_sync.py::TestBlacklist -q`
Expected: FAIL with `AttributeError: 'SyncResult' object has no attribute 'suppressed'`

- [ ] **Step 3: Implement in `src/store_sync/core/sync.py`**

Add the field to `SyncResult`:

```python
@dataclass
class SyncResult:
    plan: Plan
    dry_run: bool
    status: str  # "ok" | "blocked" | "partial" | "error"
    counts: dict = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    run_id: int | None = None
    proposals: int = 0  # update proposals recorded for operator review
    suppressed: int = 0  # blacklisted keys skipped (not re-added)
```

In `run_sync`, after `store_rows = client.get_store(config.store.id)` / `_emit("store", ...)`
and before the `plan(...)` call, insert:

```python
        # Reconcile the blacklist against the store snapshot: a tracked key gone
        # from the store was deleted by hand (blacklist it); a blacklisted key
        # back in the store was re-added by hand (clear it). Runs on dry-run too
        # — it mutates only SQLite, never the store.
        store_keys = {
            str(r.get(match.store_col))
            for r in store_rows
            if match.store_col in r
        }
        state.reconcile_blacklist(config.job, store_keys)
        blacklisted = state.blacklisted_keys(config.job)
```

Pass it to `plan(...)`:

```python
        p = plan(
            store_rows=store_rows,
            source_records=pulled.records,
            match=match,
            columns=config.binding.columns,
            id_column=config.store.id_column,
            store_columns=store_columns,
            blacklisted=blacklisted,
        )
```

Carry the count into the progress events and the result:

```python
        _emit("plan", counts=dict(p.counts), suppressed=len(p.suppressed))
        ...
        _emit("done", status=status, counts=dict(wres.counts), errors=list(errors),
              dry_run=dry_run, suppressed=len(p.suppressed))
        return SyncResult(
            plan=p, dry_run=dry_run, status=status, counts=wres.counts,
            errors=errors, run_id=run_id, proposals=proposals,
            suppressed=len(p.suppressed),
        )
```

Also add `suppressed=len(p.suppressed)` to **every** `SyncResult(...)` return that occurs *after* the reconciliation point: the readiness-gate `blocked` return, the store-conflict `blocked` return, the mid-apply `cancelled` return, and the final return. The pairing-gate return and the pull-`Cancelled` return happen before reconciliation, so their `suppressed` stays `0`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_sync.py -q`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS (no regressions in the existing 336 tests)

- [ ] **Step 6: Commit**

```bash
git add tests/core/test_sync.py src/store_sync/core/sync.py
git commit -m "feat(sync): reconcile blacklist and suppress re-adds"
```

---

### Task 4: GUI "Blacklist" tab

**Files:**
- Modify: `src/store_sync/ui/job_detail.py` (new signals; a `_blacklist_tab`; add the tab in `_build_ui`; `set_blacklist` API; load calls)
- Modify: `src/store_sync/ui/main_window.py` (proxy signals `blacklist_add_requested`, `blacklist_remove_requested`)
- Modify: `src/store_sync/gui.py` (`_load_blacklist`; wire the signals; call `_load_blacklist` in `_open_job`/`_new`/`_load_history`)
- Test: `tests/ui/test_job_detail.py` (new tests); `tests/ui/test_controller.py` (new test)

**Interfaces:**
- Consumes: `State.list_blacklist` (Task 1), `State.blacklist_key`/`unblacklist_key` (Task 1).
- Produces: `JobDetail.blacklist_add_requested(str)`, `JobDetail.blacklist_remove_requested(list)`, `JobDetail.set_blacklist(rows: list[dict])`; `MainWindow.blacklist_add_requested`, `MainWindow.blacklist_remove_requested`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/ui/test_job_detail.py`:

```python
def test_blacklist_tab_renders_rows():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_blacklist([
        {"match_value": "104", "reason": "deleted", "added_at": "2026-10-12 10:00:00"},
    ])
    assert d.blacklist_model.rowCount() == 1
    assert d.blacklist_model.data(d.blacklist_model.index(0, 0)) == "104"


def test_blacklist_remove_emits_selected_key():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_blacklist([{"match_value": "104", "reason": "deleted", "added_at": ""}])
    seen = []
    d.blacklist_remove_requested.connect(lambda keys: seen.append(keys))
    d.blacklist_view.selectRow(0)
    d._on_blacklist_remove()
    assert seen == [["104"]]


def test_blacklist_add_emits_typed_key():
    d = JobDetail()
    d.load(E(), a_job())
    seen = []
    d.blacklist_add_requested.connect(lambda key: seen.append(key))
    d.blacklist_add_edit.setText("  999  ")
    d._on_blacklist_add()
    assert seen == ["999"]
```

Add to `tests/ui/test_controller.py`:

```python
def test_blacklist_manual_add_and_remove_persist(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule=Schedule(""), enabled=False)
    c._detail_name = "a"
    w.blacklist_add_requested.emit("104")
    assert c.state.blacklisted_keys("a") == {"104"}
    w.blacklist_remove_requested.emit(["104"])
    assert c.state.blacklisted_keys("a") == set()
    c.stop()
    w.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/ui/test_job_detail.py -q -k blacklist`
Expected: FAIL with `AttributeError: 'JobDetail' object has no attribute 'blacklist_model'`

- [ ] **Step 3: Implement the `JobDetail` tab in `src/store_sync/ui/job_detail.py`**

Add signals next to the existing ones:

```python
    blacklist_add_requested = Signal(str)
    blacklist_remove_requested = Signal(list)
```

Register the tab in `_build_ui`, after the Conflicts tab and before Proposed updates:

```python
        self.tabs.addTab(self._blacklist_tab(), "Blacklist")
```

Add the builder (mirrors `_conflicts_tab` + a small add row):

```python
    def _blacklist_tab(self) -> QWidget:
        """Suppressed keys — rows deleted by hand that must not be re-added."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        self.blacklist_model = _DictTableModel(["match", "reason", "added"], self)
        self.blacklist_view = _dict_view(self.blacklist_model)
        lay.addWidget(self.blacklist_view, stretch=1)

        bar = QHBoxLayout()
        self.blacklist_add_edit = QLineEdit()
        self.blacklist_add_edit.setPlaceholderText("match key")
        self.blacklist_add_button = QPushButton("➕ Add key")
        self.blacklist_add_button.setObjectName("btn_primary")
        self.blacklist_add_button.clicked.connect(self._on_blacklist_add)
        self.blacklist_remove_button = QPushButton("🗑 Remove selected")
        self.blacklist_remove_button.setObjectName("btn_danger")
        self.blacklist_remove_button.clicked.connect(self._on_blacklist_remove)
        bar.addWidget(self.blacklist_add_edit)
        bar.addWidget(self.blacklist_add_button)
        bar.addStretch(1)
        bar.addWidget(self.blacklist_remove_button)
        lay.addLayout(bar)
        return w

    def _on_blacklist_add(self) -> None:
        key = self.blacklist_add_edit.text().strip()
        if key:
            self.blacklist_add_requested.emit(key)

    def _on_blacklist_remove(self) -> None:
        rows = self.blacklist_view.selectionModel().selectedRows()
        keys = [self.blacklist_model.data(r) for r in rows]
        if keys:
            self.blacklist_remove_requested.emit(keys)
```

Add the setter near `set_conflicts`:

```python
    def set_blacklist(self, rows: list[dict]) -> None:
        """Fill the Blacklist tab (match · reason · added)."""
        self.blacklist_model.set_rows(rows)
```

In `load`, reset the tab alongside `set_proposals([])`:

```python
        self.set_blacklist([])
```

- [ ] **Step 4: Implement the `MainWindow` proxies in `src/store_sync/ui/main_window.py`**

Add the signals:

```python
    blacklist_add_requested = Signal(str)
    blacklist_remove_requested = Signal(list)
```

In `_wire_signals`, after the proposals wiring:

```python
        self.job_detail.blacklist_add_requested.connect(
            self.blacklist_add_requested.emit
        )
        self.job_detail.blacklist_remove_requested.connect(
            self.blacklist_remove_requested.emit
        )
```

- [ ] **Step 5: Implement the controller wiring in `src/store_sync/gui.py`**

In `_wire_signals`, after `dismiss_proposals_requested`:

```python
        w.blacklist_add_requested.connect(self._blacklist_add)
        w.blacklist_remove_requested.connect(self._blacklist_remove)
```

Add the loader (near `_load_proposals`) and the handlers:

```python
    def _load_blacklist(self, name: str) -> None:
        rows = self.state.list_blacklist(name)
        self.window.job_detail.set_blacklist(
            [
                {
                    "match": r.get("match_value", ""),
                    "reason": r.get("reason", ""),
                    "added": r.get("added_at", ""),
                }
                for r in rows
            ]
        )

    def _blacklist_add(self, key: str) -> None:
        name = self._detail_name
        if not name or not key:
            return
        self.state.blacklist_key(name, key, reason="manual")
        self._load_blacklist(name)
        self.window.set_status(f"blacklisted {key}", _STATUS_OK)

    def _blacklist_remove(self, keys: list) -> None:
        name = self._detail_name
        if not name or not keys:
            return
        for key in keys:
            self.state.unblacklist_key(name, key)
        self._load_blacklist(name)
        self.window.set_status(f"unblacklisted {len(keys)} key(s)", _STATUS_OK)
```

In `_load_history`, add `self._load_blacklist(name)` in the non-empty branch (next to `self._load_proposals(name)`), and `detail.set_blacklist([])` in the empty branch.

- [ ] **Step 6: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/ui/test_job_detail.py tests/ui/test_controller.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/store_sync/ui/job_detail.py src/store_sync/ui/main_window.py src/store_sync/gui.py tests/ui/test_job_detail.py tests/ui/test_controller.py
git commit -m "feat(ui): blacklist tab with manual add/remove"
```

---

### Task 5: CLI subcommands + `run --json` suppressed count

**Files:**
- Modify: `src/store_sync/cli.py` (`build_parser`, new `_cmd_blacklist*`, `_emit`, `main` dispatch)
- Test: `tests/test_cli.py` (new tests)

**Interfaces:**
- Consumes: `State.list_blacklist`/`blacklist_key`/`unblacklist_key` (Task 1); `SyncResult.suppressed` (Task 3).
- Produces: subcommands `blacklist`, `blacklist-add`, `blacklist-remove`; `run --json` gains `"suppressed"`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py` (reuse the module's `add_job` helper; the CLI resolves its DB from `STG_STORE_SYNC_DB`, so set that env var and call `main`):

```python
def test_blacklist_add_list_remove(tmp_path, capsys, monkeypatch):
    db = tmp_path / "s.db"
    monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
    add_job(db, "a")

    assert main(["blacklist-add", "a", "104"]) == 0
    assert main(["blacklist", "a"]) == 0
    out = capsys.readouterr().out
    assert "104" in out and "manual" in out

    assert main(["blacklist-remove", "a", "104"]) == 0
    assert main(["blacklist", "a"]) == 0
    assert "104" not in capsys.readouterr().out


def test_blacklist_unknown_job_exits_3(tmp_path, monkeypatch):
    db = tmp_path / "s.db"
    monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
    assert main(["blacklist", "nope"]) == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -q -k blacklist`
Expected: FAIL — argparse rejects the unknown subcommand (`blacklist-add`), exiting `1`

- [ ] **Step 3: Implement in `src/store_sync/cli.py`**

Add subparsers in `build_parser`:

```python
    bl = sub.add_parser("blacklist", help="list a job's suppressed keys")
    bl.add_argument("name", help="registered job name")

    bla = sub.add_parser("blacklist-add", help="suppress a key for a job")
    bla.add_argument("name", help="registered job name")
    bla.add_argument("key", help="match-key value to suppress")

    blr = sub.add_parser("blacklist-remove", help="unsuppress a key for a job")
    blr.add_argument("name", help="registered job name")
    blr.add_argument("key", help="match-key value to unsuppress")
```

Add the handlers (mirror `_cmd_list`'s registry + `names()` guard):

```python
def _cmd_blacklist(db: Path, args) -> int:
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
    finally:
        reg.close()
    state = State(db)
    try:
        for row in state.list_blacklist(args.name):
            print(f"{row['match_value']} · {row['reason']}")
    finally:
        state.close()
    return 0


def _cmd_blacklist_add(db: Path, args) -> int:
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
    finally:
        reg.close()
    state = State(db)
    try:
        state.blacklist_key(args.name, args.key, reason="manual")
    finally:
        state.close()
    print(f"{args.name}: blacklisted {args.key}")
    return 0


def _cmd_blacklist_remove(db: Path, args) -> int:
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
    finally:
        reg.close()
    state = State(db)
    try:
        state.unblacklist_key(args.name, args.key)
    finally:
        state.close()
    print(f"{args.name}: unblacklisted {args.key}")
    return 0
```

In `_emit`, add the suppressed count to the JSON body:

```python
                "proposals": getattr(res, "proposals", 0),
                "suppressed": getattr(res, "suppressed", 0),
```

And in the text line, after the proposals clause in `_status_line`:

```python
    if getattr(res, "suppressed", 0):
        base += f" · {res.suppressed} suppressed (blacklisted)"
```

In `main`, add the dispatch:

```python
    if args.command == "blacklist":
        return _cmd_blacklist(db, args)
    if args.command == "blacklist-add":
        return _cmd_blacklist_add(db, args)
    if args.command == "blacklist-remove":
        return _cmd_blacklist_remove(db, args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py src/store_sync/cli.py
git commit -m "feat(cli): blacklist subcommands + suppressed count"
```

---

### Task 6: Docs — vocabulary, invariants, spec note, ledger

**Files:**
- Modify: `CONTEXT.md` (add a **Blacklist** entry under "Planning & writing")
- Modify: `AGENTS.md` (add invariant 13; note the blacklist in "Hard invariants")
- Modify: `docs/superpowers/specs/2026-10-08-stg-store-sync-design.md` (§6 write model: a "suppression list" note pointing at the new spec)
- Modify: `tasks/todo.md` (new Phase 10 ledger section)
- Test: none (documentation) — verified by reading the diff

**Interfaces:** none (docs only).

- [ ] **Step 1: Add the vocabulary entry to `CONTEXT.md`**

Under "Planning & writing", after **Quarantine**:

```markdown
**Blacklist** (suppression list):
The per-job set of match keys that must not be re-added. A key enters it
automatically when a previously-written key disappears from the store (deleted
by hand), and is cleared automatically when the key reappears in the store. The
operator can also add or remove a key by hand. Suppresses `add` only.
_Avoid_: ignore list, skip list, delete policy.
```

- [ ] **Step 2: Add the invariant to `AGENTS.md`**

After invariant 12:

```markdown
13. **Deleted rows are blacklisted, not re-added.** A match key this system has
    written (`tracked`) that is absent from the store was deleted by hand; the
    next run suppresses its `add` and records it in the per-job `blacklist`
    (state DB). A blacklisted key back in the store is auto-cleared. Suppression
    applies to `add` only, is never an error, and is recorded on dry-run too.
```

- [ ] **Step 3: Add the pointer note to the base spec §6**

Immediately after the "**Delete** — **removed**." bullet, add:

```markdown
> **Suppression list (2026-10-12):** a store row the operator deletes by hand is
> not re-added — see
> `docs/superpowers/specs/2026-10-12-blacklist-deleted-rows-design.md`. The
> per-job blacklist suppresses the `add` of a previously-tracked key that has
> disappeared from the store.
```

- [ ] **Step 4: Add the ledger entry to `tasks/todo.md`**

After the Phase 9 section:

```markdown
## Phase 10 — Blacklist (deleted rows stay deleted) ✅ COMPLETE
> Spec: `docs/superpowers/specs/2026-10-12-blacklist-deleted-rows-design.md`
> Plan: `docs/superpowers/plans/2026-10-12-blacklist-deleted-rows.md`
> A hand-deleted store row is suppressed from re-add; auto-detected from
> `tracked`, per job, correctable in the GUI tab and the CLI.

- [x] 10.1 `core/state.py` — `blacklist` table + `reconcile_blacklist`
- [x] 10.2 `core/planner.py` — `Plan.suppressed` + `blacklisted` param
- [x] 10.3 `core/sync.py` — reconcile before plan; `SyncResult.suppressed`
- [x] 10.4 `ui/job_detail.py` + `ui/main_window.py` + `gui.py` — Blacklist tab
- [x] 10.5 `cli.py` — `blacklist` / `blacklist-add` / `blacklist-remove`
- [x] 10.6 Docs — vocabulary, invariant 13, base-spec note
```

- [ ] **Step 5: Verify the suite is still green**

Run: `./.venv/bin/python -m pytest -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add CONTEXT.md AGENTS.md docs/superpowers/specs/2026-10-08-stg-store-sync-design.md tasks/todo.md
git commit -m "docs: blacklist vocabulary, invariant, ledger"
```
