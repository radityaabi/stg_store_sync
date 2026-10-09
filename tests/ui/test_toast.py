"""Tests for the toast notifications (offscreen Qt)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt, QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from store_sync.ui.main_window import MainWindow  # noqa: E402
from store_sync.ui.toast import Toast, show_toast  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _active_toasts(window):
    return [t for t in window.findChildren(Toast) if t.isVisible()]


def test_show_toast_appears_and_auto_dismisses():
    w = MainWindow()
    w.resize(1100, 760)
    w.show()
    show_toast(w, "saved ok", kind="ok")
    toasts = _active_toasts(w)
    assert len(toasts) == 1
    t = toasts[0]
    assert t.objectName() == "ToastOk"
    assert t.text() == "saved ok"
    # Position: top-right, below the header, inside the window.
    assert 0 <= t.x() <= w.width()
    assert t.y() >= 58
    # Auto-dismiss (kind "ok" = 2600 ms): pump the loop until it hides.
    from PySide6.QtCore import QEventLoop

    loop = QEventLoop()
    QTimer.singleShot(3200, loop.quit)
    loop.exec()
    _app.processEvents()
    assert _active_toasts(w) == []
    w.close()


def test_toast_kinds_map_to_object_names():
    w = MainWindow()
    w.show()
    show_toast(w, "warn", kind="warn")
    show_toast(w, "boom", kind="fail")
    toasts = _active_toasts(w)
    assert {t.objectName() for t in toasts} == {"ToastWarn", "ToastFail"}
    # Stacked: the second toast sits below the first.
    ys = sorted(t.y() for t in toasts)
    assert ys[1] > ys[0]
    w.close()


def test_unknown_kind_falls_back_to_ok():
    w = MainWindow()
    w.show()
    show_toast(w, "weird", kind="nonsense")
    toasts = _active_toasts(w)
    assert len(toasts) == 1
    assert toasts[0].objectName() == "ToastOk"
    w.close()


def test_long_text_elided():
    w = MainWindow()
    w.show()
    long = "x" * 300
    show_toast(w, long, kind="fail")
    t = _active_toasts(w)[0]
    assert len(t.text()) == 160
    assert t.text().endswith("...")
    w.close()


def test_main_window_toast_api():
    """`window.toast()` is the sanctioned controller entry point."""
    w = MainWindow()
    w.show()
    w.toast('"a" saved', kind="ok")
    toasts = _active_toasts(w)
    assert len(toasts) == 1
    assert toasts[0].text() == '"a" saved'
    w.close()


def test_toast_does_not_steal_focus():
    """Toasts must not activate the window (WA_ShowWithoutActivating)."""
    w = MainWindow()
    w.show()
    show_toast(w, "no focus", kind="ok")
    t = _active_toasts(w)[0]
    assert t.testAttribute(Qt.WA_ShowWithoutActivating)
    # A child overlay (not a separate top-level window) so it dies with the
    # window; a top-level widget would leak a wrapper and crash GC.
    assert t.parent() is w
    assert not t.isWindow()
    w.close()
