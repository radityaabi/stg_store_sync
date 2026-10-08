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
