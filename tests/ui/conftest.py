import gc
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(autouse=True)
def _gc_on_main_thread():
    """Force cyclic GC on the test (main) thread after each UI test.

    PySide6 widgets form reference cycles. If Python's cyclic GC happens to run
    on a worker thread — e.g. the mapping-import worker inside openpyxl — it
    finalises a QWidget off the GUI thread, which segfaults the interpreter.
    Collecting here, on the main thread, keeps widget finalisation safe. (In
    production the window lives for the whole process, so its cycle is never
    collected mid-run; this only affects the suite's short-lived windows.)
    """
    yield
    gc.collect()
