"""Tests for the dashboard (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from store_sync.ui.dashboard import Dashboard

_app = QApplication.instance() or QApplication([])

class E:
    def __init__(self, name):
        self.name=name; self.store_id=1; self.survey_id="s"
        self.schedule=""; self.enabled=False
        self.last_status=None; self.last_finished_at=None

def test_rows_and_selection():
    d = Dashboard()
    d.set_entries([E("a"), E("b")])
    assert d.model.rowCount() == 2
    d.table.selectRow(1)
    assert d.selected_name() == "b"

def test_queue_strip_and_stats():
    d = Dashboard()
    d.set_queue("a", ["b", "c"])
    d.set_stats(total=3, enabled=2, running=1, failed=0)
    assert "a" in d.queue_label.text()
    assert "b" in d.queue_label.text()


def test_run_edit_delete_emit_selected_name():
    d = Dashboard()
    d.set_entries([E("a"), E("b")])
    seen = []
    d.run_requested.connect(lambda n: seen.append(("run", n)))
    d.edit_requested.connect(lambda n: seen.append(("edit", n)))
    d.delete_requested.connect(lambda n: seen.append(("del", n)))
    d.table.selectRow(1)
    d.run_button.click()
    d.edit_button.click()
    d.delete_button.click()
    assert seen == [("run", "b"), ("edit", "b"), ("del", "b")]


def test_run_edit_delete_noop_without_selection():
    d = Dashboard()
    d.set_entries([E("a"), E("b")])
    seen = []
    d.run_requested.connect(seen.append)
    d.edit_requested.connect(seen.append)
    d.delete_requested.connect(seen.append)
    d.run_button.click()
    d.edit_button.click()
    d.delete_button.click()
    assert seen == []


def test_double_click_activates():
    d = Dashboard()
    d.set_entries([E("a"), E("b")])
    seen = []
    d.job_activated.connect(seen.append)
    d.table.doubleClicked.emit(d.model.index(1, 0))
    assert seen == ["b"]


def test_plain_signals_emit():
    d = Dashboard()
    seen = []
    d.new_requested.connect(lambda: seen.append("new"))
    d.stop_requested.connect(lambda: seen.append("stop"))
    d.schedules_requested.connect(lambda: seen.append("sched"))
    d.refresh_requested.connect(lambda: seen.append("refresh"))
    d.new_button.click()
    d.stop_button.click()
    d.schedules_button.click()
    d.refresh_button.click()
    assert seen == ["new", "stop", "sched", "refresh"]


def test_buttons_carry_design_icons_and_object_names():
    d = Dashboard()
    assert d.new_button.text().startswith("➕")
    assert d.new_button.objectName() == "btn_primary"
    assert d.run_button.text().startswith("▶")
    assert d.run_button.objectName() == "btn_primary"
    assert d.stop_button.text().startswith("⏹")
    assert d.stop_button.objectName() == "btn_danger"
    assert d.edit_button.text().startswith("✏")
    assert d.delete_button.text().startswith("🗑")
    assert d.delete_button.objectName() == "btn_danger"
    assert d.schedules_button.text().startswith("⏰")
    assert d.refresh_button.text().startswith("🔄")


def test_empty_state_visible_immediately_and_toggled():
    d = Dashboard()
    # Hidden flag reflects emptiness from construction (no set_entries needed).
    assert d.empty_label.isHidden() is False
    d.set_entries([E("a")])
    assert d.empty_label.isHidden() is True
    d.set_entries([])
    assert d.empty_label.isHidden() is False


