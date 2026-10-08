"""Tests for the store-detail pane (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from store_sync.core.config import (BindingConfig, ColumnBinding, JobConfig,
    SourceConfig, StoreConfig, WriteConfig)
from store_sync.ui.job_detail import JobDetail

_app = QApplication.instance() or QApplication([])

def a_job():
    return JobConfig("a", StoreConfig(17620, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig())

class E:
    name="a"; store_id=17620; survey_id="SURV"; schedule=""
    enabled=False; last_status=None; last_finished_at=None; column_count=1

def test_load_and_edit_binding():
    d = JobDetail()
    d.load(E(), a_job())
    assert d.bindings()[0].store_col == "I7_1_2"
    d.binding_model.setData(d.binding_model.index(0, 0), "I8a_1", Qt.EditRole)
    assert d.bindings()[0].store_col == "I8a_1"

def test_approve_enables_and_signals():
    d = JobDetail()
    d.load(E(), a_job())
    seen = []
    d.approve_requested.connect(lambda: seen.append(1))
    d.approve()
    assert seen == [1]

def test_approve_checks_enabled():
    d = JobDetail()
    d.load(E(), a_job())
    assert d.enabled_check.isChecked() is False
    d.approve()
    assert d.enabled_check.isChecked() is True

def test_action_rail_buttons_emit():
    d = JobDetail()
    seen = []
    d.back.connect(lambda: seen.append("back"))
    d.preflight_requested.connect(lambda: seen.append("preflight"))
    d.approve_requested.connect(lambda: seen.append("approve"))
    d.save_requested.connect(lambda: seen.append("save"))
    d.run_requested.connect(lambda: seen.append("run"))
    d.stop_requested.connect(lambda: seen.append("stop"))
    d.import_requested.connect(lambda: seen.append("import"))
    d.export_requested.connect(lambda: seen.append("export"))
    d.back_button.click()
    d.preflight_button.click()
    d.approve_button.click()
    d.save_button.click()
    d.run_button.click()
    d.stop_button.click()
    d.import_button.click()
    d.export_button.click()
    assert seen == [
        "back", "preflight", "approve", "save", "run", "stop",
        "import", "export",
    ]

def test_config_fields_round_trip():
    d = JobDetail()
    d.load(E(), a_job())
    assert d.config_fields() == {
        "store_id": 17620,
        "id_column": "No",
        "survey_id": "SURV",
        "match_source_var": "SbjNum",
        "window_days": 0,
        "date_type": "Upload",
        "delete_policy": "full_history",
        "dry_run": True,
    }

def test_set_pull_terms_and_dict_tables():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_pull_terms({"T_QPM1_1": "term1"})
    assert d.binding_model.data(d.binding_model.index(0, 2), Qt.DisplayRole) == "term1"
    d.set_history([{"time": "t", "status": "ok", "counts": "1"}])
    assert d.history_model.rowCount() == 1
    d.set_preflight([{"column": "c", "status": "s", "detail": "d"}])
    assert d.preflight_model.rowCount() == 1
    d.set_conflicts([{"match": "m", "reason": "r", "side": "x"}])
    assert d.conflicts_model.rowCount() == 1
