"""Run-detail pane: summary + counts/errors/conflicts tables + log.

Pure view over a single run's result dict and its conflict rows. Emits `back`
so the controller can return to the dashboard. Style follows the suite's
DESIGN tokens: dark console log, rounded group frames.
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
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from store_sync.ui.models import _DictTableModel


class RunDetail(QWidget):
    """Detail view for one run: status summary, counts, errors, conflicts, log."""

    back = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._build_ui()

    # -- construction ------------------------------------------------------
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._summary())
        root.addWidget(self._tables(), stretch=1)
        root.addWidget(self._log())
        root.addWidget(self._action_rail())

    def _summary(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("RunSummary")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 12, 14, 12)
        self.summary_label = QLabel("no run selected")
        lay.addWidget(self.summary_label)
        self.notification_label = QLabel("Mail: —")
        self.notification_label.setObjectName("RunNotification")
        lay.addWidget(self.notification_label)
        lay.addStretch(1)
        return frame

    def _tables(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(10)

        lay.addWidget(QLabel("Counts"))
        self.counts_model = _DictTableModel(["metric", "value"], self)
        lay.addWidget(_dict_view(self.counts_model))

        lay.addWidget(QLabel("Errors"))
        self.errors_model = _DictTableModel(["error", "detail"], self)
        lay.addWidget(_dict_view(self.errors_model))

        lay.addWidget(QLabel("Conflicts"))
        self.conflicts_model = _DictTableModel(["match_value", "reason"], self)
        lay.addWidget(_dict_view(self.conflicts_model))
        return w

    def _log(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(14, 0, 14, 12)
        lay.setSpacing(6)
        lay.addWidget(QLabel("Log"))
        self.log_view = QTextEdit()
        self.log_view.setObjectName("ConsoleLog")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(120)
        lay.addWidget(self.log_view)
        return w

    def _action_rail(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("ActionRail")
        lay = QHBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        self.back_button = QPushButton("← Back")
        self.back_button.clicked.connect(self.back.emit)
        lay.addWidget(self.back_button)
        lay.addStretch(1)
        return frame

    # -- app API -----------------------------------------------------------
    def load(self, run: dict, conflicts: list[dict], notification: dict | None = None) -> None:
        """Fill summary, counts/errors/conflicts tables from a run result."""
        run = run or {}
        status = run.get("status", "")
        run_id = run.get("id", "")
        self.summary_label.setText(f"Run #{run_id} · {status}".strip())
        self.notification_label.setText(_notification_line(notification))

        counts = run.get("counts") or {}
        self.counts_model.set_rows(
            [{"metric": k, "value": v} for k, v in counts.items()]
        )
        self.errors_model.set_rows([_as_error(e) for e in (run.get("errors") or [])])
        self.conflicts_model.set_rows([_as_conflict(c) for c in (conflicts or [])])

    def set_log(self, text: str) -> None:
        self.log_view.setPlainText(text or "")

    def append_log(self, message: str) -> None:
        self.log_view.append(message)


def _notification_line(notification: dict | None) -> str:
    """One-line summary of a run's notification attempt (or the absence of one)."""
    if not notification:
        return "Mail: —"
    status = notification.get("status", "")
    if status == "sent":
        to = notification.get("recipients") or ""
        return f"Mail: sent to {to}" if to else "Mail: sent"
    reason = notification.get("reason") or ""
    return f"Mail: {status} — {reason}" if reason else f"Mail: {status}"


def _as_error(e) -> dict:
    if isinstance(e, dict):
        if "error" in e or "detail" in e:
            return {
                "error": e.get("error", e.get("message", "")),
                "detail": e.get("detail", ""),
            }
        return {"error": e.get("message", str(e)), "detail": ""}
    return {"error": str(e), "detail": ""}


def _as_conflict(c) -> dict:
    if isinstance(c, dict):
        return {
            "match_value": c.get("match_value", c.get("match", "")),
            "reason": c.get("reason", ""),
        }
    return {"match_value": str(c), "reason": ""}


def _dict_view(model: _DictTableModel) -> QTableView:
    t = QTableView()
    t.setModel(model)
    t.setSelectionBehavior(QAbstractItemView.SelectRows)
    t.setEditTriggers(QAbstractItemView.NoEditTriggers)
    t.verticalHeader().setVisible(False)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
    t.setMinimumHeight(90)
    return t
