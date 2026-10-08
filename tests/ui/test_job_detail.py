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

def b_job():
    return JobConfig("b", StoreConfig(77, "Pid"), SourceConfig("S2"),
        BindingConfig(ColumnBinding("OtherId", "OtherId"),
            [ColumnBinding("T_X", "X1")]), WriteConfig())

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
        "match_store_col": "SbjNum",
        "window_days": 0,
        "date_type": "Upload",
        "delete_policy": "full_history",
        "dry_run": True,
    }


def test_match_store_col_round_trips_distinct():
    d = JobDetail()
    d.load(E(), a_job())
    d.match_var_edit.setText("SbjNum")
    d.match_store_edit.setText("SubjectID")
    assert d.config_fields()["match_source_var"] == "SbjNum"
    assert d.config_fields()["match_store_col"] == "SubjectID"

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

def test_reload_clears_stale_pull_terms():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_pull_terms({"T_QPM1_1": "X"})
    assert d.binding_model.data(d.binding_model.index(0, 2), Qt.DisplayRole) == "X"
    d.load(E(), b_job())
    # Fresh load: old job's pull term must not survive into the new columns.
    assert d.binding_model.data(d.binding_model.index(0, 2), Qt.DisplayRole) == ""
    assert d.binding_model._pull_terms == {}

def test_config_fields_tolerates_whitespace():
    d = JobDetail()
    d.load(E(), a_job())
    d.store_id_edit.setText("   ")
    d.window_days_edit.setText("  ")
    assert d.config_fields()["store_id"] == 0
    assert d.config_fields()["window_days"] == 0


def test_rail_buttons_carry_design_icons_and_object_names():
    d = JobDetail()
    assert d.back_button.text().startswith("←")
    assert d.preflight_button.text().startswith("✔")
    assert d.preflight_button.objectName() == "btn_warning"
    assert d.approve_button.text().startswith("▶")
    assert d.approve_button.objectName() == "btn_success"
    assert d.save_button.text().startswith("💾")
    assert d.save_button.objectName() == "btn_primary"
    assert d.run_button.text().startswith("▶")
    assert d.run_button.objectName() == "btn_primary"
    assert d.stop_button.text().startswith("⏹")
    assert d.stop_button.objectName() == "btn_danger"


def test_column_buttons_carry_design_icons_and_object_names():
    d = JobDetail()
    assert d.add_column_button.text().startswith("➕")
    assert d.add_column_button.objectName() == "btn_primary"
    assert d.remove_column_button.text().startswith("🗑")
    assert d.remove_column_button.objectName() == "btn_danger"
    assert d.import_button.text().startswith("📥")
    assert d.export_button.text().startswith("📤")
    assert d.schedule_preview.objectName() == "SchedulePreview"


def test_history_double_click_emits_run_activated():
    d = JobDetail()
    seen = []
    d.run_activated.connect(seen.append)
    d.set_history([
        {"time": "t", "status": "ok", "counts": "1", "id": 42},
        {"time": "t2", "status": "ok", "counts": "1", "id": 7},
    ])
    d.history_view.doubleClicked.emit(d.history_model.index(1, 0))
    assert seen == [7]
