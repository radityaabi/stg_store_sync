"""New/Edit job dialog: register or edit a store job's config.

Rebuilds a `JobConfig` (plus the `JobEntry`-only toggles `name`/`schedule`/
`enabled`) from form widgets. Pure view + form state — it never touches the
registry. In edit mode pass `entry=` and `job=` to pre-fill.

Style follows the suite's DESIGN tokens: rounded group frames, colour-coded
OK/Cancel buttons.
"""
from __future__ import annotations

from PySide6.QtCore import QTime
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
    QSpinBox,
    QTableView,
    QTimeEdit,
    QVBoxLayout,
)

from store_sync.core.config import (
    BindingConfig,
    ColumnBinding,
    JobConfig,
    Schedule,
    SourceConfig,
    StoreConfig,
    WriteConfig,
    human,
)
from store_sync.ui.job_table_model import JobTableModel
from store_sync.ui.models import _DictTableModel

SCHEDULE_UNIT_ITEMS = [
    ("Manual", ""),
    ("Every minute", "minute"),
    ("Every hour", "hour"),
    ("Every day", "day"),
    ("Every week", "week"),
]

WEEKDAY_ITEMS = [
    ("Monday", 0),
    ("Tuesday", 1),
    ("Wednesday", 2),
    ("Thursday", 3),
    ("Friday", 4),
    ("Saturday", 5),
    ("Sunday", 6),
]

SCHEDULE_EVERY_MAX = {
    "": 59,
    "minute": 59,
    "hour": 23,
    "day": 31,
    "week": 52,
}


def refresh_schedule_ui(
    *,
    unit: str,
    every_spin: QSpinBox,
    at_widgets: list,
    weekday_widgets: list,
) -> None:
    """Per-unit schedule control state (spec §4.1/§4.6).

    `at` shows only for day/week, weekday only for week; the *every N* spin
    range follows the unit's max and Qt clamps the value into it.
    """
    show_at = unit in ("day", "week")
    show_weekday = unit == "week"
    for w in at_widgets:
        w.setVisible(show_at)
    for w in weekday_widgets:
        w.setVisible(show_weekday)
    maximum = SCHEDULE_EVERY_MAX.get(unit, 59)
    every_spin.setMaximum(maximum)
    if every_spin.value() > maximum:
        every_spin.setValue(maximum)


class JobDialog(QDialog):
    """Modal form for creating or editing one store job."""

    def __init__(self, parent=None, *, entry=None, job=None):
        super().__init__(parent)
        self.setWindowTitle("Edit job" if entry is not None else "New job")
        self.setMinimumSize(560, 480)
        self.resize(640, 560)
        self._build_ui()
        if entry is not None or job is not None:
            self.load(entry, job)

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        form = QFormLayout()
        self._form = form
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
        self.dry_run_check = QCheckBox("Dry run")
        self.dry_run_check.setChecked(True)
        form.addRow("", self.dry_run_check)
        self.days_back_check = QCheckBox("Fetch only the last")
        self.days_back_check.setChecked(False)
        self.days_back_check.toggled.connect(self._update_days_back_enabled)
        self.days_back_spin = QSpinBox()
        self.days_back_spin.setRange(1, 90)
        self.days_back_spin.setValue(7)
        self.days_back_spin.setSuffix(" day(s)")
        self.days_back_spin.setToolTip(
            "Number of days back to read, counted from today (upload time)."
        )
        form.addRow(self.days_back_check, self.days_back_spin)
        self._update_days_back_enabled(False)
        self.enabled_check = QCheckBox("Enabled")
        form.addRow("", self.enabled_check)
        self.schedule_unit_combo = QComboBox()
        for label, value in SCHEDULE_UNIT_ITEMS:
            self.schedule_unit_combo.addItem(label, value)
        self.schedule_unit_combo.currentIndexChanged.connect(
            self._refresh_schedule_fields
        )
        form.addRow("Schedule", self.schedule_unit_combo)
        self.schedule_every_spin = QSpinBox()
        self.schedule_every_spin.setRange(1, 59)
        form.addRow("Every N", self.schedule_every_spin)
        self.schedule_at_edit = QTimeEdit()
        self.schedule_at_edit.setTime(QTime(2, 0))
        self.schedule_at_edit.setDisplayFormat("HH:mm")
        form.addRow("At", self.schedule_at_edit)
        self.schedule_weekday_combo = QComboBox()
        for label, value in WEEKDAY_ITEMS:
            self.schedule_weekday_combo.addItem(label, value)
        form.addRow("Weekday", self.schedule_weekday_combo)
        root.addLayout(form)
        self._refresh_schedule_fields()

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
            self._set_schedule(getattr(entry, "schedule", None) or Schedule())
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
            self.dry_run_check.setChecked(bool(job.write.dry_run))
            self.days_back_check.setChecked(bool(job.source.days_back))
            if job.source.days_back:
                self.days_back_spin.setValue(int(job.source.days_back))
            self._update_days_back_enabled(self.days_back_check.isChecked())
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
                days_back=self.days_back(),
            ),
            binding=BindingConfig(
                match=match,
                columns=self.binding_model.columns(),
            ),
            write=WriteConfig(
                dry_run=self.dry_run_check.isChecked(),
            ),
        )

    def schedule(self) -> Schedule:
        """The structured schedule built from the four widgets."""
        return Schedule(
            unit=self.schedule_unit_combo.currentData() or "",
            every=self.schedule_every_spin.value(),
            at=self.schedule_at_edit.time().toString("HH:mm"),
            weekday=self.schedule_weekday_combo.currentData() or 0,
        )

    def _set_schedule(self, schedule: Schedule) -> None:
        idx = self.schedule_unit_combo.findData(schedule.unit)
        if idx >= 0:
            self.schedule_unit_combo.setCurrentIndex(idx)
        self.schedule_every_spin.setValue(int(schedule.every))
        at = QTime.fromString(schedule.at, "HH:mm") if schedule.at else None
        self.schedule_at_edit.setTime(at if at and at.isValid() else QTime(2, 0))
        widx = self.schedule_weekday_combo.findData(int(schedule.weekday))
        if widx >= 0:
            self.schedule_weekday_combo.setCurrentIndex(widx)

    def _update_days_back_enabled(self, on: bool) -> None:
        """The day count only means something when the window is on."""
        self.days_back_spin.setEnabled(bool(on))

    def days_back(self) -> int:
        """The configured fetch window: `0` when the checkbox is off."""
        return self.days_back_spin.value() if self.days_back_check.isChecked() else 0

    def _refresh_schedule_fields(self, *args) -> None:
        refresh_schedule_ui(
            unit=self.schedule_unit_combo.currentData() or "",
            every_spin=self.schedule_every_spin,
            at_widgets=[self.schedule_at_edit],
            weekday_widgets=[self.schedule_weekday_combo],
        )
        # QFormLayout does NOT auto-hide row labels — toggle them ourselves.
        for w, visible in (
            (self.schedule_at_edit, self.schedule_at_edit.isVisibleTo(self)),
            (self.schedule_weekday_combo,
             self.schedule_weekday_combo.isVisibleTo(self)),
        ):
            label = self._form.labelForField(w)
            if label is not None:
                label.setVisible(visible)

    def entry_fields(self) -> dict:
        return {
            "name": self.name_edit.text().strip(),
            "schedule": self.schedule(),
            "enabled": self.enabled_check.isChecked(),
        }

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
    every job's name, human schedule label and enabled flag — no scheduling
    engine. `rows()` exposes the same data for tests; `_DictTableModel` keeps
    the table consistent with the rest of the app's read-only grid styling.
    """

    _FIELDS = ["name", "schedule", "enabled"]

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
                    "schedule": self._label(r.get("schedule")),
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
        """The raw schedule rows passed in (name · schedule · enabled)."""
        return list(self._rows)

    @staticmethod
    def _label(schedule) -> str:
        """`human(schedule)`; a non-`Schedule` value renders manual only."""
        return human(schedule) if isinstance(schedule, Schedule) else "manual only"
