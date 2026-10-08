"""Tests for the GUI controller wiring (offscreen Qt)."""
import os
import threading
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from store_sync.core.config import (  # noqa: E402
    BindingConfig,
    ColumnBinding,
    JobConfig,
    SourceConfig,
    StoreConfig,
    WriteConfig,
)
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
        match_source_var="SbjNum", match_store_col="SbjNum", window_days=0,
        date_type="Upload", delete_policy="full_history", dry_run=True,
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
