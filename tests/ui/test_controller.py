"""Tests for the GUI controller wiring (offscreen Qt)."""
import os
import threading
import time
import types

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from store_sync.core.config import (  # noqa: E402
    BindingConfig,
    ColumnBinding,
    JobConfig,
    SourceConfig,
    StoreConfig,
    WriteConfig,
)
from store_sync.core.registry import JobEntry  # noqa: E402
from store_sync.gui import Controller  # noqa: E402
from store_sync.ui.main_window import MainWindow  # noqa: E402

_app = QApplication.instance() or QApplication([])


def a_job(name="a"):
    return JobConfig(
        name,
        StoreConfig(17620, "No"),
        SourceConfig("SURV"),
        BindingConfig(
            ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")],
        ),
        WriteConfig(dry_run=True),
    )


def test_save_lists_on_dashboard(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)
    assert c.list_entries()[0].name == "a"
    w.show_dashboard()
    assert w.dashboard.model.rowCount() == 1
    c.stop()
    w.close()


def test_run_now_enqueues(tmp_path):
    w = MainWindow()
    calls = []
    c = Controller(
        w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: calls.append(name)
    )
    c.save("a", a_job(), schedule="", enabled=False)
    c.start()
    c.run_now("a")
    time.sleep(0.2)
    assert calls == ["a"]
    c.stop()
    w.close()


def test_save_twice_updates_not_duplicates(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)
    assert w.dashboard.model.rowCount() == 1
    c.save("a", a_job(), schedule="0 * * * *", enabled=True)
    assert len(c.list_entries()) == 1
    assert w.dashboard.model.rowCount() == 1
    entry = c.list_entries()[0]
    assert entry.schedule == "0 * * * *"
    assert entry.enabled is True
    c.stop()
    w.close()


def test_delete_removes_and_refreshes(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)
    assert w.dashboard.model.rowCount() == 1
    c.delete("a")
    assert c.list_entries() == []
    assert w.dashboard.model.rowCount() == 0
    c.stop()
    w.close()


def test_stop_is_idempotent(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.start()
    c.stop()
    c.stop()  # must not raise the second time
    w.close()


def test_save_defaults_blank_match_store_col(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    base = a_job()
    blank = JobConfig(
        base.job,
        base.store,
        base.source,
        BindingConfig(ColumnBinding("SbjNum", ""), base.binding.columns),
        base.write,
    )
    c.save("a", blank, schedule="", enabled=False)
    assert c.list_entries()[0].match_store_col == "SbjNum"
    c.stop()
    w.close()


def test_worker_thread_db_access(tmp_path):
    """The queue worker must touch Registry/State without a thread error."""
    w = MainWindow()
    results = []

    def run_fn(name, stop):
        results.append(c.registry.get(name))
        results.append(c.state.start_run(job=name, dry_run=True))

    c = Controller(w, db_path=tmp_path / "s.db", run_fn=run_fn)
    c.save("a", a_job(), schedule="", enabled=False)
    c.start()
    c.run_now("a")
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and not results:
        time.sleep(0.02)
    c.stop()
    w.close()

    assert len(results) == 2
    assert results[0].job == "a"
    assert isinstance(results[1], int)
    run = c_state_get(tmp_path / "s.db", results[1])
    assert run is not None and run["job"] == "a"


def test_registry_from_plain_thread(tmp_path):
    """`list_entries` from a non-GUI thread must not raise a thread error."""
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)

    out = {}

    def worker():
        try:
            out["entries"] = [e.name for e in c.registry.list_entries()]
        except Exception as exc:  # noqa: BLE001
            out["error"] = exc

    th = threading.Thread(target=worker)
    th.start()
    th.join(3)
    c.stop()
    w.close()

    assert "error" not in out, out["error"]
    assert out["entries"] == ["a"]


def test_save_bad_config_reports_error(tmp_path):
    """A non-numeric store id must not raise out of the save slot."""
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    w.show_job("a")
    w.job_detail.store_id_edit.setText("not-a-number")
    c._save_from_detail()  # must not raise
    assert c.list_entries() == []
    assert "error" in w.status_label.text().lower()
    assert "#f87171" in w.status_label.styleSheet()
    c.stop()
    w.close()


def test_preflight_unconfigured_env_reports_error(tmp_path, monkeypatch):
    """`_client()` raising (no STG_SERVER) must not escape the preflight slot."""
    monkeypatch.delenv("STG_SERVER", raising=False)
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    from store_sync.core.registry import JobEntry

    entry = JobEntry(
        name="a", store_id=17620, id_column="No", survey_id="SURV",
        match_source_var="SbjNum", match_store_col="SbjNum",
        delete_policy="full_history", dry_run=True,
        enabled=False, schedule="", column_count=1, last_status=None,
        last_finished_at=None,
    )
    w.job_detail.load(entry, a_job())
    c._preflight()  # must not raise; client construction fails before any request
    assert "error" in w.status_label.text().lower()
    assert "#f87171" in w.status_label.styleSheet()
    c.stop()
    w.close()


def test_connections_refused_after_stop(tmp_path):
    """A lazy connection request after `stop()` must not leak a tracked conn."""
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.start()
    c.stop()
    for factory in (c._state, c._registry):
        try:
            factory()
        except RuntimeError:
            pass
        else:  # pragma: no cover - a returned conn would be a leak
            raise AssertionError("factory returned a connection after stop()")
    assert c._conns == []
    w.close()


def c_state_get(path, run_id):
    """Read a run row with a fresh connection (no shared-thread issues)."""
    from store_sync.core.state import State

    st = State(path)
    try:
        return st.get_run(run_id)
    finally:
        st.close()


# -- Important 1: history / conflicts / run detail --------------------------

def _seed_run(c, *, job="a", status="ok", counts=None, conflict=None):
    """Record one finished run (+ optional conflict) via the controller's State."""
    run_id = c.state.start_run(job=job, dry_run=True)
    if conflict is not None:
        c.state.record_conflict(run_id, match_value=conflict[0], reason=conflict[1])
    c.state.finish_run(run_id, status=status, counts=counts or {"add": 1})
    return run_id


def test_open_job_fills_history_and_conflicts(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)
    _seed_run(c, conflict=("7", "duplicate key"))
    c._open_job("a")
    assert w.job_detail.history_model.rowCount() >= 1
    assert w.job_detail.conflicts_model.rowCount() == 1
    row = w.job_detail.history_model._rows[0]
    assert {"time", "status", "counts"} <= set(row)
    c.stop()
    w.close()


def test_open_job_without_runs_is_empty(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)
    c._open_job("a")
    assert w.job_detail.history_model.rowCount() == 0
    assert w.job_detail.conflicts_model.rowCount() == 0
    c.stop()
    w.close()


def test_activate_history_row_loads_run_detail(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)
    run_id = _seed_run(c, status="ok", counts={"add": 2},
                       conflict=("9", "dup"))
    c._open_job("a")
    seen = []
    w.run_activated.connect(seen.append)
    w.job_detail.history_view.doubleClicked.emit(
        w.job_detail.history_model.index(0, 0)
    )
    assert seen == [run_id]
    assert w.stack.currentWidget() is w.run_detail
    assert f"Run #{run_id}" in w.run_detail.summary_label.text()
    assert w.run_detail.conflicts_model.rowCount() == 1
    c.stop()
    w.close()


def test_run_activated_noop_for_missing_run(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._show_run(999999)  # unknown id must not raise or switch pages
    assert w.stack.currentWidget() is w.dashboard
    c.stop()
    w.close()


# -- Important 2: schedules overview ----------------------------------------

def test_main_window_relays_schedules_requested():
    w = MainWindow()
    seen = []
    w.schedules_requested.connect(lambda: seen.append(1))
    w.dashboard.schedules_requested.emit()
    assert seen == [1]
    w.close()


def test_controller_show_schedules_builds_rows(tmp_path, monkeypatch):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="0 3 * * *", enabled=True)
    c.save("b", a_job("b"), schedule="", enabled=False)
    shown = {}

    class FakeOverview:
        def __init__(self, rows, parent=None):
            shown["rows"] = rows

        def exec(self):
            return 0

    monkeypatch.setattr("store_sync.gui.ScheduleOverview", FakeOverview, raising=False)
    c._show_schedules()  # must not raise
    rows = shown["rows"]
    assert len(rows) == 2
    assert {"name", "cron", "enabled"} <= set(rows[0])
    c.stop()
    w.close()


# -- Important 3: delete confirmation ---------------------------------------

def test_confirm_delete_cancel_keeps_job(tmp_path, monkeypatch):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)
    monkeypatch.setattr(
        "store_sync.gui.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.No),
    )
    c._confirm_delete("a")
    assert [e.name for e in c.list_entries()] == ["a"]
    c.stop()
    w.close()


def test_confirm_delete_yes_removes_job(tmp_path, monkeypatch):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule="", enabled=False)
    monkeypatch.setattr(
        "store_sync.gui.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.Yes),
    )
    c._confirm_delete("a")
    assert c.list_entries() == []
    c.stop()
    w.close()


# -- Important 4: off-thread preflight / mapping ----------------------------

class _BlockingClient:
    """Fake STG client whose `get_store` blocks until `release` is set."""

    def __init__(self, release, calls):
        self.session = types.SimpleNamespace(close=lambda: calls.append("closed"))
        self.release = release
        self.calls = calls

    def get_store(self, store_id):
        self.release.wait(3)
        self.calls.append(("get_store", store_id))
        return [{"No": 1}]

    def get_survey_xml(self, survey_id):
        self.calls.append(("get_survey_xml", survey_id))
        return "<Survey/>"


class _Report:
    ok = True
    table: list = []


def _preflight_entry():
    return JobEntry(
        name="a", store_id=17620, id_column="No", survey_id="SURV",
        match_source_var="SbjNum", match_store_col="SbjNum",
        delete_policy="full_history", dry_run=True,
        enabled=False, schedule="", column_count=1, last_status=None,
        last_finished_at=None,
    )


def test_preflight_returns_immediately(tmp_path, monkeypatch):
    """The GUI thread must not wait on the network fetch."""
    import store_sync.gui as gui

    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    w.job_detail.load(_preflight_entry(), a_job())

    release = threading.Event()
    calls: list = []
    client = _BlockingClient(release, calls)
    monkeypatch.setattr(c, "_client", lambda: client)

    def fake_preflight(job, client):
        # Actually block on the (fake) network so a sync slot would hang.
        client.get_store(job.store.id)
        return _Report(), {}

    monkeypatch.setattr(gui, "run_preflight", fake_preflight)

    start = time.monotonic()
    c._preflight()
    elapsed = time.monotonic() - start
    # Slot returned without waiting on the blocking client.
    assert elapsed < 1.0
    assert calls == []  # fetch has not run on the GUI thread
    assert "preflight" in w.status_label.text().lower()
    assert c._preflight_thread is not None and c._preflight_thread.isRunning()

    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and c._preflight_thread.isRunning():
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    assert w.job_detail.preflight_model.rowCount() == 0  # empty report table
    assert "closed" in calls  # worker owned + closed its own session
    c.stop()
    assert c._preflight_thread is None  # joined on stop()
    w.close()


def test_preflight_worker_failure_reports_error(tmp_path, monkeypatch):
    import store_sync.gui as gui

    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    w.job_detail.load(_preflight_entry(), a_job())
    monkeypatch.setattr(c, "_client", lambda: _BlockingClient(threading.Event(), []))

    def boom(job, client):
        raise RuntimeError("nope")

    monkeypatch.setattr(gui, "run_preflight", boom)
    c._preflight()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and c._preflight_thread.isRunning():
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    assert "error" in w.status_label.text().lower()
    c.stop()
    w.close()


def test_mapping_import_off_thread(tmp_path, monkeypatch):
    import store_sync.gui as gui

    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    w.job_detail.load(_preflight_entry(), a_job())

    release = threading.Event()
    calls: list = []
    client = _BlockingClient(release, calls)
    monkeypatch.setattr(c, "_client", lambda: client)
    monkeypatch.setattr(
        "store_sync.gui.parse_mapping",
        lambda path, store_cols, source_vars: (
            [ColumnBinding("SbjNum", "No")], []
        ),
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getOpenFileName",
        staticmethod(lambda *a, **k: ("m.xlsx", "")),
    )

    c._import_mapping()
    assert calls == []  # pool fetch has not happened on the GUI thread
    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and c._mapping_thread.isRunning():
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    assert w.job_detail.binding_model.rowCount() == 1
    assert "closed" in calls
    c.stop()
    assert c._mapping_thread is None
    w.close()


def test_mapping_export_off_thread(tmp_path, monkeypatch):
    import store_sync.gui as gui

    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    w.job_detail.load(_preflight_entry(), a_job())
    release = threading.Event()
    calls: list = []
    client = _BlockingClient(release, calls)
    monkeypatch.setattr(c, "_client", lambda: client)
    written: list = []
    monkeypatch.setattr(
        "store_sync.gui.export_template",
        lambda path, store_cols: written.append((path, list(store_cols))),
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QFileDialog.getSaveFileName",
        staticmethod(lambda *a, **k: ("m.xlsx", "")),
    )

    c._export_mapping()
    assert calls == []
    release.set()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline and c._mapping_thread.isRunning():
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()
    assert written and written[0][0] == "m.xlsx"
    c.stop()
    w.close()


# -- Important 4 (round 2): worker ownership / re-entrancy / GUI delivery ----

def _arm_preflight(c, w, monkeypatch, release, calls):
    """Wire a blocking preflight worker onto `c` and return the imported module."""
    import store_sync.gui as gui

    c._detail_name = "a"
    w.job_detail.load(_preflight_entry(), a_job())
    monkeypatch.setattr(c, "_client", lambda: _BlockingClient(release, calls))

    def fake_preflight(job, client):
        client.get_store(job.store.id)  # blocks until `release` is set
        return _Report(), {}

    monkeypatch.setattr(gui, "run_preflight", fake_preflight)
    return gui


def _drain(c, attr, timeout=3.0):
    """Spin the event loop until the `attr` slot worker stops running."""
    deadline = time.monotonic() + timeout
    worker = getattr(c, attr)
    while (
        time.monotonic() < deadline
        and worker is not None
        and worker.isRunning()
    ):
        QCoreApplication.processEvents()
        time.sleep(0.01)
    QCoreApplication.processEvents()


def test_stop_midflight_joins_unbounded_without_abort(tmp_path, monkeypatch):
    """`stop()` must join a running worker (never drop its last ref → abort)."""
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    release = threading.Event()
    calls: list = []
    _arm_preflight(c, w, monkeypatch, release, calls)

    c._preflight()
    worker = c._preflight_thread
    assert worker is not None and worker.isRunning()
    # The strong-ref container keeps the running QThread alive (invariant).
    assert worker in c._workers

    done = {}

    def do_stop():
        c.stop()
        done["stopped"] = True

    th = threading.Thread(target=do_stop)
    th.start()
    time.sleep(0.2)
    # Unbounded join: still blocked on the in-flight request, not abandoned.
    assert "stopped" not in done
    assert c._preflight_thread is worker

    release.set()  # let the client's own call return
    th.join(5)
    assert done.get("stopped") is True
    assert not worker.isRunning()
    assert c._preflight_thread is None
    assert c._workers == set()
    w.close()


def test_reentrant_preflight_does_not_block_gui(tmp_path, monkeypatch):
    """A second click while in flight returns at once with a busy status."""
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    release = threading.Event()
    calls: list = []
    _arm_preflight(c, w, monkeypatch, release, calls)

    c._preflight()
    first = c._preflight_thread
    assert first is not None and first.isRunning()

    start = time.monotonic()
    c._preflight()  # re-entrant click on the GUI thread
    elapsed = time.monotonic() - start
    assert elapsed < 0.5  # did not join/block on the in-flight fetch
    assert "already running" in w.status_label.text().lower()
    assert c._preflight_thread is first  # not joined + replaced
    assert first.isRunning()

    release.set()
    _drain(c, "_preflight_thread")
    c.stop()
    w.close()


def test_preflight_result_delivered_on_gui_thread(tmp_path, monkeypatch):
    """The worker's `ok` slot must run on the GUI (main) thread."""
    import store_sync.gui as gui

    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._detail_name = "a"
    w.job_detail.load(_preflight_entry(), a_job())

    ready = threading.Event()
    ready.set()  # non-blocking fake client
    monkeypatch.setattr(c, "_client", lambda: _BlockingClient(ready, []))
    monkeypatch.setattr(gui, "run_preflight", lambda job, client: (_Report(), {}))

    seen: dict = {}
    main_ident = threading.get_ident()

    def record(result):
        seen["ident"] = threading.get_ident()
        Controller._on_preflight_ok(c, result)

    monkeypatch.setattr(c, "_on_preflight_ok", record)

    c._preflight()
    _drain(c, "_preflight_thread")

    assert seen.get("ident") == main_ident  # delivered on the GUI thread
    assert "preflight ok" in w.status_label.text().lower()
    c.stop()
    w.close()
