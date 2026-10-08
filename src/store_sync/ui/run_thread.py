"""RunThread — run one sync job off the UI thread (spec §7).

The controller owns the worker: it builds a :class:`RunThread` with the same
``run_fn(name, should_stop)`` signature :class:`~store_sync.core.run_queue.RunQueue`
uses, starts it, and connects ``finished_result`` to a slot on the UI thread.
Cancellation is cooperative — :meth:`stop` tripping a ``threading.Event`` the
``should_stop`` predicate reads — so a run always ends and the thread never
leaks (AGENTS.md §3.4).
"""
from __future__ import annotations

import threading
from typing import Callable

from PySide6.QtCore import QThread, Signal


class RunThread(QThread):
    """Run ``run_fn(name, should_stop)`` once and emit its result.

    Signals:
        finished_result(object): the value returned by ``run_fn`` (a
            ``SyncResult`` in production).
    """

    finished_result = Signal(object)

    def __init__(self, run_fn: Callable[[str, Callable[[], bool]], object], name: str):
        super().__init__()
        self._run_fn = run_fn
        self._name = name
        self._stop = threading.Event()

    def stop(self) -> None:
        """Request cooperative cancellation of the in-flight run."""
        self._stop.set()

    def run(self) -> None:  # noqa: D401 - QThread entry point
        result = self._run_fn(self._name, self._stop.is_set)
        self.finished_result.emit(result)
