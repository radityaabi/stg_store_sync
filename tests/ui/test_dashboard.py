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
