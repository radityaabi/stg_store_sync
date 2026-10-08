"""Read-only Qt table models for the dashboard and generic dict views."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt

from store_sync.core.config import human
from store_sync.core.registry import JobEntry


class JobListModel(QAbstractTableModel):
    """Read-only dashboard list over `JobEntry` rows.

    Only reads attributes off each entry — it never touches the registry.
    """

    HEADERS = ["Name", "Store", "Survey", "Schedule", "Enabled", "Last status", "Last run"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[JobEntry] = []

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._entries)

    def columnCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return self.HEADERS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or role != Qt.DisplayRole:
            return None
        e = self._entries[index.row()]
        col = index.column()
        if col == 0:
            return e.name
        if col == 1:
            return e.store_id
        if col == 2:
            return e.survey_id
        if col == 3:
            return human(e.schedule)
        if col == 4:
            return "yes" if e.enabled else "no"
        if col == 5:
            return e.last_status
        if col == 6:
            return e.last_finished_at
        return None

    def set_entries(self, entries: list[JobEntry]) -> None:
        self.beginResetModel()
        self._entries = list(entries)
        self.endResetModel()

    def entry_at(self, row: int) -> JobEntry:
        return self._entries[row]


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
