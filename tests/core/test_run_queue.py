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


def test_stop_all_drops_pending_and_stops_active():
    started = threading.Event()
    finished = threading.Event()
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
    q.enqueue("b")
    assert q.snapshot().active == "a"
    assert q.snapshot().pending == ["b"]
    q.stop_all()
    assert q.snapshot().pending == []              # pending dropped
    assert finished.wait(2)                         # active run saw should_stop
    q.shutdown()
    assert q.snapshot().active is None


def test_subscribe_called_on_every_change():
    seen_states = []
    gate = threading.Event()
    def run_fn(name, should_stop):
        gate.wait(2)
        return name
    q = RunQueue(run_fn)
    q.subscribe(seen_states.append)
    q.start()
    assert q.enqueue("a") is True
    time.sleep(0.1)                                 # let the worker start the run
    assert seen_states                                  # called at least once
    assert any(s.active == "a" for s in seen_states)    # saw the active job
    gate.set()
    q.shutdown()
    assert seen_states[-1].active is None               # final change observed


def test_shutdown_is_bounded_when_idle():
    q = RunQueue(lambda name, should_stop: name)
    q.start()
    before = time.monotonic()
    q.shutdown(timeout=2.0)
    assert time.monotonic() - before < 1.0              # returns promptly


def test_shutdown_before_start_does_not_queue_stale_sentinel():
    gate = threading.Event()
    q = RunQueue(lambda name, should_stop: gate.wait(2))
    q.shutdown()                                        # no worker yet
    q.start()
    assert q.enqueue("a") is True
    time.sleep(0.1)
    assert q.snapshot().active == "a"                   # worker not wedged
    gate.set()
    q.shutdown()


def test_shutdown_bounded_when_run_ignores_stop():
    started = threading.Event()
    release = threading.Event()
    def run_fn(name, should_stop):
        started.set()
        release.wait(5)                                 # ignores should_stop
        return name
    q = RunQueue(run_fn)
    q.start()
    q.enqueue("a")
    started.wait(2)
    before = time.monotonic()
    q.shutdown(timeout=0.2)                             # must not hang
    assert time.monotonic() - before < 2.0
    release.set()

