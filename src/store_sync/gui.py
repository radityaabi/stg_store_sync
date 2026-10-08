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
from store_sync.core.fetcher import parse_survey_index
from store_sync.core.stg_client import STGClient
from store_sync.core.state import State
from store_sync.core.sync import run_preflight, run_sync
from store_sync.ui.main_window import MainWindow
from store_sync.ui.mapping_io import export_template, parse_mapping


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
        window.preflight_requested.connect(self.run_preflight)
        window.import_mapping_requested.connect(self.import_mapping)
        window.export_mapping_requested.connect(self.export_mapping)

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

    def run_preflight(self) -> None:
        w = self.window
        try:
            job = self._job_from_window()
        except ValueError as e:
            w.append_log(f"config error: {e}")
            return
        w.append_log("preflight start")
        w.set_status("preflight", "#fbbf24")
        try:
            report, pull_terms = run_preflight(job, client=self._client())
        except Exception as e:  # noqa: BLE001
            w.set_status("error", "#f87171")
            w.append_log(f"preflight error: {type(e).__name__}: {e}")
            return

        rows = [
            {
                "store_col": r.store_col,
                "source_var": r.source_var,
                "overlap": f"{r.overlap}/{r.total}",
                "status": r.status,
                "pull_term": pull_terms.get(r.source_var, ""),
            }
            for r in report.table
        ]
        w.set_preflight_rows(rows)
        for e in report.errors:
            w.append_log(f"  ! {e}")
        w.set_status("preflight ok" if report.ok else "preflight blocked",
                     "#86efac" if report.ok else "#f87171")

    def export_mapping(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        w = self.window
        path, _ = QFileDialog.getSaveFileName(w, "Save mapping template", "mapping.xlsx", "Excel (*.xlsx)")
        if not path:
            return
        try:
            cols = [c.store_col for c in w.columns_model.columns()]
            export_template(path, cols)
            w.append_log(f"template written: {path}")
        except Exception as e:  # noqa: BLE001
            w.append_log(f"export error: {e}")

    def import_mapping(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        w = self.window
        path, _ = QFileDialog.getOpenFileName(w, "Open mapping", "", "Excel (*.xlsx)")
        if not path:
            return
        try:
            job = self._job_from_window()
            client = self._client()
            store_cols = list({k for row in client.get_store(job.store.id) for k in row})
            source_vars = sorted(parse_survey_index(
                client.get_survey_xml(job.source.survey_id)
            ).keys())
            parsed, errors = parse_mapping(path, store_cols, source_vars)
        except Exception as e:  # noqa: BLE001
            w.append_log(f"import error: {type(e).__name__}: {e}")
            return
        if errors:
            for e in errors:
                w.append_log(f"  ! import rejected: {e}")
            w.set_status("import rejected", "#f87171")
            return
        w.columns_model.set_columns(parsed)
        w.append_log(f"imported {len(parsed)} column(s) from {path}")
        w.set_status("binding changed", "#fbbf24")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    Controller(window)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
