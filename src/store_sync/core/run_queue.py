"""RunQueue — one worker thread per accepted run, concurrent across jobs.

Runs are **concurrent across jobs**: each accepted name gets its own daemon
thread, so a manual run of job A never waits for job B. Two things are still
exclusive:

* one run per job at a time — a name already active (or not yet entered the
  worker) is not enqueued twice; and
* **never two runs against one store** — `enqueue` takes an optional
  `store_key` (the job's `store.id`); a key already held by another in-flight
  run refuses the enqueue, because the `add` path assigns the id as
  `max(id_column)+1` from the store snapshot read at run start.

Cancellation is cooperative: every run owns a `threading.Event` that the
`should_stop` predicate passed to `run_fn` reads. `stop_current(name)` trips
just that run's event; `stop_current()` (no name) trips them all; `stop_all()`
also drops anything still pending. `shutdown()` closes the queue for good and
joins every worker under one bounded deadline.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class QueueState:
    active: list[str] = field(default_factory=list)
    pending: list[str] = field(default_factory=list)


class RunQueue:
    """Per-run worker threads: concurrent across jobs, serial per job and store."""

    def __init__(self, run_fn: Callable[[str, Callable[[], bool]], object], *, poll: float = 0.05):
        self._run_fn = run_fn
        self._poll = poll
        self._lock = threading.Lock()
        self._active: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._store_keys: dict[str, str] = {}
        self._pending: list[str] = []
        self._orphans: list[threading.Thread] = []
        self._subscribers: list[Callable[[QueueState], None]] = []
        self._started = False
        self._closed = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Mark the queue usable (idempotent); workers spawn on `enqueue`."""
        with self._lock:
            self._started = True

    def shutdown(self, *, timeout: float = 5.0) -> None:
        """Close the queue, signal every run, and join all under `timeout`.

        The join budget is shared: `timeout` bounds the whole shutdown, not each
        thread. A run that ignores `should_stop` cannot wedge shutdown forever —
        the workers are daemons, so we return anyway once the budget is spent.
        After this call `enqueue` returns False for good.

        A thread recorded by `enqueue` but not yet started (the window between
        the lock release and `thread.start()`) is skipped — joining it would
        raise `RuntimeError: cannot join thread before it is started`. Its worker
        sees `_closed` on entry and exits without running.
        """
        with self._lock:
            self._closed = True
            self._pending.clear()
            events = list(self._active.values())
            threads = [t for t in self._threads.values() if t.ident is not None]
        for ev in events:
            ev.set()
        deadline = time.monotonic() + timeout
        survivors = []
        for thread in threads:
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(timeout=remaining)
            if thread.is_alive():
                survivors.append(thread)
        with self._lock:
            if survivors:
                # Keep strong refs to runs that outlived the budget; dropping
                # them is how a still-running QThread-style thread gets GC'd.
                logger.warning(
                    "run_queue: %d run(s) still active after shutdown: %s",
                    len(survivors),
                    [t.name for t in survivors],
                )
                self._orphans.extend(survivors)
            self._threads.clear()
            self._active.clear()
            self._store_keys.clear()
        self._notify()

    # -- public API --------------------------------------------------------

    def enqueue(
        self, name: str, *, reason: str = "manual", store_key: Optional[str] = None
    ) -> bool:
        """Start `name`; return False if deduped, store-busy, or closed."""
        with self._lock:
            if self._closed:
                return False
            if name in self._active or name in self._pending:
                return False
            if store_key is not None and store_key in self._store_keys.values():
                return False
            self._pending.append(name)
            if store_key is not None:
                self._store_keys[name] = store_key
            thread = threading.Thread(
                target=self._worker, args=(name, store_key), name=f"run-{name}", daemon=True
            )
            self._threads[name] = thread
        self._notify()
        try:
            thread.start()
        except BaseException:
            # The OS refused a new thread (or the thread object failed). Undo the
            # reservation under the lock, or this job and its store stay wedged
            # for the life of the process.
            with self._lock:
                if name in self._pending:
                    self._pending.remove(name)
                self._threads.pop(name, None)
                self._store_keys.pop(name, None)
            self._notify()
            logger.exception("run_queue: could not start run %r", name)
            return False
        return True

    def stop_current(self, name: Optional[str] = None) -> None:
        """Trip one run's stop event, or every active run when `name` is None."""
        with self._lock:
            if name is None:
                events = list(self._active.values())
            else:
                ev = self._active.get(name)
                events = [ev] if ev is not None else []
        for ev in events:
            ev.set()

    def stop_all(self) -> None:
        """Ask every active run to stop and drop everything still pending."""
        self.stop_current()
        with self._lock:
            self._pending.clear()
        self._notify()

    def snapshot(self) -> QueueState:
        with self._lock:
            return QueueState(active=list(self._active), pending=list(self._pending))

    def subscribe(self, cb: Callable[[QueueState], None]) -> None:
        """Register `cb(QueueState)` called on every change."""
        with self._lock:
            self._subscribers.append(cb)

    # -- internals ---------------------------------------------------------

    def _notify(self) -> None:
        """Fan one snapshot out to every subscriber; a bad one never escapes.

        A subscriber raising must not unwind a worker (that would strand the
        job in `_active` and its store reserved forever), so each call is
        contained and logged.
        """
        snap = self.snapshot()
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            try:
                cb(snap)
            except Exception:  # noqa: BLE001 - a subscriber must never break a run
                logger.exception("run_queue: subscriber raised; continuing")

    def _worker(self, name: str, store_key: Optional[str]) -> None:
        event = threading.Event()
        with self._lock:
            if self._closed or name not in self._pending:
                # Closed before we started, or `stop_all` dropped us while we sat
                # in the enqueue→worker window: never run. (The old single-worker
                # queue had the same contract: absent from `_pending` = cancelled.)
                if name in self._pending:
                    self._pending.remove(name)
                self._threads.pop(name, None)
                self._store_keys.pop(name, None)
                cancelled = True
            else:
                cancelled = False
                self._pending.remove(name)
                self._active[name] = event
        if cancelled:
            self._notify()
            return
        try:
            self._notify()
            try:
                self._run_fn(name, event.is_set)
            except Exception:
                # A failed run must not affect any other run; the run's own
                # layer records status.
                logger.exception("run_queue: run %r raised; other runs continue", name)
        finally:
            with self._lock:
                self._active.pop(name, None)
                self._threads.pop(name, None)
                self._store_keys.pop(name, None)
            self._notify()
