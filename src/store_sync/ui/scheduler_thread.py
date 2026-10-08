"""SchedulerThread — drive :class:`~store_sync.core.scheduler.Scheduler` on a QThread.

The scheduler core owns no thread; this tiny driver ticks it on an interval and
re-emits each due job name as ``due(name)`` for the controller to enqueue. Between
ticks it sleeps in small slices so :meth:`stop` is responsive and the thread ends
promptly (AGENTS.md §3.4).
"""
from __future__ import annotations

import threading
import time

from PySide6.QtCore import QThread, Signal

# Sleep granularity: long enough to avoid a busy loop, short enough that
# `stop()` is observed promptly even with a large tick interval.
_SLICE = 0.05


class SchedulerThread(QThread):
    """Tick a scheduler on an interval, emitting each due job name.

    Signals:
        due(str): a job name the scheduler reported as due on a tick.
    """

    due = Signal(str)

    def __init__(self, scheduler, *, interval: float = 30):
        super().__init__()
        self._scheduler = scheduler
        self._interval = interval
        self._stop = threading.Event()

    def stop(self) -> None:
        """Request the loop to end; ``run()`` returns within one sleep slice."""
        self._stop.set()

    def run(self) -> None:  # noqa: D401 - QThread entry point
        while not self._stop.is_set():
            for name in self._scheduler.tick():
                self.due.emit(name)
            self._sleep_interruptible()

    def _sleep_interruptible(self) -> None:
        """Sleep up to ``interval`` seconds, waking early if ``stop()`` is set."""
        deadline = time.monotonic() + self._interval
        while not self._stop.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._stop.wait(min(_SLICE, remaining))
