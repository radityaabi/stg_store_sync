"""Stacked main window shell (PySide6).

The window is a thin navigation shell: a gradient header (title · breadcrumb ·
status dot) over a `QStackedWidget` holding the three pages built for the
GUI-redesign phase — `Dashboard`, `JobDetail`, `RunDetail`. Each page is a pure
view; the controller wires its intent signals. The shell simply:

* owns the stack and the header,
* switches between pages (`show_dashboard` / `show_job` / `show_run`),
* re-exports every page's intent signal as a window-level proxy signal so the
  controller can wire one object,
* quits any owned threads on close via `_stop_owned_threads` (set by the
  controller).

Style follows the suite's `DESIGN.md`: gradient header, page bodies own their
own action rails, dark console logs live inside the pages.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from store_sync.ui import theme
from store_sync.ui.dashboard import Dashboard
from store_sync.ui.job_detail import JobDetail
from store_sync.ui.run_detail import RunDetail

ICON = "\u25cf"  # status dot

_TITLE = "STG Store Sync"


class MainWindow(QMainWindow):
    """Stacked shell hosting Dashboard / JobDetail / RunDetail + proxy signals."""

    # -- proxy signals (one per page intent the controller cares about) ----
    new_requested = Signal()
    run_requested = Signal(str)
    stop_requested = Signal()
    edit_requested = Signal(str)
    delete_requested = Signal(str)
    save_requested = Signal()
    preflight_requested = Signal()
    approve_requested = Signal()
    import_requested = Signal()
    export_requested = Signal()
    refresh_requested = Signal()
    back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(_TITLE)
        self.resize(1100, 760)

        self._current_job: str | None = None
        self._stop_owned_threads = lambda: None

        self._build_ui()
        self._wire_signals()
        self.show_dashboard()

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._header())

        self.stack = QStackedWidget()
        self.dashboard = Dashboard()
        self.job_detail = JobDetail()
        self.run_detail = RunDetail()
        self.stack.addWidget(self.dashboard)
        self.stack.addWidget(self.job_detail)
        self.stack.addWidget(self.run_detail)
        root.addWidget(self.stack, stretch=1)

        self.setCentralWidget(central)

    def _header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("GradientHeader")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(18, 14, 18, 14)
        lay.setSpacing(12)

        title = QLabel(_TITLE)
        title.setObjectName("HeaderTitle")
        self.breadcrumb_label = QLabel("Dashboard")
        self.breadcrumb_label.setObjectName("HeaderBreadcrumb")
        self.status_label = QLabel(f"{ICON} ready")
        self.status_label.setObjectName("HeaderStatus")

        lay.addWidget(title)
        lay.addWidget(self.breadcrumb_label)
        lay.addStretch(1)
        lay.addWidget(self.status_label)
        return frame

    def _wire_signals(self) -> None:
        # Dashboard -> window proxies.
        self.dashboard.new_requested.connect(self.new_requested.emit)
        self.dashboard.run_requested.connect(self.run_requested.emit)
        self.dashboard.stop_requested.connect(self.stop_requested.emit)
        self.dashboard.edit_requested.connect(self.edit_requested.emit)
        self.dashboard.delete_requested.connect(self.delete_requested.emit)
        self.dashboard.refresh_requested.connect(self.refresh_requested.emit)
        self.dashboard.job_activated.connect(self.show_job)

        # Job detail -> window proxies. `run_requested` carries no payload on
        # the page (the currently-open job is implied), so adapt it.
        self.job_detail.back.connect(self.back.emit)
        self.job_detail.save_requested.connect(self.save_requested.emit)
        self.job_detail.preflight_requested.connect(self.preflight_requested.emit)
        self.job_detail.approve_requested.connect(self.approve_requested.emit)
        self.job_detail.run_requested.connect(self._on_detail_run)
        self.job_detail.stop_requested.connect(self.stop_requested.emit)
        self.job_detail.import_requested.connect(self.import_requested.emit)
        self.job_detail.export_requested.connect(self.export_requested.emit)

        # Run detail -> window proxies.
        self.run_detail.back.connect(self.back.emit)

    # -- navigation --------------------------------------------------------
    def show_dashboard(self) -> None:
        self.breadcrumb_label.setText("Dashboard")
        self.stack.setCurrentWidget(self.dashboard)

    def show_job(self, name: str) -> None:
        self._current_job = name
        self.breadcrumb_label.setText(f"Dashboard / {name}")
        self.stack.setCurrentWidget(self.job_detail)

    def show_run(self, run_id) -> None:
        self.breadcrumb_label.setText(f"Dashboard / Run #{run_id}")
        self.stack.setCurrentWidget(self.run_detail)

    # -- app API -----------------------------------------------------------
    def set_status(self, status: str, color: str = theme.RUNTIME_STATUS["ok"]) -> None:
        self.status_label.setText(f"{ICON} {status}")
        # Runtime status color: the only sanctioned inline style — every other
        # cosmetic comes from the shared theme.STYLESHEET.
        self.status_label.setStyleSheet(f"color: {color}; font-weight: 600;")

    # -- slots -------------------------------------------------------------
    def _on_detail_run(self) -> None:
        if self._current_job is not None:
            self.run_requested.emit(self._current_job)

    # -- lifecycle ---------------------------------------------------------
    def closeEvent(self, event) -> None:  # noqa: N802 (Qt override)
        self._stop_owned_threads()
        super().closeEvent(event)
