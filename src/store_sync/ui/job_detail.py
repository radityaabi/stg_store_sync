"""Store-detail pane: tabbed job editor (Config · Columns · Schedule · History ·
Conflicts) with a model-bound binding grid and a bottom action rail.

Pure view: it holds a `JobTableModel` for the binding grid plus `_DictTableModel`
instances for history / preflight / conflicts, and emits intent signals. It never
touches the registry or the network — the controller wires the signals in.

Style follows `stg_automation_suite/DESIGN.md`: rounded group frames, dark
console-ish surfaces, colour-coded action-rail buttons.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTime, Signal
from PySide6.QtGui import QTextCursor
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
    QSpinBox,
    QTableView,
    QTabWidget,
    QTextEdit,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from store_sync.core.config import ColumnBinding, Schedule, human
from store_sync.ui.job_table_model import JobTableModel
from store_sync.ui.models import _DictTableModel, _ProposalTableModel
from store_sync.ui.dialogs import (
    SCHEDULE_UNIT_ITEMS,
    WEEKDAY_ITEMS,
    refresh_schedule_ui,
)


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
    run_activated = Signal(int)
    apply_proposals_requested = Signal(list)
    dismiss_proposals_requested = Signal(list)
    pair_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history_ids: list[int] = []
        self._progress_lines: list[str] = []
        self._survey_index: dict = {}
        self._paired_survey_id: str = ""
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
        self.tabs.addTab(self._proposals_tab(), "Proposed updates")
        root.addWidget(self.tabs, stretch=1)
        root.addWidget(self._run_progress_panel())
        root.addWidget(self._action_rail())

    def _run_progress_panel(self) -> QWidget:
        """Live run timeline (console log + status line), hidden until a run."""
        self.progress_panel = QFrame()
        self.progress_panel.setObjectName("ConsolePanel")
        lay = QVBoxLayout(self.progress_panel)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(6)

        self.progress_label = QLabel("run: —")
        self.progress_label.setObjectName("ProgressLabel")
        lay.addWidget(self.progress_label)

        self.progress_text = QTextEdit()
        self.progress_text.setObjectName("ConsoleLog")
        self.progress_text.setReadOnly(True)
        self.progress_text.setFixedHeight(160)
        lay.addWidget(self.progress_text)

        self.progress_panel.hide()
        return self.progress_panel

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

        row4 = QHBoxLayout()
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
        self.pair_button = QPushButton("🔗 Pair with survey XML")
        self.pair_button.setObjectName("btn_warning")
        self.pair_button.clicked.connect(self.pair_requested.emit)
        bar.addWidget(self.add_column_button)
        bar.addWidget(self.remove_column_button)
        bar.addStretch(1)
        bar.addWidget(self.pair_button)
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
        row.addWidget(QLabel("Schedule:"))
        self.schedule_unit_combo = QComboBox()
        for label, value in SCHEDULE_UNIT_ITEMS:
            self.schedule_unit_combo.addItem(label, value)
        row.addWidget(self.schedule_unit_combo)
        row.addWidget(QLabel("Every:"))
        self.schedule_every_spin = QSpinBox()
        self.schedule_every_spin.setRange(1, 59)
        row.addWidget(self.schedule_every_spin)
        self.schedule_at_label = QLabel("At:")
        row.addWidget(self.schedule_at_label)
        self.schedule_at_edit = QTimeEdit()
        self.schedule_at_edit.setTime(QTime(2, 0))
        self.schedule_at_edit.setDisplayFormat("HH:mm")
        row.addWidget(self.schedule_at_edit)
        self.schedule_weekday_label = QLabel("On:")
        row.addWidget(self.schedule_weekday_label)
        self.schedule_weekday_combo = QComboBox()
        for label, value in WEEKDAY_ITEMS:
            self.schedule_weekday_combo.addItem(label, value)
        row.addWidget(self.schedule_weekday_combo)
        lay.addLayout(row)

        self.schedule_preview = QLabel("")
        self.schedule_preview.setObjectName("SchedulePreview")
        lay.addWidget(self.schedule_preview)
        lay.addStretch(1)
        # Preview + per-unit field visibility/spin range (Task 5; spec §4.6).
        self.schedule_unit_combo.currentIndexChanged.connect(
            self._refresh_schedule_fields
        )
        self.schedule_every_spin.valueChanged.connect(self._update_schedule_preview)
        self.schedule_at_edit.timeChanged.connect(self._update_schedule_preview)
        self.schedule_weekday_combo.currentIndexChanged.connect(
            self._update_schedule_preview
        )
        self._refresh_schedule_fields()
        return w

    def _history_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        self.history_model = _DictTableModel(["time", "status", "counts"], self)
        self.history_view = _dict_view(self.history_model)
        self.history_view.doubleClicked.connect(self._on_history_double_click)
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

    def _proposals_tab(self) -> QWidget:
        """Pending update proposals the operator reviews + applies (spec §6)."""
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        self.proposals_model = _ProposalTableModel(self)
        self.proposals_view = QTableView()
        self.proposals_view.setObjectName("ProposalsTable")
        self.proposals_view.setModel(self.proposals_model)
        self.proposals_view.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.proposals_view.setEditTriggers(
            QAbstractItemView.AllEditTriggers
        )
        self.proposals_view.verticalHeader().setVisible(False)
        self.proposals_view.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeToContents
        )
        self.proposals_view.horizontalHeader().setStretchLastSection(True)
        lay.addWidget(self.proposals_view, stretch=1)

        self.proposals_empty_label = QLabel(
            "No pending updates — the store matches the source."
        )
        self.proposals_empty_label.setObjectName("ProposalsEmpty")
        lay.addWidget(self.proposals_empty_label)

        bar = QHBoxLayout()
        self.select_all_button = QPushButton("Select all")
        self.select_all_button.clicked.connect(
            lambda: self.proposals_model.set_all_checked(True)
        )
        self.select_none_button = QPushButton("Select none")
        self.select_none_button.clicked.connect(
            lambda: self.proposals_model.set_all_checked(False)
        )
        self.apply_button = QPushButton("✔ Apply selected")
        self.apply_button.setObjectName("btn_success")
        self.apply_button.clicked.connect(self._on_apply_proposals)
        self.dismiss_button = QPushButton("Dismiss selected")
        self.dismiss_button.setObjectName("btn_danger")
        self.dismiss_button.clicked.connect(self._on_dismiss_proposals)
        bar.addWidget(self.select_all_button)
        bar.addWidget(self.select_none_button)
        bar.addStretch(1)
        bar.addWidget(self.apply_button)
        bar.addWidget(self.dismiss_button)
        lay.addLayout(bar)

        self._update_proposals_empty_state()
        return w

    def _update_proposals_empty_state(self) -> None:
        empty = self.proposals_model.rowCount() == 0
        self.proposals_empty_label.setVisible(empty)
        self.proposals_view.setVisible(not empty)

    def _on_apply_proposals(self) -> None:
        ids = self.proposals_model.checked_ids()
        if ids:
            self.apply_proposals_requested.emit(ids)

    def _on_dismiss_proposals(self) -> None:
        ids = self.proposals_model.checked_ids()
        if ids:
            self.dismiss_proposals_requested.emit(ids)

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
        self.dry_run_check.setChecked(bool(job.write.dry_run))

        self._survey_index = dict(job.source.survey_index)
        self._paired_survey_id = job.source.paired_survey_id
        self.binding_model.set_columns(job.binding.columns)

        self.set_proposals([])

        self.enabled_check.setChecked(bool(getattr(entry, "enabled", False)))
        self._set_schedule(getattr(entry, "schedule", None) or Schedule())
        self._update_schedule_preview()

    def bindings(self) -> list[ColumnBinding]:
        return self.binding_model.columns()

    def set_pull_terms(self, mapping: dict[str, str]) -> None:
        self.binding_model.set_pull_terms(mapping)

    def set_pairing(self, survey_id, index, terms) -> None:
        """Store the paired survey's index + id, and fill the pull-term column."""
        self._survey_index = dict(index)
        self._paired_survey_id = survey_id
        self.binding_model.set_pull_terms(terms)

    def set_history(self, rows: list[dict]) -> None:
        self.history_model.set_rows(rows)
        self._history_ids = [r.get("id") for r in rows]

    def set_preflight(self, rows: list[dict]) -> None:
        self.preflight_model.set_rows(rows)

    def set_conflicts(self, rows: list[dict]) -> None:
        self.conflicts_model.set_rows(rows)

    def set_proposals(self, rows: list[dict]) -> None:
        """Fill the Proposed-updates tab with pending proposals."""
        self.proposals_model.set_rows(rows)
        self._update_proposals_empty_state()

    # -- live run timeline -------------------------------------------------
    def set_run_progress(self, event: dict) -> None:
        """Append one pipeline stage to the run console (see `sync.run_sync`).

        `event` is `{"stage": ..., ...}`. The first event shows the panel;
        `stage == "done"` leaves it up so the operator can read the outcome.
        """
        stage = event.get("stage", "")
        if stage == "start":
            self._progress_lines = []
            self.progress_text.setPlainText("")
            job = event.get("job", "")
            dry = " (dry run)" if event.get("dry_run") else ""
            self.progress_label.setText(f"run: {job}{dry}")
        line = _progress_line(event)
        if line:
            self._progress_lines.append(line)
            self.progress_text.setPlainText("\n".join(self._progress_lines))
            # Keep the newest stage visible (setPlainText resets the cursor).
            self.progress_text.moveCursor(QTextCursor.End)
        self.progress_panel.show()

    def clear_run_progress(self) -> None:
        self._progress_lines = []
        self.progress_text.setPlainText("")
        self.progress_panel.hide()

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
            "dry_run": self.dry_run_check.isChecked(),
            "survey_index": dict(self._survey_index),
            "paired_survey_id": self._paired_survey_id,
        }

    # -- slots -------------------------------------------------------------
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

    def _update_schedule_preview(self, *args) -> None:
        self.schedule_preview.setText(human(self.schedule()))

    def _refresh_schedule_fields(self, *args) -> None:
        refresh_schedule_ui(
            unit=self.schedule_unit_combo.currentData() or "",
            every_spin=self.schedule_every_spin,
            at_widgets=[self.schedule_at_label, self.schedule_at_edit],
            weekday_widgets=[self.schedule_weekday_label, self.schedule_weekday_combo],
        )
        self._update_schedule_preview()

    def _on_add_column(self) -> None:
        self.binding_model.add_row()

    def _on_remove_column(self) -> None:
        rows = self.binding_view.selectionModel().selectedRows()
        row = rows[0].row() if rows else self.binding_view.currentIndex().row()
        if row >= 0:
            self.binding_model.remove_row(row)

    def _on_history_double_click(self, index) -> None:
        row = index.row()
        if 0 <= row < len(self._history_ids):
            run_id = self._history_ids[row]
            if run_id is not None:
                self.run_activated.emit(int(run_id))


_STAGE_TEXT = {
    "ids": "listing interviews",
    "pull": "pulling interview data",
    "store": "reading store",
    "preflight": "checking readiness",
    "plan": "planning changes",
    "apply": "applying changes",
    "applied": "changes applied",
    "done": "finished",
}


def _progress_line(event: dict) -> str:
    """One human-readable timeline line for a `run_sync` progress event."""
    stage = event.get("stage", "")
    label = _STAGE_TEXT.get(stage, stage)
    if stage == "ids":
        return f"· {label}: {event.get('total', 0)} interview(s)"
    if stage == "pull":
        return f"· {label}: {event.get('total', 0)} record(s)"
    if stage == "store":
        return f"· {label}: {event.get('total', 0)} row(s)"
    if stage == "plan":
        return f"· {label}: {_fmt_counts(event.get('counts')) or 'no changes'}"
    if stage == "apply":
        dry = " (dry run)" if event.get("dry_run") else ""
        return f"· {label}: {event.get('planned', 0)} action(s){dry}"
    if stage == "applied":
        dry = " (dry run)" if event.get("dry_run") else ""
        return f"· {label}: {_fmt_counts(event.get('counts')) or 'nothing'}{dry}"
    if stage == "done":
        counts = _fmt_counts(event.get("counts"))
        dry = " (dry run)" if event.get("dry_run") else ""
        tail = f" ({counts})" if counts else ""
        return f"· {label}: {event.get('status', '?')}{dry}{tail}"
    if stage == "start":
        return "· started"
    if stage == "preflight":
        errs = event.get("errors") or []
        if errs:
            return f"· {label}: {len(errs)} problem(s)"
        return f"· {label}: ok"
    return f"· {label}"


def _fmt_counts(counts) -> str:
    if not counts:
        return ""
    return ", ".join(f"{k} {v}" for k, v in counts.items())


def _dict_view(model: _DictTableModel) -> QTableView:
    t = QTableView()
    t.setModel(model)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return t
