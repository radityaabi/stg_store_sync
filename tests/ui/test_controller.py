"""Tests for the GUI controller wiring (offscreen Qt)."""
import os
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
