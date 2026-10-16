"""Non-blocking toast notifications (top-right overlay on the main window).

`set_status` only recolors the tiny header status dot — terminal events
(save ok, import rejected, blocked, …) were easy to miss. A toast
gives the operator a visible, colour-coded confirmation that auto-dismisses.

Styling follows DESIGN.md via object names only (`ToastOk`/`ToastWarn`/
`ToastFail` map to the `status_*` pill tokens in `theme.TOAST_QSS`); no
widget carries its own hex values. Transient progress states ("importing...",
"ready") must NOT toast — only terminal outcomes do (the controller decides;
`show_toast` itself is dumb).
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QLabel, QWidget

_KIND_MS = {"ok": 2600, "warn": 3600, "fail": 6000}
_KIND_OBJECT_NAME = {"ok": "ToastOk", "warn": "ToastWarn", "fail": "ToastFail"}

# Elide cap for long reasons (e.g. an import-rejection line).
_MAX_TEXT = 160


class Toast(QLabel):
    """One floating notification; auto-deletes after its kind's timeout."""

    def __init__(self, parent: QWidget, text: str, *, kind: str = "ok"):
        super().__init__(text, parent)
        self.setObjectName(_KIND_OBJECT_NAME.get(kind, "ToastOk"))
        self.setWordWrap(True)
        self.setMaximumWidth(420)
        self.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        # Overlay, not layout: a **child** widget positioned absolutely floats
        # above the page and is destroyed with its parent window. (A Qt.ToolTip
        # window flag would make it a separate top-level widget that outlives
        # the window, leaking a shiboken wrapper whose dangling C++ pointer
        # crashes the next GC pass — a real segfault, seen in the suite.)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        if len(text) > _MAX_TEXT:
            self.setText(text[: _MAX_TEXT - 3] + "...")
        # `QTimer.singleShot` (a timer Qt owns, no Python attribute) — do NOT
        # keep the timer as `self._timer`: `self._timer.timeout.connect(
        # self._dismiss)` forms a reference cycle (toast → timer → bound method
        # → toast) that defers finalisation to a later GC pass, which can run on
        # a worker thread and finalise a wrapper whose C++ was already destroyed
        # with its parent window → hard segfault.
        QTimer.singleShot(_KIND_MS.get(kind, 2600), self._dismiss)

    def _dismiss(self) -> None:
        self.hide()
        self.deleteLater()


def show_toast(window: QWidget, text: str, *, kind: str = "ok") -> None:
    """Show a toast stacked in the window's top-right (below the header).

    `kind`: "ok" | "warn" | "fail" (colour + duration; fail lingers longest).
    Must be called on the GUI thread.
    """
    if kind not in _KIND_OBJECT_NAME:
        kind = "ok"
    toast = Toast(window, text, kind=kind)
    toast.adjustSize()
    margin = 16
    header_h = 58  # GradientHeader height (title 18px + padding 14*2)
    x = max(0, window.width() - toast.width() - margin)
    y = header_h + margin
    # Nudge below any toasts still on screen so they stack visibly.
    for child in window.findChildren(Toast):
        if child is not toast and child.isVisible():
            y = max(y, child.y() + child.height() + 8)
    toast.move(x, y)
    toast.show()
