# Concurrent runs — design

Date: 2026-10-17
Status: approved (design), spec for review
Supersedes: `2026-10-08-gui-registry-redesign-design.md` §5.1 (run queue) and
`AGENTS.md` invariant 10 ("one global run queue — exactly one sync at a time").
Amends invariant 11 only in wording (headless `run-all` stays serial); every
other invariant is unchanged.

## 1. Problem

Runs are globally serialized. `RunQueue` (`core/run_queue.py`) owns **one**
daemon worker that drains a FIFO, so a manual run of job `A` blocks a manual run
of job `B` until `A` finishes. With ~30 configured jobs and a pull that can take
minutes (full survey history + chunked export + per-row add throttle), the
operator waits behind unrelated work.

Observed: clicking *Run now* on job `B` while job `A` is running puts `B` in
`pending`; `B` starts only after `A`'s worker returns
(`run_queue.py:112-135`, `gui.py:362`).

The serialization is deliberate (invariant 10) but its justification is narrow:
the `add` path assigns the id as `max(id_column)+1` over the store snapshot read
at run start (`planner.py:66-75`, `planner.py:160-170`). Two runs writing the
**same** store concurrently would hand out the same id. Two runs writing
**different** stores share nothing.

## 2. Goals / Non-Goals

**Goals**

- Manual runs of **different stores** proceed concurrently; no run waits for an
  unrelated run.
- Per-job stop: stopping one run never stops another.
- Queue state and the dashboard show every active run, not one.
- Safety stays fail-closed: two runs targeting the **same store** never run
  concurrently, whatever the configuration says.
- Existing invariants hold: add-only automation, dry-run default, no delete,
  full-column writes, byte-for-byte diff proposals, blacklist behaviour.

**Non-Goals**

- A worker pool, a concurrency cap, or a configurable parallelism limit. The
  operator chose *unlimited*; every enqueued job starts immediately.
- Parallel `run-all` in the CLI. Headless stays serial (§5).
- Parallelizing the pairing/mapping network workers (`gui.py` `_NetworkWorker`
  slots). Unchanged.
- Changing the scheduler's due semantics.

## 3. Decisions (operator-confirmed)

| Question | Decision |
|---|---|
| Do concurrent jobs target the same store? | **Always different stores.** Each job owns one store. |
| Max concurrent runs? | **Unlimited** — no pool, no cap, nothing waits. |
| Stop semantics? | **Per job.** Detail-page Stop stops that job; the dashboard Stop-all keeps its meaning. |
| Scheduler tick while the job is already running? | **Skip** — no double-run, no queueing. |

## 4. Design

### 4.1 `core/run_queue.py` — fan-out instead of FIFO

`RunQueue` keeps its public API (`enqueue`, `stop_current`, `stop_all`,
`snapshot`, `subscribe`, `start`, `shutdown`) but changes its execution model
from one worker draining a queue to **one thread per accepted run**.

```python
@dataclass
class QueueState:
    active: list[str]            # was: Optional[str]
    pending: list[str]
```

State, all under the existing `threading.Lock`:

- `_active: dict[str, threading.Event]` — name → that run's stop event.
- `_threads: dict[str, threading.Thread]` — name → its worker (strong refs; a
  running thread is never dropped).
- `_store_keys: dict[str, str]` — reserved store key per in-flight name.
- `_pending: list[str]` — names accepted but not yet entered `run_fn`
  (microseconds; kept only so `snapshot()` can report them and dedupe is
  atomic).
- `_closed: bool` — set by `shutdown()`.

`enqueue(name, *, store_key=None)`:

1. Name already in `_active` or `_pending` → `False` (dedupe, unchanged).
2. `store_key` already reserved by another in-flight run → `False`, and the
   caller is told why (§4.4). Reservation is released in the worker's `finally`.
3. Otherwise append to `_pending`, spawn `threading.Thread(target=_worker,
   args=(name, store_key), daemon=True)`, `_notify()`, return `True`.

`_worker(name, store_key)`:

1. Move `name` from `_pending` to `_active[name]` with a fresh
   `threading.Event`; `_notify()`.
2. `self._run_fn(name, ev.is_set)`.
3. `finally`: pop `_active`, `_threads`, `_store_keys`; `_notify()`.

Exceptions inside `run_fn` are logged and contained exactly as today — one
failed run never affects another.

`stop_current(name=None)`:

- `name` given → set that run's event only.
- omitted → set every active event (back-compat for the dashboard/global Stop).

`stop_all()` → `stop_current()` + clear `_pending`.

`shutdown(timeout=5.0)` → set `_closed`, set every event, join every thread
with a shared bounded deadline (the deadline, not 5 s per thread), clear state,
`_notify()`. If `_closed`, `enqueue` returns `False` — no thread may outlive the
controller.

`start()` becomes a no-op marker (`_started`) kept for API compatibility; no
worker is spawned until `enqueue`. It still must not raise, and `shutdown()`
before `start()` must stay safe (existing test).

### 4.2 Same-store guard

`enqueue` takes an optional `store_key` (the job's `store.id` as `str`). The
queue refuses a second in-flight run with the same key. This is the safety net
behind the operator's "always different stores" assumption, and it is
store-level, not job-level: two job names pointing at one store is exactly the
case that would collide on `max(id)+1`.

`Controller.run_now(name)` resolves the job, passes `store_key=str(job.store.id)`
and, on refusal, sets a warning status (`"X is already running against store N"`)
instead of silently queueing. Scheduler enqueues (`Scheduler.tick` →
`queue.enqueue`) pass the key the same way so a scheduled run is refused while
another job holds that store.

`Scheduler.tick()` is unchanged in behaviour: it still calls `enqueue` and adds
`name` to its `_last` map before the call. A refused enqueue (name already
active, or store busy) therefore still marks the minute as fired — the job is
skipped this tick, which is the confirmed decision, and the next due minute can
try again.

### 4.3 GUI (`gui.py`, `ui/dashboard.py`, `ui/job_detail.py`)

- `_on_queue_changed`: header status lists active runs
  (`running a, b`); each finished run still produces its own toast. The
  finished-run detection compares the previous and current **sets** of active
  names (per-name), not one `_active_prev` string, so two completions in one
  callback each get their toast and the open job's History refreshes.
- `_refresh`: `running = len(snap.active)`.
- `dashboard.set_queue(active: list[str], pending: list[str])` renders
  `▶ a   ▶ b   • c`; `idle` when empty.
- `ui/main_window.py`: `job_detail.run_requested` already carries the current
  job name (`_on_detail_run`). `job_detail.stop_requested` is re-routed in
  `Controller._wire_signals` to `self._stop_detail_run()` →
  `queue.stop_current(self._detail_name)`. `w.stop_requested` (dashboard
  toolbar) keeps `queue.stop_current()` — all runs. Both are explicit; the
  detail Stop no longer stops unrelated jobs.
- The per-job progress buffers (`_progress_by_job`) already key on `job`, so
  concurrent logs cannot bleed into each other; no change.

### 4.4 SQLite and shared state

Every thread already gets its own `State`/`Registry` connection
(`gui.py` `_state`/`_registry`), and the DB runs in WAL. Two concurrent runs
therefore write different rows from different connections — correct, but
contention can surface as `database is locked` under burst. Mitigation:
`sqlite3.connect(..., timeout=30)` in `core/state.py:87` and
`core/registry.py:131`. No schema change, no new tables, no migration.

`STGClient`/`requests.Session` is built per run (`Controller._default_run_fn`),
so the sessions are already independent.

### 4.5 CLI (unchanged behaviour)

`run-all` and `run-due` iterate jobs serially with a per-job `FileLock`
(`cli.py:340-395`). That stays: headless remains add-only and serial, and the
per-job lock only prevents a headless run and a GUI run of the *same job* from
overlapping. The store-level guard is in-process only, which is sufficient: the
invariant needed is "one job per store", and the CLI is serial.

## 5. Invariants to update

- **Invariant 10** becomes: *Runs are concurrent across jobs, one run per job at
  a time, and never two runs against one store. Same-job dedupe, per-run stop.*
- **Invariant 11** wording: headless (`cli`, scheduler-driven `run-due`) is
  serial and add-only; the GUI may run several jobs at once, each add-only.
- All other invariants (1-9, 12-15) are untouched.

## 6. Testing

`tests/core/test_run_queue.py` — rewrite the serialization cases:

- `test_two_jobs_run_concurrently` — gate both runs; both must be in
  `snapshot().active` before the gate opens; assert overlap actually happened
  (not just fast completion).
- `test_same_job_deduped` — second `enqueue("a")` is `False`.
- `test_same_store_refused` — `enqueue("a", store_key="1")` then
  `enqueue("b", store_key="1")` is `False`; different keys are both accepted.
- `test_stop_current_only_stops_named` — `stop_current("a")` ends `a`, leaves
  `b` running.
- `test_stop_current_without_name_stops_all` — back-compat.
- `test_stop_all_drops_pending_and_stops_active` — unchanged meaning.
- `test_shutdown_joins_every_run` — bounded, no leaked thread.
- `test_enqueue_after_shutdown_returns_false`.
- Keep `test_subscribe_called_on_every_change`,
  `test_shutdown_is_bounded_when_idle`,
  `test_shutdown_before_start_does_not_queue_stale_sentinel`,
  `test_shutdown_bounded_when_run_ignores_stop`.

`tests/core/test_scheduler.py` — add: a job whose `enqueue` returns `False`
(name already active) is still marked fired for the minute and does not raise.

`tests/ui/` — `set_queue(["a", "b"], ["c"])` renders both active names; two
finishes in one `_on_queue_changed` callback produce two toasts.

Full suite must stay green with `-W error`.

## 7. Risks

| Risk | Mitigation |
|---|---|
| Two jobs unexpectedly share a store → duplicate ids | Store-key guard refuses the second run (fail closed, warning in status bar). |
| `enqueue` from two threads (GUI + scheduler) races on the guard | All state changes under the existing `_lock`; thread spawn happens after the reservation is recorded. |
| A run that ignores `should_stop` wedges shutdown | Shared bounded join deadline; threads are daemons (§4.1). |
| SQLite `database is locked` under burst | WAL + per-thread connections (already) + `timeout=30`. |
| Header status becomes noisy with many active runs | Status lists names; the per-job detail log remains the detailed view. |

## 8. Out of scope

Concurrency caps / queueing beyond "start immediately", cross-process
(store-level) locking for CLI+GUI against the same store, and parallel `run-all`.
