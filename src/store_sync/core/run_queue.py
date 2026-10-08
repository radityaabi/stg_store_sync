"""RunQueue — one global worker thread driving sync runs one at a time.

A single daemon worker consumes names from a FIFO queue. At most one run is
active at any moment; queued names are de-duplicated against the active run and
the pending set. Cooperative cancellation flows through a per-run
`threading.Event` that the `should_stop` predicate passed to `run_fn` reads; the
event is cleared before each run so a later run starts un-cancelled.
"""
from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_SENTINEL = object()


@dataclass
class QueueState:
    active: Optional[str]
    pending: list = field(default_factory=list)


class RunQueue:
    """Single-worker FIFO run queue with dedupe and cooperative stop."""

    def __init__(self, run_fn: Callable[[str, Callable[[], bool]], object], *, poll: float = 0.05):
        self._run_fn = run_fn
        self._poll = poll
        self._lock = threading.Lock()
        self._queue: "queue.Queue" = queue.Queue()
        self._active: Optional[str] = None
        self._pending: list[str] = []
        self._stop_event = threading.Event()
        self._subscribers: list[Callable[[QueueState], None]] = []
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Spawn the worker thread (idempotent)."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._worker, name="run-queue", daemon=True)
        self._thread.start()

    def shutdown(self) -> None:
        """Signal the active run, wake the worker, and join it."""
        self._stop_event.set()
        self._queue.put(_SENTINEL)
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    # -- public API --------------------------------------------------------

    def enqueue(self, name: str, *, reason: str = "manual") -> bool:
        """Queue `name`; return False if already active or pending."""
        with self._lock:
            if name == self._active or name in self._pending:
                return False
            self._pending.append(name)
        self._queue.put(name)
        self._notify()
        return True

    def stop_current(self) -> None:
        """Ask the active run to stop by tripping its `should_stop` event."""
        self._stop_event.set()

    def stop_all(self) -> None:
        """Ask the active run to stop and drop everything still pending."""
        with self._lock:
            self._stop_event.set()
            self._pending.clear()
        self._notify()

    def snapshot(self) -> QueueState:
        with self._lock:
            return QueueState(active=self._active, pending=list(self._pending))

    def subscribe(self, cb: Callable[[QueueState], None]) -> None:
        """Register `cb(QueueState)` called on every change."""
        with self._lock:
            self._subscribers.append(cb)

    # -- internals ---------------------------------------------------------

    def _notify(self) -> None:
        snap = self.snapshot()
        with self._lock:
            subs = list(self._subscribers)
        for cb in subs:
            cb(snap)

    def _worker(self) -> None:
        while True:
            item = self._queue.get()
            if item is _SENTINEL:
                # Drain any sentinel copies and exit.
                return
            with self._lock:
                if item not in self._pending:
                    # Dropped by stop_all (or never registered) — skip.
                    continue
                self._pending.remove(item)
                self._active = item
            self._stop_event.clear()
            self._notify()
            try:
                self._run_fn(item, self._stop_event.is_set)
            except Exception:
                # A failed run must not kill the worker; the run's own layer
                # records status. Keep draining the queue.
                logger.exception("run_queue: run %r raised; worker continues", item)
            finally:
                with self._lock:
                    self._active = None
                self._notify()
