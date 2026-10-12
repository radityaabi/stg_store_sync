"""Dashboard widget: store table + stat cards + queue strip + toolbar.

The dashboard is a pure view: it holds a `JobListModel` over `list[JobEntry]`
and emits intent signals. It never touches the registry or the network — the
controller wires the signals in. The gradient header lives in the main window;
this is the body only.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from store_sync.core.registry import JobEntry
from store_sync.ui import theme
from store_sync.ui.models import JobListModel


class Dashboard(QWidget):
    """Store dashboard: stat cards, queue strip, job table, toolbar."""

    new_requested = Signal()
    run_requested = Signal(str)
    stop_requested = Signal()
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    schedules_requested = Signal()
    refresh_requested = Signal()
    job_activated = Signal(str)
    os_sync_requested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(10)
        root.addWidget(self._stats_row())
        root.addWidget(self._queue_strip())
        root.addWidget(self._toolbar())
        root.addWidget(self._table(), stretch=1)
        # Freshly-constructed dashboard has an empty model: show the empty hint.
        self._update_empty_state()

    def _stats_row(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        self._stat_labels: dict[str, QLabel] = {}
        for key, label in (
            ("total", "Total"),
            ("enabled", "Enabled"),
            ("running", "Running"),
            ("failed", "Failed"),
        ):
            lay.addWidget(self._stat_card(key, label), stretch=1)
        return w

    def _stat_card(self, key: str, label: str) -> QWidget:
        frame = QFrame()
        frame.setObjectName("StatCard")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(2)
        value = QLabel("0")
        value.setObjectName("StatValue")
        caption = QLabel(label.upper())
        caption.setObjectName("StatCaption")
        theme.style_caps(caption)
        lay.addWidget(value)
        lay.addWidget(caption)
        self._stat_labels[key] = value
        return frame

    def _queue_strip(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("QueueStrip")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(12, 8, 12, 8)
        caption = QLabel("QUEUE")
        caption.setObjectName("QueueCaption")
        theme.style_caps(caption, px=12)
        self.queue_label = QLabel("idle")
        self.queue_label.setObjectName("QueueLabel")
        lay.addWidget(caption)
        lay.addSpacing(8)
        lay.addWidget(self.queue_label, stretch=1)
        return frame

    def _toolbar(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        new_btn = QPushButton("➕ New")
        new_btn.setObjectName("btn_primary")
        new_btn.clicked.connect(self.new_requested.emit)

        run_btn = QPushButton("▶ Run now")
        run_btn.setObjectName("btn_primary")
        run_btn.clicked.connect(self._on_run)
        stop_btn = QPushButton("⏹ Stop")
        stop_btn.setObjectName("btn_danger")
        stop_btn.clicked.connect(self.stop_requested.emit)
        edit_btn = QPushButton("✏️ Edit")
        edit_btn.clicked.connect(self._on_edit)
        delete_btn = QPushButton("🗑 Delete")
        delete_btn.setObjectName("btn_danger")
        delete_btn.clicked.connect(self._on_delete)

        schedules_btn = QPushButton("⏰ Schedules")
        schedules_btn.clicked.connect(self.schedules_requested.emit)
        os_btn = QPushButton("🗓 Sync OS schedule")
        os_btn.clicked.connect(self.os_sync_requested.emit)
        refresh_btn = QPushButton("🔄 Refresh")
        refresh_btn.clicked.connect(self.refresh_requested.emit)

        self.new_button = new_btn
        self.run_button = run_btn
        self.stop_button = stop_btn
        self.edit_button = edit_btn
        self.delete_button = delete_btn
        self.schedules_button = schedules_btn
        self.os_sync_button = os_btn
        self.refresh_button = refresh_btn

        lay.addWidget(new_btn)
        lay.addWidget(run_btn)
        lay.addWidget(stop_btn)
        lay.addSpacing(8)
        lay.addWidget(edit_btn)
        lay.addWidget(delete_btn)
        lay.addStretch(1)
        lay.addWidget(schedules_btn)
        lay.addWidget(os_btn)
        lay.addWidget(refresh_btn)
        return w

    def _table(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)

        self.model = JobListModel(self)
        self.table = QTableView()
        self.table.setObjectName("StoreTable")
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.doubleClicked.connect(self._on_double_click)
        lay.addWidget(self.table, stretch=1)

        self.empty_label = QLabel("No stores registered yet — click ➕ New")
        self.empty_label.setObjectName("EmptyState")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setVisible(False)
        lay.addWidget(self.empty_label)
        return host

    # -- app API -----------------------------------------------------------
    def set_entries(self, entries: list[JobEntry], *,
                    os_status: dict[str, str] | None = None) -> None:
        self.model.set_entries(entries, os_status=os_status)
        self._update_empty_state()

    def _update_empty_state(self) -> None:
        """Show the friendly hint whenever the model has no rows."""
        self.empty_label.setVisible(self.model.rowCount() == 0)

    def set_queue(self, active: str | None, pending: list[str]) -> None:
        parts: list[str] = []
        if active:
            parts.append(f"▶ {active}")
        parts.extend(f"• {name}" for name in pending)
        self.queue_label.setText("  ".join(parts) if parts else "idle")

    def set_stats(self, total, enabled, running, failed) -> None:
        self._stat_labels["total"].setText(str(total))
        self._stat_labels["enabled"].setText(str(enabled))
        self._stat_labels["running"].setText(str(running))
        self._stat_labels["failed"].setText(str(failed))

    def selected_name(self) -> str | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows:
            idx = self.table.currentIndex()
            if not idx.isValid():
                return None
            row = idx.row()
        else:
            row = rows[0].row()
        if 0 <= row < self.model.rowCount():
            return self.model.entry_at(row).name
        return None

    # -- slots -------------------------------------------------------------
    def _on_run(self) -> None:
        name = self.selected_name()
        if name:
            self.run_requested.emit(name)

    def _on_edit(self) -> None:
        name = self.selected_name()
        if name:
            self.edit_requested.emit(name)

    def _on_delete(self) -> None:
        name = self.selected_name()
        if name:
            self.delete_requested.emit(name)

    def _on_double_click(self, index) -> None:
        if index.isValid():
            self.job_activated.emit(self.model.entry_at(index.row()).name)
