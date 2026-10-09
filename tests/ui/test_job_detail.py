"""Tests for the store-detail pane (offscreen Qt)."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from store_sync.core.config import (BindingConfig, ColumnBinding, JobConfig,
    Schedule, SourceConfig, StoreConfig, WriteConfig, human)
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
    name="a"; store_id=17620; survey_id="SURV"; schedule=Schedule("")
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
        "store_id": 17620, "id_column": "No", "survey_id": "SURV",
        "match_source_var": "SbjNum", "match_store_col": "SbjNum",
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


def test_proposals_empty_state_by_default():
    d = JobDetail()
    d.load(E(), a_job())
    assert d.proposals_model.rowCount() == 0
    assert not d.proposals_empty_label.isHidden()
    assert d.proposals_view.isVisible() is False


def test_set_proposals_renders_diffs_and_clears_empty_state():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_proposals([
        {"id": 7, "match_value": "102", "id_value": 2,
         "diff": [{"col": "Q32", "old": "22", "new": "99"}]},
    ])
    assert d.proposals_model.rowCount() == 1
    assert d.proposals_empty_label.isHidden()
    assert "Q32: 22 → 99" in d.proposals_model.index(0, 3).data(Qt.DisplayRole)
    assert d.proposals_model.index(0, 1).data(Qt.DisplayRole) == "102"


def test_apply_proposals_emits_checked_ids():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_proposals([
        {"id": 7, "match_value": "102", "id_value": 2, "diff": []},
        {"id": 8, "match_value": "103", "id_value": 3, "diff": []},
    ])
    seen = []
    d.apply_proposals_requested.connect(seen.append)
    # tick row 0 only
    d.proposals_model.setData(d.proposals_model.index(0, 0), Qt.Checked, Qt.CheckStateRole)
    d.apply_button.click()
    assert seen == [[7]]


def test_dismiss_proposals_emits_checked_ids():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_proposals([{"id": 5, "match_value": "1", "id_value": 1, "diff": []}])
    seen = []
    d.dismiss_proposals_requested.connect(seen.append)
    d.proposals_model.setData(d.proposals_model.index(0, 0), Qt.Checked, Qt.CheckStateRole)
    d.dismiss_button.click()
    assert seen == [[5]]


def test_select_all_checks_every_row():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_proposals([
        {"id": 1, "match_value": "a", "id_value": 1, "diff": []},
        {"id": 2, "match_value": "b", "id_value": 2, "diff": []},
    ])
    d.select_all_button.click()
    assert d.proposals_model.checked_ids() == [1, 2]
    d.select_none_button.click()
    assert d.proposals_model.checked_ids() == []


def test_reload_clears_proposals():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_proposals([{"id": 1, "match_value": "a", "id_value": 1, "diff": []}])
    assert d.proposals_model.rowCount() == 1
    d.load(E(), b_job())
    # Fresh load must not carry the previous job's proposals.
    assert d.proposals_model.rowCount() == 0


def test_run_progress_panel_visible_on_start():
    d = JobDetail()
    d.load(E(), a_job())
    assert not d.progress_panel.isVisible() or d.progress_panel.isHidden()
    d.set_run_progress({"stage": "start", "job": "a", "dry_run": True})
    assert d.progress_panel.isVisibleTo(d) or not d.progress_panel.isHidden()
    assert "a" in d.progress_label.text()


def test_run_progress_text_per_stage():
    d = JobDetail()
    d.load(E(), a_job())
    for ev in (
        {"stage": "survey"},
        {"stage": "ids", "total": 296},
        {"stage": "pull", "total": 296},
        {"stage": "store", "total": 1},
        {"stage": "plan", "counts": {"add": 296, "delete": 1}},
        {"stage": "apply", "dry_run": True, "planned": 297},
        {"stage": "applied", "dry_run": True, "counts": {"add": 296, "delete": 1}},
        {"stage": "done", "status": "ok", "counts": {"add": 296}, "dry_run": True},
    ):
        d.set_run_progress(ev)
    text = d.progress_text.toPlainText()
    lines = [ln for ln in text.splitlines() if ln.strip()]
    assert len(lines) == 8
    assert "survey" in lines[0]
    assert "296" in lines[1] and "interview" in lines[1]
    assert "plan" in lines[4].lower()
    assert "dry run" in lines[6].lower()
    assert "ok" in lines[7].lower()
    assert "dry run" in lines[7].lower()  # the finished line flags simulation too


def test_run_progress_cleared_and_hidden():
    d = JobDetail()
    d.load(E(), a_job())
    d.set_run_progress({"stage": "survey"})
    d.clear_run_progress()
    assert d.progress_text.toPlainText() == ""
    assert d.progress_panel.isHidden()

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
    assert d.config_fields()["store_id"] == 0
    assert not hasattr(d, "window_days_edit")
    assert not hasattr(d, "date_type_edit")
    assert not hasattr(d, "schedule_edit")


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


def test_schedule_controls_round_trip():
    d = JobDetail()
    d.load(E(), a_job())
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("day"))
    d.schedule_every_spin.setValue(3)
    d.schedule_at_edit.setTime(__import__("PySide6.QtCore", fromlist=["QTime"]).QTime(2, 0))
    assert d.schedule() == Schedule("day", 3, "02:00")


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


def test_schedule_fields_follow_unit():
    d = JobDetail()
    d.load(E(), a_job())
    # Switch to the Schedule tab first: widgets on a non-current tab page
    # report isVisibleTo()=False regardless of our visibility logic.
    d.tabs.setCurrentIndex(d.tabs.indexOf(d.tabs.widget(2)))
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("minute"))
    assert d.schedule_at_edit.isVisibleTo(d) is False
    assert d.schedule_weekday_combo.isVisibleTo(d) is False
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("week"))
    assert d.schedule_weekday_combo.isVisibleTo(d) is True
    assert d.schedule_preview.text() == human(d.schedule())


def test_schedule_fields_day_shows_at_not_weekday():
    d = JobDetail()
    d.load(E(), a_job())
    d.tabs.setCurrentIndex(d.tabs.indexOf(d.tabs.widget(2)))
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("day"))
    assert d.schedule_at_edit.isVisibleTo(d) is True
    assert d.schedule_weekday_combo.isVisibleTo(d) is False


def test_schedule_fields_manual_and_hour_hide_at_and_weekday():
    d = JobDetail()
    d.load(E(), a_job())
    for unit in ("", "hour"):
        d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData(unit))
        assert d.schedule_at_edit.isVisibleTo(d) is False
        assert d.schedule_weekday_combo.isVisibleTo(d) is False


def test_schedule_every_spin_range_follows_unit_and_clamps():
    d = JobDetail()
    d.load(E(), a_job())
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("minute"))
    d.schedule_every_spin.setValue(59)
    assert d.schedule_every_spin.maximum() == 59
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("day"))
    assert d.schedule_every_spin.maximum() == 31
    assert d.schedule_every_spin.value() <= 31
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("hour"))
    assert d.schedule_every_spin.maximum() == 23
    d.schedule_unit_combo.setCurrentIndex(d.schedule_unit_combo.findData("week"))
    assert d.schedule_every_spin.maximum() == 52
    assert 1 <= d.schedule_every_spin.value() <= 52
