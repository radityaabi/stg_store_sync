"""Store-detail pane: tabbed job editor (Config · Columns · Schedule · History ·
Conflicts) with a model-bound binding grid and a bottom action rail.

Pure view: it holds a `JobTableModel` for the binding grid plus `_DictTableModel`
instances for history / preflight / conflicts, and emits intent signals. It never
touches the registry or the network — the controller wires the signals in.

Style follows `stg_automation_suite/DESIGN.md`: rounded group frames, dark
console-ish surfaces, colour-coded action-rail buttons.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from store_sync.core.config import ColumnBinding
from store_sync.ui.job_table_model import JobTableModel
from store_sync.ui.models import _DictTableModel

DELETE_POLICIES = [
    ("Full history", "full_history"),
    ("Tracked only", "tracked_only"),
    ("Manual", "manual"),
]


class JobDetail(QWidget):
    """Tabbed store detail: edit one job's config, columns and schedule."""

    back = Signal()
    save_requested = Signal()
    preflight_requested = Signal()
    approve_requested = Signal()
    run_requested = Signal()
    stop_requested = Signal()
    import_requested = Signal()
    export_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Preflight results have no dedicated tab (the controller surfaces them
        # inline); keep the model so `set_preflight` has somewhere to land.
        self.preflight_model = _DictTableModel(["column", "status", "detail"], self)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("WorkspaceTabs")
        self.tabs.addTab(self._config_tab(), "Config")
        self.tabs.addTab(self._columns_tab(), "Columns")
        self.tabs.addTab(self._schedule_tab(), "Schedule")
        self.tabs.addTab(self._history_tab(), "History")
        self.tabs.addTab(self._conflicts_tab(), "Conflicts")
        root.addWidget(self.tabs, stretch=1)
        root.addWidget(self._action_rail())

    def _config_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        row = QHBoxLayout()
        row.addWidget(QLabel("Store ID:"))
        self.store_id_edit = QLineEdit()
        row.addWidget(self.store_id_edit)
        row.addWidget(QLabel("Id column:"))
        self.id_column_edit = QLineEdit()
        row.addWidget(self.id_column_edit)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Survey ID:"))
        self.survey_id_edit = QLineEdit()
        row2.addWidget(self.survey_id_edit)
        row2.addWidget(QLabel("Match var:"))
        self.match_var_edit = QLineEdit()
        row2.addWidget(self.match_var_edit)
        row2.addWidget(QLabel("Match store col:"))
        self.match_store_edit = QLineEdit()
        row2.addWidget(self.match_store_edit)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Window days:"))
        self.window_days_edit = QLineEdit()
        row3.addWidget(self.window_days_edit)
        row3.addWidget(QLabel("Date type:"))
        self.date_type_edit = QLineEdit()
        row3.addWidget(self.date_type_edit)
        lay.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Delete policy:"))
        self.delete_policy_combo = QComboBox()
        for label, value in DELETE_POLICIES:
            self.delete_policy_combo.addItem(label, value)
        row4.addWidget(self.delete_policy_combo)
        self.dry_run_check = QCheckBox("Dry run")
        self.dry_run_check.setChecked(True)
        row4.addWidget(self.dry_run_check)
        lay.addLayout(row4)
        lay.addStretch(1)
        return w

    def _columns_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        self.binding_model = JobTableModel(self)
        self.binding_view = QTableView()
        self.binding_view.setObjectName("BindingTable")
        self.binding_view.setModel(self.binding_model)
        self.binding_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.binding_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.binding_view.setAlternatingRowColors(True)
        self.binding_view.verticalHeader().setVisible(False)
        self.binding_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.binding_view, stretch=1)

        bar = QHBoxLayout()
        self.add_column_button = QPushButton("➕ Add column")
        self.add_column_button.setObjectName("btn_primary")
        self.add_column_button.clicked.connect(self._on_add_column)
        self.remove_column_button = QPushButton("🗑 Remove selected")
        self.remove_column_button.setObjectName("btn_danger")
        self.remove_column_button.clicked.connect(self._on_remove_column)
        self.import_button = QPushButton("📥 Import mapping")
        self.import_button.clicked.connect(self.import_requested.emit)
        self.export_button = QPushButton("📤 Export mapping")
        self.export_button.clicked.connect(self.export_requested.emit)
        bar.addWidget(self.add_column_button)
        bar.addWidget(self.remove_column_button)
        bar.addStretch(1)
        bar.addWidget(self.import_button)
        bar.addWidget(self.export_button)
        lay.addLayout(bar)
        return w

    def _schedule_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        self.enabled_check = QCheckBox("Enabled")
        lay.addWidget(self.enabled_check)

        row = QHBoxLayout()
        row.addWidget(QLabel("Cron:"))
        self.schedule_edit = QLineEdit()
        row.addWidget(self.schedule_edit)
        lay.addLayout(row)

        self.schedule_preview = QLabel("")
        self.schedule_preview.setObjectName("SchedulePreview")
        lay.addWidget(self.schedule_preview)
        lay.addStretch(1)
        self.schedule_edit.textChanged.connect(self._update_schedule_preview)
        return w

    def _history_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        self.history_model = _DictTableModel(["time", "status", "counts"], self)
        self.history_view = _dict_view(self.history_model)
        lay.addWidget(self.history_view)
        return w

    def _conflicts_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        self.conflicts_model = _DictTableModel(["match", "reason", "side"], self)
        self.conflicts_view = _dict_view(self.conflicts_model)
        lay.addWidget(self.conflicts_view)
        return w

    def _action_rail(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("ActionRail")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(8)

        self.back_button = QPushButton("← Back")
        self.back_button.clicked.connect(self.back.emit)
        self.preflight_button = QPushButton("✔ Run preflight")
        self.preflight_button.setObjectName("btn_warning")
        self.preflight_button.clicked.connect(self.preflight_requested.emit)
        self.approve_button = QPushButton("▶ Approve binding")
        self.approve_button.setObjectName("btn_success")
        self.approve_button.clicked.connect(self.approve)
        self.save_button = QPushButton("💾 Save")
        self.save_button.setObjectName("btn_primary")
        self.save_button.clicked.connect(self.save_requested.emit)
        self.run_button = QPushButton("▶ Run now")
        self.run_button.setObjectName("btn_primary")
        self.run_button.clicked.connect(self.run_requested.emit)
        self.stop_button = QPushButton("⏹ Stop")
        self.stop_button.setObjectName("btn_danger")
        self.stop_button.clicked.connect(self.stop_requested.emit)

        lay.addWidget(self.back_button)
        lay.addStretch(1)
        lay.addWidget(self.preflight_button)
        lay.addWidget(self.approve_button)
        lay.addWidget(self.save_button)
        lay.addWidget(self.run_button)
        lay.addWidget(self.stop_button)
        return frame

    # -- app API -----------------------------------------------------------
    def load(self, entry, job) -> None:
        """Fill every tab from a `JobEntry` + its `JobConfig`."""
        self.store_id_edit.setText(str(job.store.id))
        self.id_column_edit.setText(job.store.id_column)
        self.survey_id_edit.setText(job.source.survey_id)
        match = job.binding.match
        self.match_var_edit.setText(match.source_var if match else "")
        self.match_store_edit.setText(
            (match.store_col if match else "") or (match.source_var if match else "")
        )
        self.window_days_edit.setText(str(job.source.window_days))
        self.date_type_edit.setText(job.source.date_type)
        idx = self.delete_policy_combo.findData(job.write.delete_policy)
        if idx >= 0:
            self.delete_policy_combo.setCurrentIndex(idx)
        self.dry_run_check.setChecked(bool(job.write.dry_run))

        self.binding_model.set_pull_terms({})
        self.binding_model.set_columns(job.binding.columns)

        self.enabled_check.setChecked(bool(getattr(entry, "enabled", False)))
        self.schedule_edit.setText(str(getattr(entry, "schedule", "") or ""))
        self._update_schedule_preview()

    def bindings(self) -> list[ColumnBinding]:
        return self.binding_model.columns()

    def set_pull_terms(self, mapping: dict[str, str]) -> None:
        self.binding_model.set_pull_terms(mapping)

    def set_history(self, rows: list[dict]) -> None:
        self.history_model.set_rows(rows)

    def set_preflight(self, rows: list[dict]) -> None:
        self.preflight_model.set_rows(rows)

    def set_conflicts(self, rows: list[dict]) -> None:
        self.conflicts_model.set_rows(rows)

    def approve(self) -> None:
        self.enabled_check.setChecked(True)
        self.approve_requested.emit()

    def config_fields(self) -> dict:
        return {
            "store_id": int(self.store_id_edit.text().strip() or 0),
            "id_column": self.id_column_edit.text().strip(),
            "survey_id": self.survey_id_edit.text().strip(),
            "match_source_var": self.match_var_edit.text().strip(),
            "match_store_col": self.match_store_edit.text().strip(),
            "window_days": int(self.window_days_edit.text().strip() or 0),
            "date_type": self.date_type_edit.text().strip(),
            "delete_policy": self.delete_policy_combo.currentData(),
            "dry_run": self.dry_run_check.isChecked(),
        }

    # -- slots -------------------------------------------------------------
    def _update_schedule_preview(self) -> None:
        text = self.schedule_edit.text().strip()
        self.schedule_preview.setText(f"Runs: {text}" if text else "Runs: manual only")

    def _on_add_column(self) -> None:
        self.binding_model.add_row()

    def _on_remove_column(self) -> None:
        rows = self.binding_view.selectionModel().selectedRows()
        row = rows[0].row() if rows else self.binding_view.currentIndex().row()
        if row >= 0:
            self.binding_model.remove_row(row)


def _dict_view(model: _DictTableModel) -> QTableView:
    t = QTableView()
    t.setModel(model)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return t
