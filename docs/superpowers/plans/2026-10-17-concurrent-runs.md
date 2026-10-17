# Concurrent Runs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let runs of different jobs execute concurrently (one run per job, never two runs against one store), with per-job stop and multi-active queue display.

**Architecture:** `RunQueue` changes from one FIFO worker thread to one thread per accepted run, tracking `active` as a dict of name→stop-event plus a per-name store reservation. The controller, dashboard, and scheduler consume the new list-shaped `QueueState` and pass a store key on enqueue so a second run against the same store is refused.

**Tech Stack:** Python 3.12, `threading`, SQLite (WAL, per-thread connections), PySide6 (signals/marshalling), pytest.

**Spec:** `docs/superpowers/specs/2026-10-17-concurrent-runs-design.md`

## Global Constraints

- Add-only automation; dry-run default; never delete — unchanged.
- Writes still send the store's full column set; id = `max(id_column)+1` per run.
- One run per job at a time; **never two runs against one store** (fail closed).
- Stop is per job; the dashboard Stop-all still stops every run.
- Headless (`cli run-all` / `run-due`) stays serial and add-only.
- Full suite must pass both `./.venv/bin/python -m pytest -q` and `... -W error`.
- Never commit unless explicitly asked; each task's commit step is the only commit.

## Review Focus

- Two job names configured with the same `store.id` — the second `enqueue` must be refused, not silently queued.
- `shutdown()` while three runs are in flight — must join all, bounded, and drop no thread reference.
- A run that ignores `should_stop` — must not wedge shutdown of the other runs.
- Stopping job A from its detail page while B runs — B must keep running.
- `enqueue` racing from the GUI thread and the scheduler thread — the guard must be atomic.

---

### Task 1: `RunQueue` — one thread per run

**Files:**
- Modify: `src/store_sync/core/run_queue.py` (whole file)
- Test: `tests/core/test_run_queue.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `QueueState(active: list[str], pending: list[str])`;
  `RunQueue(run_fn, *, poll: float = 0.05)`;
  `enqueue(name: str, *, reason: str = "manual", store_key: str | None = None) -> bool`;
  `stop_current(name: str | None = None) -> None`; `stop_all() -> None`;
  `snapshot() -> QueueState`; `subscribe(cb) -> None`; `start() -> None`;
  `shutdown(*, timeout: float = 5.0) -> None`.

- [ ] **Step 1: Rewrite the tests for concurrent semantics**

Replace the body of `tests/core/test_run_queue.py` with these cases (keep the module docstring updated to "concurrent per-job runs, dedupe, per-job stop"):

```python
def test_two_jobs_run_concurrently():
    gates = {"a": threading.Event(), "b": threading.Event()}
    both = threading.Barrier(3, timeout=5)
    def run_fn(name, should_stop):
        both.wait()                      # both runs must be in flight at once
        gates[name].wait(5)
        return name
    q = RunQueue(run_fn)
    q.start()
    assert q.enqueue("a") is True
    assert q.enqueue("b") is True
    both.wait()
    assert sorted(q.snapshot().active) == ["a", "b"]
    gates["a"].set(); gates["b"].set()
    q.shutdown()
    assert q.snapshot().active == []

def test_same_job_deduped():
    gate = threading.Event()
    q = RunQueue(lambda name, stop: gate.wait(5))
    q.start()
    assert q.enqueue("a") is True
    assert q.enqueue("a") is False
    gate.set()
    q.shutdown()

def test_stop_current_only_stops_named():
    stopped = set(); release = threading.Event()
    def run_fn(name, should_stop):
        while not should_stop():
            time.sleep(0.01)
        stopped.add(name)
        release.wait(5)
        return name
    q = RunQueue(run_fn)
    q.start()
    q.enqueue("a"); q.enqueue("b")
    deadline = time.monotonic() + 2
    while sorted(q.snapshot().active) != ["a", "b"] and time.monotonic() < deadline:
        time.sleep(0.01)
    q.stop_current("a")
    deadline = time.monotonic() + 2
    while "a" not in stopped and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stopped == {"a"}
    assert "b" in q.snapshot().active     # b untouched
    release.set()
    q.stop_all()
    q.shutdown()

def test_stop_current_without_name_stops_all():
    stopped = set()
    def run_fn(name, should_stop):
        while not should_stop():
            time.sleep(0.01)
        stopped.add(name)
        return name
    q = RunQueue(run_fn)
    q.start()
    q.enqueue("a"); q.enqueue("b")
    q.stop_current()
    deadline = time.monotonic() + 2
    while len(stopped) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    assert stopped == {"a", "b"}
    q.shutdown()

def test_stop_all_drops_pending_and_stops_active():
    started = threading.Event(); finished = threading.Event()
    q = None
    def run_fn(name, should_stop):
        started.set()
        while not should_stop():
            time.sleep(0.01)
        finished.set()
        return name
    q = RunQueue(run_fn)
    q.start()
    q.enqueue("a")
    started.wait(2)
    q.stop_all()
    assert finished.wait(2)
    assert q.snapshot().pending == []
    q.shutdown()
    assert q.snapshot().active == []

def test_subscribe_called_on_every_change():
    seen = []
    gate = threading.Event()
    q = RunQueue(lambda name, stop: gate.wait(5))
    q.subscribe(seen.append)
    q.start()
    assert q.enqueue("a") is True
    deadline = time.monotonic() + 2
    while not any("a" in s.active for s in seen) and time.monotonic() < deadline:
        time.sleep(0.01)
    assert any("a" in s.active for s in seen)
    gate.set()
    q.shutdown()
    assert seen[-1].active == []

def test_shutdown_joins_every_run():
    release = threading.Event()
    q = RunQueue(lambda name, stop: release.wait(5))
    q.start()
    q.enqueue("a"); q.enqueue("b"); q.enqueue("c")
    deadline = time.monotonic() + 2
    while len(q.snapshot().active) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    release.set()
    q.shutdown()
    assert q.snapshot().active == []
    assert q._threads == {}

def test_shutdown_is_bounded_when_idle():
    q = RunQueue(lambda name, stop: name)
    q.start()
    before = time.monotonic()
    q.shutdown(timeout=2.0)
    assert time.monotonic() - before < 1.0

def test_shutdown_before_start_is_safe():
    gate = threading.Event()
    q = RunQueue(lambda name, stop: gate.wait(5))
    q.shutdown()
    q.start()
    assert q.enqueue("a") is False        # closed for good
    gate.set()

def test_shutdown_bounded_when_run_ignores_stop():
    started = threading.Event(); release = threading.Event()
    def run_fn(name, should_stop):
        started.set()
        release.wait(5)                   # ignores should_stop
        return name
    q = RunQueue(run_fn)
    q.start()
    q.enqueue("a")
    started.wait(2)
    before = time.monotonic()
    q.shutdown(timeout=0.2)
    assert time.monotonic() - before < 2.0
    release.set()

def test_enqueue_after_shutdown_returns_false():
    q = RunQueue(lambda name, stop: name)
    q.start()
    q.shutdown()
    assert q.enqueue("a") is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_run_queue.py -q`
Expected: FAIL — `QueueState.active` is `None`-typed and `stop_current` takes no arguments; concurrency assertions fail.

- [ ] **Step 3: Rewrite `src/store_sync/core/run_queue.py`**

Module docstring must state: one thread per accepted run, one run per job at a time, never two runs against one store, per-run cooperative stop.

```python
@dataclass
class QueueState:
    active: list = field(default_factory=list)
    pending: list = field(default_factory=list)
```

`RunQueue.__init__(self, run_fn, *, poll: float = 0.05)` keeps `_run_fn`, `_poll`, `_lock`, `_subscribers`; adds `_active: dict[str, threading.Event] = {}`, `_threads: dict[str, threading.Thread] = {}`, `_store_keys: dict[str, str] = {}`, `_pending: list[str] = []`, `_closed = False`, `_started = False`. No `queue.Queue` and no sentinel.

`start()`: set `_started = True`; idempotent; no thread spawned (workers are per-run).

`enqueue(name, *, reason="manual", store_key=None) -> bool`: under `_lock`, return False if `_closed`, `name in _active`, `name in _pending`, or `store_key is not None and store_key in _store_keys.values()`. Otherwise append to `_pending` (and record `_store_keys[name] = store_key` when given), create the thread `threading.Thread(target=self._worker, args=(name, store_key), name=f"run-{name}", daemon=True)`, store in `_threads[name]`, notify after releasing the lock, then `thread.start()`, return True.

`_worker(name, store_key)`: under `_lock` remove from `_pending`, install a fresh `threading.Event` in `_active[name]`; notify; then `self._run_fn(name, event.is_set)` inside try/except that logs `run_queue: run %r raised; worker continues`; `finally` pops `_active`, `_threads`, `_store_keys` for that name and notifies.

`stop_current(name=None)`: with `_lock`, collect the events to set — `[self._active[name]]` when `name` is given and present, else all. Set them **outside** the lock.

`stop_all()`: `stop_current()` then clear `_pending` under the lock; notify.

`snapshot()`: return `QueueState(active=list(self._active), pending=list(self._pending))` under the lock.

`subscribe(cb)`: unchanged.

`shutdown(timeout=5.0)`: with `_lock` set `_closed = True`, clear `_pending`, take all events and `list(self._threads.values())`; set every event; join each thread with a **shared deadline** (`deadline = time.monotonic() + timeout`, remaining time per join, `max(0.0, deadline - now)`); clear `_active`/`_threads`/`_store_keys`; notify. Import `time`.

`_notify()`: keep the snapshot-then-copy-subscribers pattern.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_run_queue.py -q`
Expected: PASS (11 tests).

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/run_queue.py tests/core/test_run_queue.py
git commit -m "feat(run-queue): run jobs concurrently, one thread per run"
```

---

### Task 2: Same-store guard

**Files:**
- Modify: `src/store_sync/core/run_queue.py` (`enqueue` store reservation — already scaffolded in Task 1; verify and complete)
- Test: `tests/core/test_run_queue.py`

**Interfaces:**
- Consumes: `RunQueue.enqueue(name, *, reason, store_key)` from Task 1.
- Produces: nothing new; the store reservation is released when the run's thread exits.

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_run_queue.py`:

```python
def test_same_store_refused():
    gate = threading.Event()
    q = RunQueue(lambda name, stop: gate.wait(5))
    q.start()
    assert q.enqueue("a", store_key="1") is True
    assert q.enqueue("b", store_key="1") is False   # same store: refused
    assert q.enqueue("b", store_key="2") is True    # different store: allowed
    gate.set()
    q.shutdown()

def test_store_key_released_after_run():
    q = RunQueue(lambda name, stop: None)
    q.start()
    assert q.enqueue("a", store_key="1") is True
    deadline = time.monotonic() + 2
    while q.snapshot().active and time.monotonic() < deadline:
        time.sleep(0.01)
    assert q.enqueue("b", store_key="1") is True    # reservation released
    q.shutdown()

def test_store_key_without_store_is_unconstrained():
    gate = threading.Event()
    q = RunQueue(lambda name, stop: gate.wait(5))
    q.start()
    assert q.enqueue("a") is True
    assert q.enqueue("b") is True
    gate.set()
    q.shutdown()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_run_queue.py -q`
Expected: FAIL on `test_same_store_refused` — both enqueues accepted.

- [ ] **Step 3: Implement the reservation**

In `enqueue`, add the `store_key` argument and this guard **inside** the existing `with self._lock:` block, before appending to `_pending`:

```python
if store_key is not None and store_key in self._store_keys.values():
    return False
```

Record `self._store_keys[name] = store_key` when `store_key is not None`. The worker's `finally` must `self._store_keys.pop(name, None)` (verify Task 1 did this; add if missing).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_run_queue.py -q`
Expected: PASS (14 tests).

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/run_queue.py tests/core/test_run_queue.py
git commit -m "feat(run-queue): refuse a second concurrent run against one store"
```

---

### Task 3: SQLite contention tolerance

**Files:**
- Modify: `src/store_sync/core/state.py:87`
- Modify: `src/store_sync/core/registry.py:131`
- Test: `tests/core/test_state.py`, `tests/core/test_registry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: nothing new; both connections open with `timeout=30.0`.

- [ ] **Step 1: Write the failing test**

Append to `tests/core/test_state.py`:

```python
def test_connection_busy_timeout_allows_concurrent_writers(tmp_path):
    """Concurrent runs hold write locks briefly; the wait must be 30 s, not 5."""
    from store_sync.core.state import State

    st = State(tmp_path / "s.db")
    try:
        busy_ms = st.conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert busy_ms >= 30_000
    finally:
        st.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/bin/python -m pytest tests/core/test_state.py::test_connection_busy_timeout_allows_concurrent_writers -q`
Expected: FAIL — `busy_timeout` is the sqlite3 default `5000`. (A wall-clock lock test would be flaky and would pass at the default, so the timeout value is what gets pinned.)

- [ ] **Step 3: Pass `timeout=30.0`**

`state.py:87` → `self.conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30.0)`. Same change at `registry.py:131`. Add the same `busy_timeout >= 30_000` assertion for `Registry` in `tests/core/test_registry.py`.

- [ ] **Step 4: Run the tests**

Run: `./.venv/bin/python -m pytest tests/core/test_state.py tests/core/test_registry.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/state.py src/store_sync/core/registry.py tests/core/test_state.py
git commit -m "fix(state): 30s SQLite lock timeout for concurrent runs"
```

---

### Task 4: Scheduler passes the store key

**Files:**
- Modify: `src/store_sync/core/scheduler.py:99-127`
- Test: `tests/core/test_scheduler.py`

**Interfaces:**
- Consumes: `RunQueue.enqueue(name, *, reason, store_key)` (Task 1/2).
- Produces: `Scheduler.__init__(registry, enqueue, *, now_fn=None)` where `enqueue` is called as `enqueue(name, store_key=str(entry.store_id))`; a `False` return means skipped and is not an error.

- [ ] **Step 1: Write the failing tests**

Append to `tests/core/test_scheduler.py`:

```python
class EStore:
    def __init__(self, name, enabled, schedule, store_id):
        self.name, self.enabled, self.schedule = name, enabled, schedule
        self.store_id = store_id

def test_tick_passes_store_key():
    calls = []
    s = Scheduler(
        FakeRegistry([EStore("a", True, Schedule("minute", 1), 17620)]),
        lambda name, **kw: calls.append((name, kw)) or True,
        now_fn=lambda: datetime(2026, 10, 8, 3, 10),
    )
    assert s.tick() == ["a"]
    assert calls == [("a", {"store_key": "17620"})]

def test_refused_enqueue_still_fires_once_per_minute():
    calls = []
    s = Scheduler(
        FakeRegistry([EStore("a", True, Schedule("minute", 1), 7)]),
        lambda name, **kw: calls.append(name) or False,   # refused: store busy
        now_fn=lambda: datetime(2026, 10, 8, 3, 10),
    )
    assert s.tick() == ["a"]        # reported fired
    assert s.tick() == []           # not retried the same minute
    assert calls == ["a"]           # refused enqueue is not an error
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_scheduler.py -q`
Expected: FAIL — `Scheduler` calls `enqueue(name)` positionally, so the fakes receive no `store_key`.

- [ ] **Step 3: Pass the key from the entry**

In `Scheduler.tick`, replace `self._enqueue(name)` with:

```python
self._enqueue(name, store_key=str(getattr(entry, "store_id", "") or ""))
```

Order stays: set `self._last[name] = minute_key` **before** the call, then `fired.append(name)`. Document in `tick`'s docstring that a refused enqueue (job already active, or its store busy) is reported as fired and skipped until the next due minute.

Also update the module docstring's "thin loop" paragraph to say the store key travels with each enqueue.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_scheduler.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/scheduler.py tests/core/test_scheduler.py
git commit -m "feat(scheduler): pass store key so a busy store is skipped"
```

---

### Task 5: Controller + dashboard show every active run

**Files:**
- Modify: `src/store_sync/gui.py` (`_active_prev` → set, `_on_queue_changed`, `_refresh`, `run_now`)
- Modify: `src/store_sync/ui/dashboard.py:189-195`
- Test: `tests/ui/test_controller.py:296-318`, `tests/ui/test_dashboard.py:23-29`

**Interfaces:**
- Consumes: `QueueState.active` as `list[str]` (Task 1); `RunQueue.enqueue(name, *, store_key)` (Task 2).
- Produces: `Dashboard.set_queue(active: list[str], pending: list[str])`; `Controller._active_prev: set[str]`; `Controller.run_now(name)` resolves the job and passes `store_key=str(job.store.id)`.

- [ ] **Step 1: Write the failing tests**

Replace `tests/ui/test_dashboard.py::test_queue_strip_and_stats` body with:

```python
def test_queue_strip_and_stats():
    d = Dashboard()
    d.set_queue(["a", "b"], ["c"])
    d.set_stats(total=3, enabled=2, running=2, failed=0)
    text = d.queue_label.text()
    assert "a" in text and "b" in text and "c" in text
```

Update `tests/ui/test_controller.py::test_finished_run_toast_flags_dry_run`: replace `c._active_prev = "a"` with `c._active_prev = {"a"}`, and `QueueState(active=None, pending=[])` with `QueueState(active=[], pending=[])`.

Add to `tests/ui/test_controller.py`:

```python
def test_two_finished_runs_produce_two_toasts(tmp_path):
    from store_sync.ui.toast import Toast
    from store_sync.core.run_queue import QueueState

    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule=Schedule(""), enabled=False)
    c.save("b", a_job(), schedule=Schedule(""), enabled=False)
    _seed_run(c, job="a")
    _seed_run(c, job="b")
    w.show()
    c._active_prev = {"a", "b"}
    c._on_queue_changed(QueueState(active=[], pending=[]))
    texts = [t.text() for t in w.findChildren(Toast) if t.isVisible()]
    assert any('"a"' in t for t in texts)
    assert any('"b"' in t for t in texts)
    c.stop(); w.close()

def test_run_now_passes_store_key(tmp_path):
    seen = {}
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    real = c.queue.enqueue
    c.queue.enqueue = lambda name, **kw: seen.update(name=name, **kw) or True
    c.save("a", a_job(), schedule=Schedule(""), enabled=False)
    c.run_now("a")
    assert seen["store_key"] == str(c.registry.get("a").store.id)
    c.queue.enqueue = real
    c.stop(); w.close()
```

`_seed_run` already exists in that file; if it takes no `job` kwarg, extend it with `job: str = "a"` and pass it through to `state.start_run`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/ui/test_dashboard.py tests/ui/test_controller.py -q`
Expected: FAIL — `set_queue` expects a `str | None`, `_active_prev` is a str, two toasts never appear.

- [ ] **Step 3: Implement**

`ui/dashboard.py`:

```python
def set_queue(self, active: list[str], pending: list[str]) -> None:
    parts = [f"▶ {name}" for name in active]
    parts.extend(f"• {name}" for name in pending)
    self.queue_label.setText("  ".join(parts) if parts else "idle")
```

`gui.py`:

- `self._active_prev: set[str] = set()`.
- `run_now(name)` resolves the job and enqueues with its store, warning on refusal:

```python
def run_now(self, name: str) -> None:
    try:
        job = self.registry.get(name)
        store_key = str(job.store.id)
    except KeyError:
        return
    if not self.queue.enqueue(name, store_key=store_key):
        self.window.set_status(
            f"{name} not started (already running, or store {store_key} busy)",
            _STATUS_WARN,
        )
```

- `_refresh`: `running = len(snap.active)`; `dash.set_queue(snap.active, snap.pending)`.
- `_on_queue_changed(state)`: build `now_active = set(state.active)`; `finished = self._active_prev - now_active`; for each name in `sorted(finished)` run the existing per-run toast + status block (reading `recent_runs(name)[0]`); then, if `self._detail_name` and it is in `finished`, `self._load_history(self._detail_name)`; set `self._active_prev = now_active`; call `self.window.set_status(...)` with `"running " + ", ".join(sorted(now_active))` when non-empty else `"ready", _STATUS_OK`; then `self._refresh()`.

- `_wire_signals`: `w.run_requested` stays `self.run_now`; `w.stop_requested` stays `self.queue.stop_current` (all runs).

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/ui/test_dashboard.py tests/ui/test_controller.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/gui.py src/store_sync/ui/dashboard.py tests/ui/test_controller.py tests/ui/test_dashboard.py
git commit -m "feat(gui): show every active run; enqueue with the job's store key"
```

---

### Task 6: Per-job stop from the job detail page

**Files:**
- Modify: `src/store_sync/ui/main_window.py` (new `stop_job_requested` signal, `_on_detail_stop`)
- Modify: `src/store_sync/gui.py` (`_wire_signals`)
- Test: `tests/ui/test_controller.py`, `tests/ui/test_main_window.py`

**Interfaces:**
- Consumes: `RunQueue.stop_current(name=None)` (Task 1); `MainWindow._current_job`.
- Produces: `MainWindow.stop_job_requested = Signal(str)` — emitted by the detail page's Stop with the open job's name; `window.stop_requested` (no payload) now means **all runs** and is emitted only by the dashboard toolbar.

- [ ] **Step 1: Write the failing tests**

Append to `tests/ui/test_controller.py`:

```python
def test_detail_stop_only_stops_that_job(tmp_path):
    seen = []
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.queue.stop_current = lambda name=None: seen.append(name)
    w.show_job("b")
    w.job_detail.stop_requested.emit()
    assert seen == ["b"]                 # scoped, not global
    c.stop(); w.close()
```

Update `tests/ui/test_main_window.py::test_proxy_signals_relay`: add `"stop_job_requested"` to `names`, and change the assertion block to:

```python
assert seen["stop_requested"] == [()]          # dashboard only
assert seen["stop_job_requested"] == [("j1",)] # job detail carries the job
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/ui/test_controller.py::test_detail_stop_only_stops_that_job tests/ui/test_main_window.py -q`
Expected: FAIL — no `stop_job_requested` signal; the detail Stop still relays to `stop_requested`.

- [ ] **Step 3: Implement**

`ui/main_window.py`:

- Declare `stop_job_requested = Signal(str)` next to `stop_requested`.
- In `_wire_signals`, replace `self.job_detail.stop_requested.connect(self.stop_requested.emit)` with `self.job_detail.stop_requested.connect(self._on_detail_stop)`.
- Add:

```python
def _on_detail_stop(self) -> None:
    """Detail-page Stop carries the open job so only that run stops."""
    if self._current_job is not None:
        self.stop_job_requested.emit(self._current_job)
```

`gui.py` `_wire_signals`:

```python
w.stop_requested.connect(self.queue.stop_current)          # dashboard: all runs
w.stop_job_requested.connect(lambda name: self.queue.stop_current(name))
```

Keep `w.run_requested.connect(self.run_now)`.

- [ ] **Step 4: Run tests**

Run: `./.venv/bin/python -m pytest tests/ui/test_controller.py tests/ui/test_main_window.py tests/ui/test_job_detail.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/gui.py src/store_sync/ui/main_window.py tests/ui/test_controller.py tests/ui/test_main_window.py
git commit -m "feat(gui): detail-page stop stops only that job"
```

---

### Task 7: Docs and invariant updates

**Files:**
- Modify: `AGENTS.md` (invariants 10 and 11)
- Modify: `CONTEXT.md` ("Run queue" entry)
- Modify: `README.md` (queue description, if it mentions serially)
- Modify: `tasks/todo.md` (new Phase 16 ledger entry)

**Interfaces:**
- Consumes: the behaviour shipped in Tasks 1-6.
- Produces: documentation only.

- [ ] **Step 1: Update `AGENTS.md` invariant 10**

Replace invariant 10 with:

> **10. Runs are concurrent across jobs; one run per job; never two runs against
> one store.** Each accepted run gets its own worker thread; `enqueue` is
> refused while the job is already active or while another in-flight run holds
> the same `store.id` (fail closed — the id is assigned `max(id_column)+1` from
> the store snapshot). Stop is per job; the dashboard Stop-all still stops
> every run. Headless (`cli`, `run-due`) remains serial.

- [ ] **Step 2: Update invariant 11**

Keep add-only wording; change the serial clause to: "Headless = serial and
add-only." (GUI runs may be concurrent, each still add-only.)

- [ ] **Step 3: Update `CONTEXT.md`**

> **Run queue**: The per-run scheduler of work: each accepted job runs on its
> own thread, one run per job at a time, never two runs against one store, with
> cooperative per-run stop.
> _Avoid_: worker pool, thread pool, daemon.

- [ ] **Step 4: Add the ledger entry**

Append to `tasks/todo.md`:

```markdown
## Phase 16 — Concurrent runs ✅ COMPLETE
> Spec: `docs/superpowers/specs/2026-10-17-concurrent-runs-design.md`
> Plan: `docs/superpowers/plans/2026-10-17-concurrent-runs.md`
> Runs of different jobs execute in parallel; one run per job, never two against
> one store, per-job stop, multi-active queue display.

- [ ] 16.1 `core/run_queue.py` — one thread per run, `QueueState.active: list[str]`
- [ ] 16.2 `core/run_queue.py` — same-store refusal
- [ ] 16.3 `core/state.py`/`registry.py` — 30 s SQLite lock timeout
- [ ] 16.4 `core/scheduler.py` — enqueue carries the store key
- [ ] 16.5 `gui.py`/`ui/dashboard.py` — multi-active status, per-run toasts, store-key enqueue
- [ ] 16.6 `gui.py`/`ui/main_window.py` — per-job stop
- [ ] 16.7 Docs — invariant 10/11, vocabulary, ledger
```

Tick the boxes as each task lands.

- [ ] **Step 5: Run the whole suite (both modes) and commit**

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/python -m pytest -q -W error
git add AGENTS.md CONTEXT.md tasks/todo.md README.md
git commit -m "docs: concurrent runs invariant, vocabulary, phase 16"
```

Expected: all tests pass in both runs.
