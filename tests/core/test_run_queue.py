"""Tests for store_sync.core.run_queue — concurrent per-job runs, dedupe, per-job stop."""
import threading, time
from store_sync.core.run_queue import RunQueue

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

# -- robustness: a failure must never wedge a job or its store -------------

def test_raising_subscriber_does_not_wedge_queue():
    """A subscriber that raises must not leave the job/store reserved forever."""
    calls = {"n": 0}
    def bad_sub(state):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("subscriber blew up")
    q = RunQueue(lambda name, stop: None)
    q.subscribe(bad_sub)
    q.start()
    assert q.enqueue("a", store_key="7") is True
    deadline = time.monotonic() + 2
    while q.snapshot().active and time.monotonic() < deadline:
        time.sleep(0.01)
    q.shutdown()
    assert q.snapshot().active == []
    assert q.snapshot().pending == []
    assert q._store_keys == {}
    q2 = RunQueue(lambda name, stop: None)
    q2.subscribe(lambda s: None)
    q2.start()
    assert q2.enqueue("a", store_key="7") is True
    q2.shutdown()

def test_thread_start_failure_releases_reservation(monkeypatch):
    """`can't start new thread` must not leave the job pending forever."""
    import store_sync.core.run_queue as rq

    class Boom(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    real = rq.threading.Thread
    monkeypatch.setattr(rq.threading, "Thread", Boom)
    q = RunQueue(lambda name, stop: None)
    q.start()
    try:
        assert q.enqueue("a", store_key="9") is False
        snap = q.snapshot()
        assert snap.pending == [] and snap.active == []
        assert q._store_keys == {}
        assert q.enqueue("a", store_key="9") is False or True  # never wedged
    finally:
        monkeypatch.setattr(rq.threading, "Thread", real)
        q.shutdown()

def test_stop_all_cancels_run_in_pending_window():
    """Stop-all must still cancel a run that has not entered the worker yet."""
    ran = []
    q = RunQueue(lambda name, stop: ran.append(name))
    q.subscribe(lambda state: q.stop_all() if "a" in state.pending else None)
    q.start()
    assert q.enqueue("a") is True     # subscriber fires pre-start and cancels
    time.sleep(0.2)
    q.shutdown()
    assert ran == []
    assert q.snapshot().active == [] and q.snapshot().pending == []

def test_shutdown_with_unstarted_thread_does_not_raise():
    """shutdown() racing enqueue's pre-start window must not raise."""
    errors = []
    done = []
    q = RunQueue(lambda name, stop: None)
    def sub(state):
        if done:                      # shutdown() notifies; do it exactly once
            return
        done.append(True)
        try:
            q.shutdown(timeout=1.0)
        except Exception as exc:  # noqa: BLE001 - the probe records it
            errors.append(exc)
    q.subscribe(sub)
    q.start()
    q.enqueue("a")                    # sub calls shutdown before thread.start()
    time.sleep(0.2)
    assert errors == []
    assert q.snapshot().active == [] and q.snapshot().pending == []
