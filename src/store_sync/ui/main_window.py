"""Main window (PySide6): job config, binding editor, preflight, sync, log.

The window is intentionally thin: all logic lives in `core/`. It exposes small
methods (`load_job`, `mark_binding_approved`, `set_preflight_rows`,
`set_conflicts`, `append_log`) so the wiring is unit-testable with an offscreen
QApplication.

Style follows `stg_automation_suite/DESIGN.md`: gradient header, tabbed
workspace, dark console log, bottom action rail.
"""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor
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
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from store_sync.core.config import ColumnBinding, JobConfig, WriteConfig

ICON = "\u25cf"  # status dot


class JobTableModel(QAbstractTableModel):
    """Editable binding table: store column · source variable · pull term · kind.

    The pull term column is **read-only** — it is derived, not entered (§7.3).
    """

    HEADERS = ["Store column", "Source variable", "Pull term (derived)", "Kind"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns: list[ColumnBinding] = []
        self._pull_terms: dict[str, str] = {}

    # -- Qt API ------------------------------------------------------------
    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._columns)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        col = self._columns[index.row()]
        c = index.column()
        if role in (Qt.DisplayRole, Qt.EditRole):
            if c == 0:
                return col.store_col
            if c == 1:
                return col.source_var
            if c == 2:
                return self._pull_terms.get(col.source_var, "")
            if c == 3:
                return col.kind
        if role == Qt.ForegroundRole and c == 2:
            return QColor("#64748b")  # muted: derived/read-only
        return None

    def flags(self, index):
        base = super().flags(index)
        if index.column() == 2:
            return base & ~Qt.ItemIsEditable
        return base | Qt.ItemIsEditable

    def setData(self, index, value, role=Qt.EditRole):
        if role != Qt.EditRole or not index.isValid():
            return False
        col = self._columns[index.row()]
        c = index.column()
        value = "" if value is None else str(value)
        if c == 0:
            col.store_col = value
        elif c == 1:
            col.source_var = value
        elif c == 3:
            col.kind = value or "text"
        else:
            return False
        self.dataChanged.emit(index, index)
        return True

    # -- app API -----------------------------------------------------------
    def set_columns(self, columns: list[ColumnBinding]) -> None:
        self.beginResetModel()
        self._columns = list(columns)
        self.endResetModel()

    def set_pull_terms(self, mapping: dict[str, str]) -> None:
        self._pull_terms = dict(mapping)
        if self._columns:
            self.dataChanged.emit(
                self.index(0, 2), self.index(len(self._columns) - 1, 2)
            )

    def columns(self) -> list[ColumnBinding]:
        return list(self._columns)

    def add_row(self, binding: ColumnBinding | None = None) -> None:
        self.beginInsertRows(QModelIndex(), len(self._columns), len(self._columns))
        self._columns.append(binding or ColumnBinding("", "", "text"))
        self.endInsertRows()

    def remove_row(self, row: int) -> None:
        if 0 <= row < len(self._columns):
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._columns[row]
            self.endRemoveRows()


class MainWindow(QWidget):
    sync_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("STG Store Sync")
        self.resize(1000, 720)
        self._job: JobConfig | None = None
        self._binding_approved = False
        self._build_ui()

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._header())
        root.addWidget(self._workspace(), stretch=1)
        root.addWidget(self._action_rail())

    def _header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("GradientHeader")
        frame.setStyleSheet(
            "#GradientHeader { background: qlineargradient("
            "x1:0, y1:0, x2:1, y2:1, stop:0 #667eea, stop:1 #764ba2); }"
        )
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(18, 14, 18, 14)
        title = QLabel("STG Store Sync")
        title.setStyleSheet("color: white; font-size: 18px; font-weight: 700;")
        self.job_label = QLabel("no job loaded")
        self.job_label.setStyleSheet("color: #e2e8f0;")
        self.status_label = QLabel(f"{ICON} ready")
        self.status_label.setStyleSheet("color: #86efac; font-weight: 600;")
        lay.addWidget(title)
        lay.addSpacing(12)
        lay.addWidget(self.job_label)
        lay.addStretch(1)
        lay.addWidget(self.status_label)
        return frame

    def _workspace(self) -> QWidget:
        tabs = QTabWidget()
        tabs.addTab(self._job_tab(), "Job")
        tabs.addTab(self._binding_tab(), "Columns")
        tabs.addTab(self._preflight_tab(), "Preflight")
        tabs.addTab(self._log_tab(), "Run log")
        tabs.addTab(self._conflicts_tab(), "Conflicts")
        self.tabs = tabs
        return tabs

    def _job_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
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
        lay.addLayout(row2)
        lay.addStretch(1)
        return w

    def _binding_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.columns_model = JobTableModel(self)
        view = QTableWidget()
        view.hide()  # model is the source of truth; view added on demand
        table = QTableWidget(0, len(JobTableModel.HEADERS))
        table.setHorizontalHeaderLabels(JobTableModel.HEADERS)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.columns_view = table
        lay.addWidget(table)
        bar = QHBoxLayout()
        add = QPushButton("Add column")
        add.clicked.connect(self._on_add_column)
        remove = QPushButton("Remove selected")
        remove.clicked.connect(self._on_remove_column)
        self.import_btn = QPushButton("Import mapping (.xlsx)")
        self.export_btn = QPushButton("Export template (.xlsx)")
        bar.addWidget(add)
        bar.addWidget(remove)
        bar.addStretch(1)
        bar.addWidget(self.import_btn)
        bar.addWidget(self.export_btn)
        lay.addLayout(bar)
        return w

    def _preflight_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.preflight_model = _DictTableModel(
            ["store_col", "source_var", "overlap", "status"]
        )
        self.preflight_view = _dict_view(self.preflight_model)
        lay.addWidget(self.preflight_view)
        self.approve_button = QPushButton("Approve binding")
        self.approve_button.clicked.connect(self.mark_binding_approved)
        bar = QHBoxLayout()
        bar.addStretch(1)
        bar.addWidget(self.approve_button)
        lay.addLayout(bar)
        return w

    def _log_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setStyleSheet(
            "background:#0f172a; color:#e2e8f0; font-family:monospace;"
        )
        lay.addWidget(self.log_view)
        return w

    def _conflicts_tab(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        self.conflicts_model = _DictTableModel(["match_value", "reason"])
        self.conflicts_view = _dict_view(self.conflicts_model)
        lay.addWidget(self.conflicts_view)
        return w

    def _action_rail(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet("background:#f8fafc; border-top:1px solid #e2e8f0;")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)

        self.dry_run_check = QCheckBox("Dry run")
        self.dry_run_check.setChecked(True)

        lay.addWidget(QLabel("Delete policy:"))
        self.delete_policy_combo = QComboBox()
        self.delete_policy_combo.addItem("Full history", "full_history")
        self.delete_policy_combo.addItem("Tracked only", "tracked_only")
        self.delete_policy_combo.addItem("Manual", "manual")
        lay.addWidget(self.delete_policy_combo)

        lay.addStretch(1)
        lay.addWidget(self.dry_run_check)
        self.sync_button = QPushButton("Sync")
        self.sync_button.setObjectName("btn_primary")
        self.sync_button.setStyleSheet(
            "QPushButton#btn_primary{background:#667eea;color:white;"
            "padding:8px 22px;border-radius:6px;font-weight:600;}"
        )
        self.sync_button.setEnabled(False)
        self.sync_button.clicked.connect(self.sync_requested.emit)
        lay.addWidget(self.sync_button)
        return frame

    # -- app API -----------------------------------------------------------
    def load_job(self, job: JobConfig) -> None:
        self._job = job
        self._binding_approved = False
        self.sync_button.setEnabled(False)
        self.job_label.setText(job.job)
        self.store_id_edit.setText(str(job.store.id))
        self.id_column_edit.setText(job.store.id_column)
        self.survey_id_edit.setText(job.source.survey_id)
        self.match_var_edit.setText(job.binding.match.source_var if job.binding.match else "")
        self.columns_model.set_columns(job.binding.columns)
        self._sync_table_from_model()
        idx = self.delete_policy_combo.findData(job.write.delete_policy)
        if idx >= 0:
            self.delete_policy_combo.setCurrentIndex(idx)
        self.dry_run_check.setChecked(job.write.dry_run)

    def mark_binding_approved(self) -> None:
        self._binding_approved = True
        self.sync_button.setEnabled(True)
        self.append_log("binding approved")

    def set_status(self, status: str, color: str = "#86efac") -> None:
        self.status_label.setText(f"{ICON} {status}")
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    def store_fields(self):
        from store_sync.core.config import StoreConfig

        return StoreConfig(id=int(self.store_id_edit.text() or 0),
                           id_column=self.id_column_edit.text().strip())

    def source_fields(self):
        from store_sync.core.config import SourceConfig

        return SourceConfig(survey_id=self.survey_id_edit.text().strip())

    def write_fields(self):
        return WriteConfig(
            dry_run=self.dry_run_check.isChecked(),
            delete_policy=self.delete_policy_combo.currentData(),
        )

    def set_preflight_rows(self, rows: list[dict]) -> None:
        self.preflight_model.set_rows(rows)
        if self._job is not None:
            self.columns_model.set_pull_terms(
                {r.get("source_var"): r.get("pull_term", "") for r in rows if r.get("source_var")}
            )

    def set_conflicts(self, rows: list[dict]) -> None:
        self.conflicts_model.set_rows(rows)

    def append_log(self, message: str) -> None:
        self.log_view.append(message)

    def _sync_table_from_model(self) -> None:
        cols = self.columns_model.columns()
        t = self.columns_view
        t.setRowCount(len(cols))
        for i, c in enumerate(cols):
            t.setItem(i, 0, QTableWidgetItem(c.store_col))
            t.setItem(i, 1, QTableWidgetItem(c.source_var))
            t.setItem(i, 2, QTableWidgetItem(""))
            t.setItem(i, 3, QTableWidgetItem(c.kind))

    def _on_add_column(self) -> None:
        self.columns_model.add_row()
        self._sync_table_from_model()

    def _on_remove_column(self) -> None:
        row = self.columns_view.currentRow()
        if row >= 0:
            self.columns_model.remove_row(row)
            self._sync_table_from_model()


# --- small generic dict table ----------------------------------------------


class _DictTableModel(QAbstractTableModel):
    def __init__(self, fields: list[str], parent=None):
        super().__init__(parent)
        self.fields = fields
        self._rows: list[dict] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.fields)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.fields[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        val = self._rows[index.row()].get(self.fields[index.column()], "")
        return "" if val is None else str(val)

    def set_rows(self, rows: list[dict]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()


def _dict_view(model: _DictTableModel) -> QTableWidget:
    t = QTableWidget(0, len(model.fields))
    t.setHorizontalHeaderLabels(model.fields)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    return t
