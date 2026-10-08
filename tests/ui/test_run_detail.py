"""Tests for run detail + job dialog (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from store_sync.core.config import (BindingConfig, ColumnBinding, JobConfig,
    SourceConfig, StoreConfig, WriteConfig)
from store_sync.core.registry import JobEntry
from store_sync.ui.run_detail import RunDetail
from store_sync.ui.dialogs import JobDialog

_app = QApplication.instance() or QApplication([])


def test_run_detail_loads():
    d = RunDetail()
    d.load({"id": 1, "status": "ok", "counts": {"add": 2}, "errors": []},
           [{"match_value": "1", "reason": "dup"}])
    assert "ok" in d.summary_label.text()


def test_job_dialog_builds_config():
    dlg = JobDialog()
    dlg.name_edit.setText("a")
    dlg.store_id_edit.setText("17620")
    dlg.id_column_edit.setText("No")
    dlg.survey_id_edit.setText("SURV")
    dlg.match_var_edit.setText("SbjNum")
    dlg.binding_model.set_columns([ColumnBinding("V", "A")])
    j = dlg.job()
    assert j.job == "a" and j.store.id == 17620
    assert j.binding.columns[0].store_col == "A"


# -- RunDetail ---------------------------------------------------------------

def _run(**kw):
    run = {"id": 7, "status": "failed",
           "counts": {"add": 3, "update": 1, "delete": 2},
           "errors": []}
    run.update(kw)
    return run


def test_run_detail_back_emits():
    d = RunDetail()
    seen = []
    d.back.connect(lambda: seen.append("back"))
    d.back_button.click()
    assert seen == ["back"]


def test_run_detail_uses_design_object_names():
    d = RunDetail()
    assert d.log_view.objectName() == "ConsoleLog"
    assert d.back_button.text().startswith("←")
    assert d.back_button.objectName() in ("", "btn_secondary")


def test_run_detail_loads_tables():
    d = RunDetail()
    d.load(_run(errors=["boom", "bang"]),
           [{"match_value": "1", "reason": "dup"},
            {"match_value": "2", "reason": "gap"}])
    assert "failed" in d.summary_label.text()
    assert d.counts_model.rowCount() == 3
    assert d.errors_model.rowCount() == 2
    assert d.conflicts_model.rowCount() == 2
    from PySide6.QtCore import Qt
    vals = {d.counts_model.index(i, 0).data(Qt.DisplayRole):
            d.counts_model.index(i, 1).data(Qt.DisplayRole)
            for i in range(d.counts_model.rowCount())}
    assert vals == {"add": "3", "update": "1", "delete": "2"}


def test_run_detail_errors_accept_dicts():
    d = RunDetail()
    d.load(_run(errors=[{"error": "x", "detail": "y"}]), [])
    assert d.errors_model.rowCount() == 1
    assert d.conflicts_model.rowCount() == 0


def test_run_detail_load_resets_tables():
    d = RunDetail()
    d.load(_run(errors=["a"]), [{"match_value": "1", "reason": "r"}])
    d.load(_run(counts={"add": 1}, errors=[]), [])
    assert d.counts_model.rowCount() == 1
    assert d.errors_model.rowCount() == 0
    assert d.conflicts_model.rowCount() == 0


# -- JobDialog ---------------------------------------------------------------

def test_job_dialog_entry_fields():
    dlg = JobDialog()
    dlg.name_edit.setText("a")
    dlg.schedule_edit.setText("0 3 * * *")
    dlg.enabled_check.setChecked(True)
    assert dlg.entry_fields() == {
        "name": "a", "schedule": "0 3 * * *", "enabled": True}
    assert dlg.schedule == "0 3 * * *"
    assert dlg.enabled is True


def a_job():
    return JobConfig("a", StoreConfig(17620, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig())


def an_entry():
    return JobEntry(name="a", store_id=17620, id_column="No", survey_id="SURV",
        match_source_var="SbjNum", match_store_col="SbjNum", window_days=5,
        date_type="Upload", delete_policy="full_history", dry_run=True,
        enabled=True, schedule="0 3 * * *", column_count=1,
        last_status=None, last_finished_at=None)


def test_job_dialog_edit_round_trip():
    dlg = JobDialog(entry=an_entry(), job=a_job())
    assert dlg.name_edit.text() == "a"
    assert dlg.store_id_edit.text() == "17620"
    assert dlg.id_column_edit.text() == "No"
    assert dlg.survey_id_edit.text() == "SURV"
    assert dlg.match_var_edit.text() == "SbjNum"
    assert dlg.match_store_edit.text() == "SbjNum"
    assert dlg.schedule == "0 3 * * *"
    assert dlg.enabled is True
    assert dlg.binding_model.rowCount() == 1
    assert dlg.job() == a_job()
    assert dlg.entry_fields() == {
        "name": "a", "schedule": "0 3 * * *", "enabled": True}


def distinct_match_job():
    return JobConfig("a", StoreConfig(17620, "No"), SourceConfig("SURV", 5, "Upload"),
        BindingConfig(ColumnBinding("SbjNum", "SubjectID"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig())


def test_job_dialog_match_pair_round_trips_distinct():
    dlg = JobDialog(entry=an_entry(), job=distinct_match_job())
    assert dlg.match_var_edit.text() == "SbjNum"
    assert dlg.match_store_edit.text() == "SubjectID"
    j = dlg.job()
    assert j.binding.match.source_var == "SbjNum"
    assert j.binding.match.store_col == "SubjectID"


def test_job_dialog_match_store_defaults_to_var():
    dlg = JobDialog()
    dlg.match_var_edit.setText("SbjNum")
    assert dlg.job().binding.match.store_col == "SbjNum"


def test_job_dialog_defaults():
    dlg = JobDialog()
    assert not hasattr(dlg, "window_days_edit")
    assert not hasattr(dlg, "date_type_edit")
    assert dlg.delete_policy_combo.currentData() == "full_history"
    assert dlg.dry_run_check.isChecked() is True
    assert dlg.enabled is False
    assert dlg.binding_model.rowCount() == 0


def test_job_dialog_button_design_icons():
    dlg = JobDialog()
    assert dlg.ok_button.text().startswith("💾")
    assert dlg.ok_button.objectName() == "btn_primary"
    assert dlg.cancel_button.text() == "Cancel"


# -- ScheduleOverview --------------------------------------------------------

def test_schedule_overview_rows_and_model():
    from store_sync.ui.dialogs import ScheduleOverview

    dlg = ScheduleOverview([
        {"name": "a", "cron": "0 3 * * *", "enabled": True},
        {"name": "b", "cron": "", "enabled": False},
    ])
    assert [r["name"] for r in dlg.rows()] == ["a", "b"]
    assert dlg.model.rowCount() == 2
    from PySide6.QtCore import Qt
    assert dlg.model.index(1, 2).data(Qt.DisplayRole) == "no"
    assert dlg.close_button.text() == "Close"
