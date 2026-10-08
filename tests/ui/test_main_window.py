"""Tests for store_sync.ui.main_window — construct + wiring (offscreen Qt).

Only construction, model wiring, and pure slots are tested; no live network.
"""
from __future__ import annotations

import os

import pytest

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
from store_sync.ui.main_window import JobTableModel, MainWindow  # noqa: E402


@pytest.fixture(scope="session")
def app():
    return QApplication.instance() or QApplication([])


def job():
    return JobConfig(
        job="link1",
        store=StoreConfig(id=17620, id_column="No"),
        source=SourceConfig(survey_id="SURV"),
        binding=BindingConfig(
            match=ColumnBinding("SbjNum", "SbjNum"),
            columns=[ColumnBinding("T_QPM1_1", "I7_1_2"), ColumnBinding("Q32", "Q32")],
        ),
        write=WriteConfig(dry_run=True),
    )


class TestModel:
    def test_set_job_populates_rows(self, app):
        m = JobTableModel()
        m.set_columns(job().binding.columns)
        assert m.rowCount() == 2
        assert m.columnCount() == 4  # store col, source var, pull term, kind

    def test_pull_term_column_shows_blank_when_unknown(self, app):
        m = JobTableModel()
        m.set_columns(job().binding.columns)
        # no index yet -> unresolved -> blank
        assert m.data(m.index(0, 2)) in ("", None)

    def test_pull_term_set_from_index(self, app):
        m = JobTableModel()
        m.set_columns([ColumnBinding("T_QPM1_1", "I7_1_2")])
        m.set_pull_terms({"T_QPM1_1": "QPM1"})
        assert m.data(m.index(0, 2)) == "QPM1"

    def test_columns_returns_bindings(self, app):
        m = JobTableModel()
        m.set_columns(job().binding.columns)
        cols = m.columns()
        assert cols[0].store_col == "I7_1_2"
        assert cols[0].source_var == "T_QPM1_1"


class TestWindow:
    def test_constructs(self, app):
        w = MainWindow()
        assert w.windowTitle()
        w.close()

    def test_load_job_populates(self, app):
        w = MainWindow()
        w.load_job(job())
        assert w.job_label.text() == "link1"
        assert w.columns_model.rowCount() == 2
        w.close()

    def test_dry_run_toggle_default_on(self, app):
        w = MainWindow()
        w.load_job(job())
        assert w.dry_run_check.isChecked() is True
        w.close()

    def test_delete_policy_selector_values(self, app):
        w = MainWindow()
        values = [w.delete_policy_combo.itemData(i) for i in range(w.delete_policy_combo.count())]
        assert set(values) == {"full_history", "tracked_only", "manual"}
        w.close()

    def test_sync_disabled_until_binding_approved(self, app):
        w = MainWindow()
        w.load_job(job())
        assert w.sync_button.isEnabled() is False
        w.close()

    def test_approving_binding_enables_sync(self, app):
        w = MainWindow()
        w.load_job(job())
        w.mark_binding_approved()
        assert w.sync_button.isEnabled() is True
        w.close()

    def test_set_preflight_rows(self, app):
        w = MainWindow()
        w.load_job(job())
        w.set_preflight_rows([
            {"store_col": "I7_1_2", "source_var": "T_QPM1_1", "status": "ok"},
            {"store_col": "I8a_1", "source_var": "T_QPM1_3", "status": "format_differs"},
        ])
        assert w.preflight_model.rowCount() == 2
        w.close()

    def test_append_log(self, app):
        w = MainWindow()
        w.append_log("hello")
        assert "hello" in w.log_view.toPlainText()
        w.close()

    def test_set_conflicts(self, app):
        w = MainWindow()
        w.set_conflicts([{"match_value": "101", "reason": "dup"}])
        assert w.conflicts_model.rowCount() == 1
        w.close()

    def test_set_status(self, app):
        w = MainWindow()
        w.set_status("running", "#fbbf24")
        assert "running" in w.status_label.text()
        w.close()

    def test_field_accessors(self, app):
        w = MainWindow()
        w.load_job(job())
        assert w.store_fields().id == 17620
        assert w.store_fields().id_column == "No"
        assert w.source_fields().survey_id == "SURV"
        assert w.write_fields().dry_run is True
        w.close()
