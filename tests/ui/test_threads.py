"""Tests for the Qt run/scheduler threads (offscreen Qt)."""
import os, time, threading
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication
from store_sync.ui.run_thread import RunThread
from store_sync.ui.scheduler_thread import SchedulerThread

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


def test_run_thread_emits_error_marker_on_exception():
    def run_fn(name, should_stop):
        raise RuntimeError("boom")
    t = RunThread(run_fn, "a")
    got = []
    t.finished_result.connect(got.append)
    t.start()
    assert t.wait(3000)
    QCoreApplication.processEvents()
    assert len(got) == 1
    assert getattr(got[0], "status", None) == "error"


def test_scheduler_thread_emits_due_names():
    got = []

    class FakeScheduler:
        def __init__(self):
            self._calls = 0

        def tick(self):
            self._calls += 1
            return ["a", "b"] if self._calls == 1 else []

    t = SchedulerThread(FakeScheduler(), interval=0.05)
    t.due.connect(got.append)
    t.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and len(got) < 2:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    t.stop()
    t.wait(3000)
    QCoreApplication.processEvents()
    assert got == ["a", "b"]


def test_scheduler_thread_survives_tick_exception():
    got = []

    class FlakyScheduler:
        def __init__(self):
            self._calls = 0

        def tick(self):
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("transient")
            return ["a"]

    t = SchedulerThread(FlakyScheduler(), interval=0.05)
    t.due.connect(got.append)
    t.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not got:
        QCoreApplication.processEvents()
        time.sleep(0.01)
    t.stop()
    assert t.wait(3000)
    QCoreApplication.processEvents()
    assert got == ["a"]


def test_scheduler_thread_stop_returns_promptly():
    class IdleScheduler:
        def tick(self):
            return []

    t = SchedulerThread(IdleScheduler(), interval=30)
    t.start()
    time.sleep(0.05)
    t.stop()
    assert t.wait(2000)
