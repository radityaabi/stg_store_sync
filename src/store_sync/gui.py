"""PySide6 entry point: launch the STG Store Sync window.

Wires the stacked window (Dashboard / JobDetail / RunDetail) to the persisted
registry, the single-worker run queue, and the cron scheduler:

* **Registry** (`core/registry.py`) — the job configs, same SQLite file as `State`.
* **RunQueue** (`core/run_queue.py`) — one worker thread, runs jobs one at a time
  with cooperative stop.
* **Scheduler** (`core/scheduler.py`) + **SchedulerThread** — enqueues due jobs.

The default `run_fn` builds an `STGClient` from `.env` (STG_SERVER/STG_USER/
STG_PASSWORD), loads the job from the registry, and runs `run_sync`. Tests inject
a `run_fn`, so no network is touched there. The module imports network-free.
"""
from __future__ import annotations

import sys
import types
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from PySide6.QtCore import QObject, Signal  # noqa: E402

from store_sync.core.config import (  # noqa: E402
    BindingConfig,
    ColumnBinding,
    JobConfig,
    SourceConfig,
    StoreConfig,
    WriteConfig,
)
from store_sync.core.registry import Registry  # noqa: E402
from store_sync.core.run_queue import RunQueue  # noqa: E402
from store_sync.core.scheduler import Scheduler  # noqa: E402
from store_sync.core.stg_client import STGClient  # noqa: E402
from store_sync.core.state import State  # noqa: E402
from store_sync.core.sync import run_preflight, run_sync  # noqa: E402
from store_sync.ui.main_window import MainWindow  # noqa: E402
from store_sync.ui.mapping_io import export_template, parse_mapping  # noqa: E402
from store_sync.ui.scheduler_thread import SchedulerThread  # noqa: E402


def _default_db_path() -> Path:
    d = Path.home() / ".stg_store_sync"
    d.mkdir(parents=True, exist_ok=True)
    return d / "state.db"


class _QueueBridge(QObject):
    """Marshal `RunQueue` notifications onto the GUI thread.

    `RunQueue` invokes its subscribers from the worker thread; Qt widgets must
    only be touched on the GUI thread, so the subscription emits this signal and
    the controller's slot runs queued on the main thread.
    """

    changed = Signal(object)


class Controller:
    """Glue between the window and the registry / queue / scheduler."""

    def __init__(self, window: MainWindow, *, db_path=None, run_fn=None, now_fn=None):
        self.window = window
        db_path = Path(db_path) if db_path is not None else _default_db_path()
        self.state = State(db_path)
        self.registry = Registry(db_path)

        self._run_fn = run_fn or self._default_run_fn
        self.queue = RunQueue(self._run_fn)
        self.scheduler = Scheduler(self.registry, self.queue.enqueue, now_fn=now_fn)
        self._scheduler_thread: SchedulerThread | None = None

        self._detail_name: str | None = None
        self._started = False
        self._stopped = False

        self._bridge = _QueueBridge()
        self._bridge.changed.connect(self._on_queue_changed)
        self.queue.subscribe(self._notify_queue)

        self._wire_signals()
        # `closeEvent` -> controller.stop (idempotent).
        window._stop_owned_threads = self.stop
        self._refresh()

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Start the queue worker + scheduler thread (idempotent)."""
        if self._started or self._stopped:
            return
        self._started = True
        self.queue.start()
        self._scheduler_thread = SchedulerThread(self.scheduler, interval=30)
        self._scheduler_thread.due.connect(self._on_due)
        self._scheduler_thread.start()

    def stop(self) -> None:
        """Stop both threads and close the DB connections (idempotent)."""
        if self._stopped:
            return
        self._stopped = True
        if self._scheduler_thread is not None:
            self._scheduler_thread.stop()
            self._scheduler_thread.wait(3000)
            self._scheduler_thread = None
        self.queue.shutdown()
        self.state.close()
        self.registry.close()

    # -- public API --------------------------------------------------------

    def run_now(self, name: str) -> None:
        self.queue.enqueue(name)

    def save(self, name: str, job: JobConfig, *, schedule: str, enabled: bool) -> None:
        """Persist `job` under `name` (add if new, else update) and refresh."""
        job = self._default_match_store_col(job)
        if name in self.registry.names():
            self.registry.update(name, job, schedule=schedule, enabled=enabled)
        else:
            self.registry.add(job, schedule=schedule, enabled=enabled)
        self._refresh()

    def delete(self, name: str) -> None:
        try:
            self.registry.delete(name)
        except KeyError:
            return
        if self._detail_name == name:
            self._detail_name = None
        self._refresh()

    def list_entries(self):
        return self.registry.list_entries()

    # -- run plumbing ------------------------------------------------------

    def _default_run_fn(self, name: str, should_stop):
        """Build a client from the environment, load the job, run one sync."""
        import os

        job = self.registry.get(name)
        client = STGClient(
            server=os.environ.get("STG_SERVER", ""),
            user=os.environ.get("STG_USER", ""),
            password=os.environ.get("STG_PASSWORD", ""),
        )
        return run_sync(
            job,
            client=client,
            state=self.state,
            should_stop=should_stop,
            throttle=0.05,
        )

    def _client(self) -> STGClient:
        import os

        return STGClient(
            server=os.environ.get("STG_SERVER", ""),
            user=os.environ.get("STG_USER", ""),
            password=os.environ.get("STG_PASSWORD", ""),
        )

    @staticmethod
    def _default_match_store_col(job: JobConfig) -> JobConfig:
        """Blank `match.store_col` defaults to `match.source_var` (NOT NULL)."""
        match = job.binding.match
        if match is not None and not match.store_col:
            fixed = replace(
                job, binding=replace(job.binding, match=ColumnBinding(
                    match.source_var, match.source_var, match.kind
                ))
            )
            return fixed
        return job

    # -- wiring ------------------------------------------------------------

    def _wire_signals(self) -> None:
        w = self.window
        w.new_requested.connect(self._new)
        w.run_requested.connect(self.run_now)
        w.stop_requested.connect(self.queue.stop_current)
        w.edit_requested.connect(self._open_job)
        w.delete_requested.connect(self.delete)
        w.save_requested.connect(self._save_from_detail)
        w.preflight_requested.connect(self._preflight)
        w.approve_requested.connect(self._approve)
        w.import_requested.connect(self._import_mapping)
        w.export_requested.connect(self._export_mapping)
        w.refresh_requested.connect(self._refresh)
        w.back.connect(w.show_dashboard)
        # Double-clicking a dashboard row opens (and loads) the job detail.
        w.dashboard.job_activated.connect(self._open_job)

    # -- queue / scheduler callbacks ---------------------------------------

    def _notify_queue(self, state) -> None:
        """Called by `RunQueue` (possibly off-thread); hop to the GUI thread."""
        self._bridge.changed.emit(state)

    def _on_queue_changed(self, state) -> None:
        self.window.dashboard.set_queue(state.active, state.pending)
        if state.active:
            self.window.set_status(f"running {state.active}", "#fbbf24")
        else:
            self.window.set_status("ready", "#86efac")
        self._refresh()

    def _on_due(self, name: str) -> None:
        # `Scheduler.tick` already enqueued the job; this is UI feedback only.
        self.window.set_status(f"scheduled {name}", "#fbbf24")

    # -- refresh -----------------------------------------------------------

    def _refresh(self) -> None:
        if self._stopped:
            return
        entries = self.registry.list_entries()
        dash = self.window.dashboard
        dash.set_entries(entries)

        snap = self.queue.snapshot()
        running = 1 if snap.active else 0
        failed = sum(1 for e in entries if e.last_status == "error")
        enabled = sum(1 for e in entries if e.enabled)
        dash.set_stats(len(entries), enabled, running, failed)
        dash.set_queue(snap.active, snap.pending)

    # -- navigation --------------------------------------------------------

    def _new(self) -> None:
        from PySide6.QtWidgets import QDialog

        from store_sync.ui.dialogs import JobDialog

        dlg = JobDialog(self.window)
        if dlg.exec() != QDialog.Accepted:
            return
        job = dlg.job()
        fields = dlg.entry_fields()
        name = fields["name"]
        self._detail_name = name or None
        entry = types.SimpleNamespace(
            name=name, schedule=fields["schedule"], enabled=fields["enabled"]
        )
        self.window.job_detail.load(entry, job)
        self.window.show_job(name)

    def _open_job(self, name: str) -> None:
        if not name:
            return
        try:
            entry = self.registry.get_entry(name)
            job = self.registry.get(name)
        except KeyError:
            return
        self._detail_name = name
        self.window.job_detail.load(entry, job)
        self.window.show_job(name)

    # -- detail actions ----------------------------------------------------

    def _job_from_detail(self) -> JobConfig:
        fields = self.window.job_detail.config_fields()
        match_source = fields["match_source_var"]
        match_store = fields["match_store_col"] or match_source
        return JobConfig(
            job=self._detail_name or "",
            store=StoreConfig(fields["store_id"], fields["id_column"]),
            source=SourceConfig(
                fields["survey_id"], fields["window_days"], fields["date_type"]
            ),
            binding=BindingConfig(
                match=ColumnBinding(match_source, match_store),
                columns=self.window.job_detail.bindings(),
            ),
            write=WriteConfig(
                dry_run=fields["dry_run"],
                delete_policy=fields["delete_policy"],
            ),
        )

    def _save_from_detail(self) -> None:
        name = self._detail_name or ""
        if not name:
            self.window.set_status("save needs a name", "#f87171")
            return
        detail = self.window.job_detail
        self.save(
            name,
            self._job_from_detail(),
            schedule=detail.schedule_edit.text().strip(),
            enabled=detail.enabled_check.isChecked(),
        )
        self.window.set_status(f"saved {name}", "#86efac")

    def _approve(self) -> None:
        # `JobDetail.approve()` has already checked the enabled box; persist the
        # approved binding through the normal save path so config + enabled land.
        name = self._detail_name
        if not name:
            return
        detail = self.window.job_detail
        self.save(
            name,
            self._job_from_detail(),
            schedule=detail.schedule_edit.text().strip(),
            enabled=detail.enabled_check.isChecked(),
        )
        self.window.set_status(f"binding approved: {name}", "#86efac")

    def _preflight(self) -> None:
        w = self.window
        try:
            job = self._job_from_detail()
        except ValueError as e:  # invalid config from the detail page
            w.set_status("config error", "#f87171")
            return
        w.set_status("preflight...", "#fbbf24")
        try:
            report, pull_terms = run_preflight(job, client=self._client())
        except Exception:  # noqa: BLE001 - surface any failure to operator
            w.set_status("preflight error", "#f87171")
            return
        rows = [
            {
                "column": r.source_var,
                "status": r.status,
                "detail": f"{r.overlap}/{r.total}",
            }
            for r in report.table
        ]
        w.job_detail.set_pull_terms(pull_terms)
        w.job_detail.set_preflight(rows)
        w.set_status(
            "preflight ok" if report.ok else "preflight blocked",
            "#86efac" if report.ok else "#f87171",
        )

    # -- mapping import / export -------------------------------------------

    def _mapping_pool(self, job: JobConfig) -> tuple[list[str], list[str]]:
        client = self._client()
        from store_sync.core.fetcher import parse_survey_index

        store_cols = sorted({k for r in client.get_store(job.store.id) for k in r})
        source_vars = sorted(
            parse_survey_index(client.get_survey_xml(job.source.survey_id)).keys()
        )
        return store_cols, source_vars

    def _import_mapping(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        w = self.window
        path, _ = QFileDialog.getOpenFileName(w, "Open mapping", "", "Excel (*.xlsx)")
        if not path:
            return
        try:
            job = self._job_from_detail()
            store_cols, source_vars = self._mapping_pool(job)
            parsed, errors = parse_mapping(path, store_cols, source_vars)
        except Exception:  # noqa: BLE001
            w.set_status("import error", "#f87171")
            return
        if errors:
            w.set_status("import rejected", "#f87171")
            return
        w.job_detail.binding_model.set_columns(parsed)
        w.set_status(f"imported {len(parsed)} column(s)", "#fbbf24")

    def _export_mapping(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        w = self.window
        path, _ = QFileDialog.getSaveFileName(
            w, "Save mapping template", "mapping.xlsx", "Excel (*.xlsx)"
        )
        if not path:
            return
        try:
            job = self._job_from_detail()
            store_cols, _ = self._mapping_pool(job)
            export_template(path, store_cols)
        except Exception:  # noqa: BLE001
            w.set_status("export error", "#f87171")
            return
        w.set_status(f"template written: {path}", "#86efac")


def main() -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow()
    controller = Controller(window)
    controller.start()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
