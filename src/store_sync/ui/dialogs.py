"""New/Edit job dialog: register or edit a store job's config.

Rebuilds a `JobConfig` (plus the `JobEntry`-only toggles `name`/`schedule`/
`enabled`) from form widgets. Pure view + form state — it never touches the
registry. In edit mode pass `entry=` and `job=` to pre-fill.

Style follows the suite's DESIGN tokens: rounded group frames, colour-coded
OK/Cancel buttons.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHeaderView,
    QLineEdit,
    QPushButton,
    QTableView,
    QVBoxLayout,
)

from store_sync.core.config import (
    BindingConfig,
    ColumnBinding,
    JobConfig,
    SourceConfig,
    StoreConfig,
    WriteConfig,
)
from store_sync.ui.job_table_model import JobTableModel
from store_sync.ui.models import _DictTableModel

DELETE_POLICIES = [
    ("Full history", "full_history"),
    ("Tracked only", "tracked_only"),
    ("Manual", "manual"),
]


class JobDialog(QDialog):
    """Modal form for creating or editing one store job."""

    def __init__(self, parent=None, *, entry=None, job=None):
        super().__init__(parent)
        self.setWindowTitle("Edit job" if entry is not None else "New job")
        self._build_ui()
        if entry is not None or job is not None:
            self.load(entry, job)

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        form = QFormLayout()
        self.name_edit = QLineEdit()
        form.addRow("Name", self.name_edit)
        self.store_id_edit = QLineEdit()
        form.addRow("Store ID", self.store_id_edit)
        self.id_column_edit = QLineEdit()
        form.addRow("Id column", self.id_column_edit)
        self.survey_id_edit = QLineEdit()
        form.addRow("Survey ID", self.survey_id_edit)
        self.match_var_edit = QLineEdit()
        form.addRow("Match var", self.match_var_edit)
        self.match_store_edit = QLineEdit()
        form.addRow("Match store col", self.match_store_edit)
        self.window_days_edit = QLineEdit("0")
        form.addRow("Window days", self.window_days_edit)
        self.date_type_edit = QLineEdit("Upload")
        form.addRow("Date type", self.date_type_edit)
        self.delete_policy_combo = QComboBox()
        for label, value in DELETE_POLICIES:
            self.delete_policy_combo.addItem(label, value)
        form.addRow("Delete policy", self.delete_policy_combo)
        self.dry_run_check = QCheckBox("Dry run")
        self.dry_run_check.setChecked(True)
        form.addRow("", self.dry_run_check)
        self.enabled_check = QCheckBox("Enabled")
        form.addRow("", self.enabled_check)
        self.schedule_edit = QLineEdit()
        form.addRow("Schedule (cron)", self.schedule_edit)
        root.addLayout(form)

        self.binding_model = JobTableModel(self)
        self.binding_view = QTableView()
        self.binding_view.setModel(self.binding_model)
        self.binding_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.binding_view.setSelectionMode(QAbstractItemView.SingleSelection)
        self.binding_view.verticalHeader().setVisible(False)
        self.binding_view.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.binding_view.setAlternatingRowColors(True)
        root.addWidget(self.binding_view)

        buttons = QDialogButtonBox()
        self.ok_button = QPushButton("💾 Save")
        self.ok_button.setObjectName("btn_primary")
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons.addButton(self.ok_button, QDialogButtonBox.AcceptRole)
        buttons.addButton(self.cancel_button, QDialogButtonBox.RejectRole)
        root.addWidget(buttons)

    # -- app API -----------------------------------------------------------
    def load(self, entry, job) -> None:
        """Pre-fill from an existing `JobEntry` (+ its `JobConfig`)."""
        if entry is not None:
            self.name_edit.setText(str(getattr(entry, "name", "") or ""))
            self.schedule_edit.setText(str(getattr(entry, "schedule", "") or ""))
            self.enabled_check.setChecked(bool(getattr(entry, "enabled", False)))
        if job is not None:
            self.name_edit.setText(job.job)
            self.store_id_edit.setText(str(job.store.id))
            self.id_column_edit.setText(job.store.id_column)
            self.survey_id_edit.setText(job.source.survey_id)
            match = job.binding.match
            self.match_var_edit.setText(match.source_var if match else "")
            self.match_store_edit.setText(
                match.store_col if match else ""
            )
            self.window_days_edit.setText(str(job.source.window_days))
            self.date_type_edit.setText(job.source.date_type)
            idx = self.delete_policy_combo.findData(job.write.delete_policy)
            if idx >= 0:
                self.delete_policy_combo.setCurrentIndex(idx)
            self.dry_run_check.setChecked(bool(job.write.dry_run))
            self.binding_model.set_columns(job.binding.columns)

    def job(self) -> JobConfig:
        match_var = self.match_var_edit.text().strip()
        match_store = self.match_store_edit.text().strip() or match_var
        match = ColumnBinding(match_var, match_store) if match_var else None
        return JobConfig(
            job=self.name_edit.text().strip(),
            store=StoreConfig(
                id=_int(self.store_id_edit.text()),
                id_column=self.id_column_edit.text().strip(),
            ),
            source=SourceConfig(
                survey_id=self.survey_id_edit.text().strip(),
                window_days=_int(self.window_days_edit.text()),
                date_type=self.date_type_edit.text().strip(),
            ),
            binding=BindingConfig(
                match=match,
                columns=self.binding_model.columns(),
            ),
            write=WriteConfig(
                dry_run=self.dry_run_check.isChecked(),
                delete_policy=self.delete_policy_combo.currentData(),
            ),
        )

    def entry_fields(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "schedule": self.schedule_edit.text().strip(),
            "enabled": self.enabled_check.isChecked(),
        }

    @property
    def schedule(self) -> str:
        return self.schedule_edit.text().strip()

    @property
    def enabled(self) -> bool:
        return self.enabled_check.isChecked()


def _int(text: str) -> int:
    try:
        return int(text.strip())
    except (ValueError, AttributeError):
        return 0


class ScheduleOverview(QDialog):
    """Read-only overview of the registry's schedules.

    A minimal, honest surface for the dashboard's ⏰ Schedules button: it lists
    every job's name, cron expression and enabled flag — no scheduling engine.
    `rows()` exposes the same data for tests; `_DictTableModel` keeps the table
    consistent with the rest of the app's read-only grid styling.
    """

    _FIELDS = ["name", "cron", "enabled"]

    def __init__(self, rows: list[dict], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Enabled schedules")
        self.resize(520, 360)
        self._rows = list(rows)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)
        self.model = _DictTableModel(self._FIELDS, self)
        self.model.set_rows(
            [
                {
                    "name": r.get("name", ""),
                    "cron": r.get("cron", "") or "(manual only)",
                    "enabled": "yes" if r.get("enabled") else "no",
                }
                for r in self._rows
            ]
        )
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        lay.addWidget(self.table, stretch=1)

        buttons = QDialogButtonBox()
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        buttons.addButton(self.close_button, QDialogButtonBox.AcceptRole)
        lay.addWidget(buttons)

    def rows(self) -> list[dict]:
        """The raw schedule rows passed in (name · cron · enabled)."""
        return list(self._rows)
