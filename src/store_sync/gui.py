"""PySide6 entry point: launch the STG Store Sync window.

Wires the window to the real orchestrator: on Sync, it builds an `STGClient`
from `.env` (STG_SERVER/STG_USER/STG_PASSWORD), loads persisted state, runs one
`run_sync`, and streams the result into the log + conflicts panel.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from store_sync.core.config import BindingConfig, ColumnBinding, JobConfig
from store_sync.core.stg_client import STGClient
from store_sync.core.state import State
from store_sync.core.sync import run_sync
from store_sync.ui.main_window import MainWindow


def _data_dir() -> Path:
    d = Path.home() / ".stg_store_sync"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Controller:
    """Glue between the window and the core pipeline."""

    def __init__(self, window: MainWindow):
        self.window = window
        self.state = State(_data_dir() / "state.db")
        window.sync_requested.connect(self.run)

    def _client(self) -> STGClient:
        import os

        return STGClient(
            server=os.environ.get("STG_SERVER", ""),
            user=os.environ.get("STG_USER", ""),
            password=os.environ.get("STG_PASSWORD", ""),
        )

    def _job_from_window(self) -> JobConfig:
        w = self.window
        match_var = w.match_var_edit.text().strip()
        return JobConfig(
            job=w.job_label.text(),
            store=w.store_fields(),
            source=w.source_fields(),
            binding=BindingConfig(
                match=ColumnBinding(match_var, match_var),
                columns=w.columns_model.columns(),
            ),
            write=w.write_fields(),
        )

    def run(self) -> None:
        w = self.window
        try:
            job = self._job_from_window()
        except ValueError as e:  # invalid config from the window
            w.append_log(f"config error: {e}")
            return

        w.set_status("running", "#fbbf24")
        w.append_log(f"sync start (dry_run={job.write.dry_run})")
        try:
            res = run_sync(job, client=self._client(), state=self.state, throttle=0.05)
        except Exception as e:  # noqa: BLE001 - surface any failure to the operator
            w.set_status("error", "#f87171")
            w.append_log(f"error: {type(e).__name__}: {e}")
            return

        for kind, n in res.counts.items():
            if n:
                w.append_log(f"  {kind}: {n}")
        for err in res.errors:
            w.append_log(f"  ! {err}")
        w.append_log(f"status: {res.status}")
        w.set_conflicts(
            [
                {"match_value": a.match_value, "reason": a.reason}
                for a in res.plan.actions
                if a.kind == "conflict"
            ]
        )
        w.set_status(res.status if res.status != "ok" else "done",
                     "#86efac" if res.status in ("ok", "dry") else "#f87171")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    Controller(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
