"""Tests for run detail + job dialog (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QTime
from PySide6.QtWidgets import QApplication
from store_sync.core.config import (BindingConfig, ColumnBinding, JobConfig,
    Schedule, SourceConfig, StoreConfig, WriteConfig)
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
    dlg.schedule_unit_combo.setCurrentIndex(
        dlg.schedule_unit_combo.findData("day")
    )
    dlg.schedule_every_spin.setValue(1)
    dlg.schedule_at_edit.setTime(QTime(3, 0))
    dlg.enabled_check.setChecked(True)
    assert dlg.entry_fields() == {
        "name": "a", "schedule": Schedule("day", 1, "03:00"), "enabled": True}
    assert dlg.schedule() == Schedule("day", 1, "03:00")
    assert dlg.enabled is True


def a_job():
    return JobConfig("a", StoreConfig(17620, "No"), SourceConfig("SURV"),
        BindingConfig(ColumnBinding("SbjNum", "SbjNum"),
            [ColumnBinding("T_QPM1_1", "I7_1_2")]), WriteConfig())


def an_entry():
    return JobEntry(name="a", store_id=17620, id_column="No", survey_id="SURV",
        match_source_var="SbjNum", match_store_col="SbjNum",
        dry_run=True,
        enabled=True, schedule=Schedule("day", 1, "03:00"), column_count=1,
        last_status=None, last_finished_at=None)


def test_job_dialog_edit_round_trip():
    dlg = JobDialog(entry=an_entry(), job=a_job())
    assert dlg.name_edit.text() == "a"
    assert dlg.store_id_edit.text() == "17620"
    assert dlg.id_column_edit.text() == "No"
    assert dlg.survey_id_edit.text() == "SURV"
    assert dlg.match_var_edit.text() == "SbjNum"
    assert dlg.match_store_edit.text() == "SbjNum"
    assert dlg.schedule() == Schedule("day", 1, "03:00")
    assert dlg.enabled is True
    assert dlg.binding_model.rowCount() == 1
    assert dlg.job() == a_job()
    assert dlg.entry_fields() == {
        "name": "a", "schedule": Schedule("day", 1, "03:00"), "enabled": True}


def distinct_match_job():
    return JobConfig("a", StoreConfig(17620, "No"), SourceConfig("SURV"),
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
    assert not hasattr(dlg, "delete_policy_combo")
    assert dlg.dry_run_check.isChecked() is True
    assert dlg.enabled is False
    assert dlg.binding_model.rowCount() == 0


def test_job_dialog_opens_usably_sized():
    """The dialog must open large enough that no manual resize is needed."""
    dlg = JobDialog()
    assert dlg.size().width() >= 560
    assert dlg.size().height() >= 480
    assert dlg.minimumSize().width() >= 560
    assert dlg.minimumSize().height() >= 480



def test_job_dialog_button_design_icons():
    dlg = JobDialog()
    assert dlg.ok_button.text().startswith("💾")
    assert dlg.ok_button.objectName() == "btn_primary"
    assert dlg.cancel_button.text() == "Cancel"


def test_job_dialog_schedule_fields_follow_unit():
    dlg = JobDialog()
    dlg.schedule_unit_combo.setCurrentIndex(
        dlg.schedule_unit_combo.findData("minute"))
    assert dlg.schedule_at_edit.isVisibleTo(dlg) is False
    assert dlg.schedule_weekday_combo.isVisibleTo(dlg) is False
    dlg.schedule_unit_combo.setCurrentIndex(dlg.schedule_unit_combo.findData("day"))
    assert dlg.schedule_at_edit.isVisibleTo(dlg) is True
    assert dlg.schedule_weekday_combo.isVisibleTo(dlg) is False
    dlg.schedule_unit_combo.setCurrentIndex(dlg.schedule_unit_combo.findData("week"))
    assert dlg.schedule_at_edit.isVisibleTo(dlg) is True
    assert dlg.schedule_weekday_combo.isVisibleTo(dlg) is True


def test_job_dialog_schedule_row_labels_follow_unit():
    dlg = JobDialog()
    dlg.show()
    at_label = dlg._form.labelForField(dlg.schedule_at_edit)
    weekday_label = dlg._form.labelForField(dlg.schedule_weekday_combo)
    assert at_label is not None and weekday_label is not None
    dlg.schedule_unit_combo.setCurrentIndex(
        dlg.schedule_unit_combo.findData("minute"))
    assert at_label.isHidden() is True
    assert weekday_label.isHidden() is True
    dlg.schedule_unit_combo.setCurrentIndex(dlg.schedule_unit_combo.findData("day"))
    assert at_label.isHidden() is False
    assert weekday_label.isHidden() is True
    dlg.schedule_unit_combo.setCurrentIndex(dlg.schedule_unit_combo.findData("week"))
    assert at_label.isHidden() is False
    assert weekday_label.isHidden() is False


def test_job_dialog_schedule_every_spin_range_follows_unit_and_clamps():
    dlg = JobDialog()
    dlg.schedule_unit_combo.setCurrentIndex(
        dlg.schedule_unit_combo.findData("minute"))
    dlg.schedule_every_spin.setValue(59)
    dlg.schedule_unit_combo.setCurrentIndex(dlg.schedule_unit_combo.findData("day"))
    assert dlg.schedule_every_spin.maximum() == 31
    assert dlg.schedule_every_spin.value() <= 31
    dlg.schedule_unit_combo.setCurrentIndex(dlg.schedule_unit_combo.findData("week"))
    assert dlg.schedule_every_spin.maximum() == 52
    assert 1 <= dlg.schedule_every_spin.value() <= 52


# -- ScheduleOverview --------------------------------------------------------

def test_schedule_overview_rows_and_model():
    from store_sync.core.config import human
    from store_sync.ui.dialogs import ScheduleOverview

    rows = [
        {"name": "a", "schedule": Schedule("day", 1, "03:00"), "enabled": True},
        {"name": "b", "schedule": Schedule(""), "enabled": False},
    ]
    dlg = ScheduleOverview(rows)
    assert [r["name"] for r in dlg.rows()] == ["a", "b"]
    assert dlg.model.rowCount() == 2
    from PySide6.QtCore import Qt
    assert dlg.model.index(1, 2).data(Qt.DisplayRole) == "no"
    assert dlg.model.index(0, 1).data(Qt.DisplayRole) == human(Schedule("day", 1, "03:00"))
    assert dlg.model.index(1, 1).data(Qt.DisplayRole) == "manual only"
    assert dlg.close_button.text() == "Close"


def test_run_detail_shows_notification_line():
    d = RunDetail()
    d.load(_run(), [], notification={"status": "sent", "recipients": "a@x.com"})
    assert "a@x.com" in d.notification_label.text()
    d.load(_run(), [], notification=None)
    assert "—" in d.notification_label.text()


def test_run_detail_notification_line_states_reason():
    d = RunDetail()
    d.load(_run(), [], notification={"status": "skipped", "reason": "no rows added"})
    assert "skipped" in d.notification_label.text()
    assert "no rows added" in d.notification_label.text()
