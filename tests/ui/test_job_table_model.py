"""Tests for the GUI models (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from store_sync.core.config import ColumnBinding
from store_sync.ui.job_table_model import JobTableModel
from store_sync.ui.models import JobListModel

_app = QApplication.instance() or QApplication([])

def test_job_table_edit_reaches_model():
    m = JobTableModel()
    m.set_columns([ColumnBinding("", "", "text")])
    m.setData(m.index(0, 0), "I7_1_2", Qt.EditRole)
    m.setData(m.index(0, 1), "T_QPM1_1", Qt.EditRole)
    assert m.columns()[0].store_col == "I7_1_2"
    assert m.columns()[0].source_var == "T_QPM1_1"

def test_job_list_model_rows():
    class E:
        name="a"; store_id=1; survey_id="s"; schedule="* * * * *"
        enabled=True; last_status="ok"; last_finished_at="t"
    m = JobListModel()
    m.set_entries([E()])
    assert m.rowCount() == 1
    assert m.entry_at(0).name == "a"
