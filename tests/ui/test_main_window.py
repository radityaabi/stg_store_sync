"""Tests for the stacked main window (offscreen Qt)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from store_sync.ui.main_window import MainWindow  # noqa: E402

_app = QApplication.instance() or QApplication([])


def test_stacked_navigation():
    w = MainWindow()
    assert w.stack.currentWidget() is w.dashboard
    w.show_job("a")
    assert w.stack.currentWidget() is w.job_detail
    w.show_run(1)
    assert w.stack.currentWidget() is w.run_detail
    w.show_dashboard()
    assert w.stack.currentWidget() is w.dashboard
    w.close()


def test_run_request_proxied():
    w = MainWindow()
    seen = []
    w.run_requested.connect(seen.append)
    w.dashboard.run_requested.emit("a")
    assert seen == ["a"]
    w.close()


def test_proxy_signals_relay():
    """Every page signal reaches the window's matching proxy signal."""
    w = MainWindow()
    seen: dict[str, list] = {}
    names = (
        "new_requested",
        "run_requested",
        "stop_requested",
        "edit_requested",
        "delete_requested",
        "save_requested",
        "import_requested",
        "export_requested",
        "refresh_requested",
        "back",
    )
    for name in names:
        seen[name] = []
        getattr(w, name).connect(lambda *a, n=name: seen[n].append(a))

    # dashboard signals
    w.dashboard.new_requested.emit()
    w.dashboard.run_requested.emit("a")
    w.dashboard.stop_requested.emit()
    w.dashboard.edit_requested.emit("b")
    w.dashboard.delete_requested.emit("c")
    w.dashboard.refresh_requested.emit()
    # job-detail signals (run carries the currently-shown job name)
    w.show_job("j1")
    w.job_detail.back.emit()
    w.job_detail.save_requested.emit()
    w.job_detail.run_requested.emit()
    w.job_detail.stop_requested.emit()
    w.job_detail.import_requested.emit()
    w.job_detail.export_requested.emit()
    # run-detail signals
    w.run_detail.back.emit()

    assert seen["new_requested"] == [()]
    assert seen["run_requested"] == [("a",), ("j1",)]
    assert seen["stop_requested"] == [(), ()]  # dashboard + job detail
    assert seen["edit_requested"] == [("b",)]
    assert seen["delete_requested"] == [("c",)]
    assert seen["refresh_requested"] == [()]
    assert seen["back"] == [(), ()]  # job detail + run detail
    assert seen["save_requested"] == [()]
    assert seen["import_requested"] == [()]
    assert seen["export_requested"] == [()]
    w.close()


def test_close_event_calls_stop_owned_threads():
    w = MainWindow()
    calls = []
    w._stop_owned_threads = lambda: calls.append(True)
    w.close()
    assert calls == [True]


def test_run_activated_relayed_from_job_detail():
    w = MainWindow()
    seen = []
    w.run_activated.connect(seen.append)
    w.job_detail.run_activated.emit(5)
    assert seen == [5]
    w.close()


def test_schedules_requested_relayed_from_dashboard():
    w = MainWindow()
    seen = []
    w.schedules_requested.connect(lambda: seen.append(1))
    w.dashboard.schedules_requested.emit()
    assert seen == [1]
    w.close()


def test_header_object_names_and_status_recolor():
    w = MainWindow()
    assert w.breadcrumb_label.objectName() == "HeaderBreadcrumb"
    assert w.status_label.objectName() == "HeaderStatus"
    w.set_status("running x", "#fbbf24")
    assert "running x" in w.status_label.text()
    assert "#fbbf24" in w.status_label.styleSheet()
    w.close()
