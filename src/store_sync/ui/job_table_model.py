"""Editable binding table model: store column · source variable · pull term."""
from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from store_sync.core.config import ColumnBinding
from store_sync.ui import theme

# Derived/read-only pull-term text is muted to the DESIGN.md `secondary` token.
_MUTED = QColor(theme.TOKENS["colors"]["secondary"])


class JobTableModel(QAbstractTableModel):
    """Editable binding table: store column · source variable · pull term.

    The pull term column is **read-only** — it is derived, not entered (§7.3).
    No `kind` column: the STG API exposes no store column type, so kind was
    never more than a manual guess (see mapping_io.HEADERS).
    """

    HEADERS = ["Store column", "Source variable", "Pull term (derived)"]

    def __init__(self, parent=None):
        super().__init__(parent)
        self._columns: list[ColumnBinding] = []
        self._pull_terms: dict[str, str] = {}
        # kind: deliberately NOT surfaced in the UI (no API source of truth).

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
        if role == Qt.ForegroundRole and c == 2:
            return _MUTED  # derived/read-only
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
        self._columns.append(binding or ColumnBinding("", ""))
        self.endInsertRows()

    def remove_row(self, row: int) -> None:
        if 0 <= row < len(self._columns):
            self.beginRemoveRows(QModelIndex(), row, row)
            del self._columns[row]
            self.endRemoveRows()
