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

import logging
import sys
import threading
import types
from dataclasses import replace
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from PySide6.QtCore import QObject, QThread, Signal  # noqa: E402
from PySide6.QtWidgets import QMessageBox  # noqa: E402

from store_sync.core.config import (  # noqa: E402
    BindingConfig,
    ColumnBinding,
    JobConfig,
    Schedule,
    SourceConfig,
    StoreConfig,
    WriteConfig,
    human,
)
from store_sync.core.registry import Registry  # noqa: E402
from store_sync.core.run_queue import RunQueue  # noqa: E402
from store_sync.core.scheduler import Scheduler  # noqa: E402
from store_sync.core.stg_client import STGClient  # noqa: E402
from store_sync.core.state import State  # noqa: E402
from store_sync.core.sync import run_preflight, run_sync  # noqa: E402
from store_sync.ui.main_window import MainWindow  # noqa: E402
from store_sync.ui.dialogs import ScheduleOverview  # noqa: E402
from store_sync.ui.mapping_io import export_template, parse_mapping  # noqa: E402
from store_sync.ui.scheduler_thread import SchedulerThread  # noqa: E402
from store_sync.ui import theme  # noqa: E402

logger = logging.getLogger(__name__)

# Runtime header-dot colors from the shared theme (DESIGN.md `status-dot` + the
# two runtime amber/red shades). Kept as locals for readable call sites.
_STATUS_OK = theme.RUNTIME_STATUS["ok"]
_STATUS_WARN = theme.RUNTIME_STATUS["warn"]
_STATUS_FAIL = theme.RUNTIME_STATUS["fail"]


def _default_db_path() -> Path:
    d = Path.home() / ".stg_store_sync"
    d.mkdir(parents=True, exist_ok=True)
    return d / "state.db"


def _enable_wal(conn) -> None:
    """Let a background write and a foreground read share the file cleanly."""
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:  # noqa: BLE001 - `:memory:` refuses WAL; harmless
        logger.debug("journal_mode=WAL unavailable", exc_info=True)


def _fmt_counts(counts) -> str:
    """`{add: 1}` → `"add 1"` for the History tab's counts cell."""
    if not counts:
        return ""
    return ", ".join(f"{k} {v}" for k, v in counts.items())


def _run_log(run: dict) -> str:
    """A readable log for the Run detail console (stored log or a summary)."""
    lines = [
        f"run #{run.get('id')} · {run.get('status') or 'unknown'}",
        f"started:  {run.get('started_at') or '-'}",
        f"finished: {run.get('finished_at') or '-'}",
        f"dry_run:  {bool(run.get('dry_run'))}",
    ]
    counts = run.get("counts") or {}
    if counts:
        lines.append("counts:   " + _fmt_counts(counts))
    return "\n".join(lines)


class _QueueBridge(QObject):
    """Marshal `RunQueue` notifications onto the GUI thread.

    `RunQueue` invokes its subscribers from the worker thread; Qt widgets must
    only be touched on the GUI thread, so the subscription emits this signal and
    the controller's slot runs queued on the main thread.
    """

    changed = Signal(object)


class _RegistryProxy:
    """Thread-safe stand-in for `Registry` handed to the `Scheduler`.

    The scheduler ticks on its own thread, so it must not be given the GUI
    thread's connection. Every call resolves `Registry` lazily for the calling
    thread.
    """

    def __init__(self, controller):
        self._controller = controller

    def list_entries(self):
        return self._controller._registry().list_entries()


class _NetworkWorker(QThread):
    """Run one network/export closure off the GUI thread (DESIGN.md: never block).

    The closure owns its own `STGClient` session + DB connections and must close
    them before returning. Results (or the exception) are marshalled back to the
    GUI thread via `ok`/`failed` — QThread objects live on the thread that built
    them, so these deliver queued on the main thread.
    """

    ok = Signal(object)
    failed = Signal(object)

    def __init__(self, work):
        super().__init__()
        self._work = work

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            result = self._work()
        except Exception as exc:  # noqa: BLE001 - report to the GUI thread
            logger.exception("network worker failed")
            self.failed.emit(exc)
            return
        self.ok.emit(result)


class Controller:
    """Glue between the window and the registry / queue / scheduler."""

    def __init__(self, window: MainWindow, *, db_path=None, run_fn=None, now_fn=None):
        self.window = window
        self._db_path = Path(db_path) if db_path is not None else _default_db_path()

        # SQLite connections are per-thread; each thread that touches the DB
        # gets its own `State`/`Registry` on first use via `_state`/`_registry`.
        self._local = threading.local()
        self._conns: list = []
        self._conns_lock = threading.Lock()

        self._run_fn = run_fn or self._default_run_fn
        self.queue = RunQueue(self._run_fn)
        self.scheduler = Scheduler(
            _RegistryProxy(self), self.queue.enqueue, now_fn=now_fn
        )
        self._scheduler_thread: SchedulerThread | None = None

        self._detail_name: str | None = None
        self._started = False
        self._stopped = False
        self._active_prev: str | None = None

        # In-flight off-thread network workers (preflight / mapping pool). The
        # `_preflight_thread`/`_mapping_thread` attrs are "the current worker for
        # this slot" (status/busy checks); `_workers` is the strong-reference
        # container that guarantees a running QThread is never GC'd mid-run. Each
        # worker is discarded on its `finished` signal; all are joined in `stop()`.
        self._preflight_thread: QThread | None = None
        self._mapping_thread: QThread | None = None
        self._workers: set[QThread] = set()

        self._bridge = _QueueBridge()
        self._bridge.changed.connect(self._on_queue_changed)
        self.queue.subscribe(self._notify_queue)

        self._wire_signals()
        # `closeEvent` -> controller.stop (idempotent).
        window._stop_owned_threads = self.stop
        self._refresh()

    # -- per-thread DB connections -----------------------------------------

    def _state(self) -> State:
        st = getattr(self._local, "state", None)
        if st is None:
            if self._stopped:
                raise RuntimeError("controller stopped")
            st = State(self._db_path)
            _enable_wal(st.conn)
            self._local.state = st
            with self._conns_lock:
                self._conns.append(st)
        return st

    def _registry(self) -> Registry:
        reg = getattr(self._local, "registry", None)
        if reg is None:
            if self._stopped:
                raise RuntimeError("controller stopped")
            reg = Registry(self._db_path)
            _enable_wal(reg.conn)
            self._local.registry = reg
            with self._conns_lock:
                self._conns.append(reg)
        return reg

    # Backwards-compatible attributes: always the calling thread's connection.
    @property
    def state(self) -> State:
        return self._state()

    @property
    def registry(self) -> Registry:
        return self._registry()

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
        """Stop both threads and close every DB connection (idempotent)."""
        if self._stopped:
            return
        self._stopped = True
        if self._scheduler_thread is not None:
            self._scheduler_thread.stop()
            self._scheduler_thread.wait(3000)
            self._scheduler_thread = None
        self._join_worker("_preflight_thread")
        self._join_worker("_mapping_thread")
        self.queue.shutdown()
        with self._conns_lock:
            conns, self._conns = list(self._conns), []
        for conn in conns:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - a dead thread's conn must not block stop
                logger.debug("error closing connection at stop", exc_info=True)
        self._local = threading.local()

    def _join_worker(self, attr: str) -> None:
        """Join every off-thread network worker (unbounded) + clear the slots.

        The wait is deliberately **unbounded**: a running QThread must never be
        left unreferenced (AGENTS §3.4), and dropping the last ref mid-run aborts
        the process (`QThread: Destroyed while thread is still running`). The
        in-flight request is bounded by the client's own timeout (120 s fetch /
        300 s export), so `stop()`/close blocks until it returns or times out —
        the safe, honest tradeoff. The `attr` argument is kept for call-site
        compatibility but the container is the authoritative set.
        """
        for worker in list(self._workers):
            worker.wait()
        self._workers.clear()
        self._preflight_thread = None
        self._mapping_thread = None

    def _reap_worker(self, worker: _NetworkWorker) -> None:
        """Drop a finished worker from the strong-ref container (keep slot attr).

        The slot attr still points at the worker so status/busy checks and the
        existing poll loops can observe it; only the GC guarantee is released.
        """
        self._workers.discard(worker)

    def _start_worker(
        self, attr: str, worker: _NetworkWorker, *, label: str
    ) -> bool:
        """Start `worker` unless the `attr` slot is busy (never blocks the GUI).

        A re-entrant click while a fetch is in flight must return immediately
        with an informational status — joining here would freeze the GUI for the
        client's full timeout. Returns True if `worker` was started.
        """
        current = getattr(self, attr)
        if current is not None and current.isRunning():
            self.window.set_status(f"{label} already running", _STATUS_WARN)
            return False
        setattr(self, attr, worker)
        self._workers.add(worker)
        worker.finished.connect(lambda w=worker: self._reap_worker(w))
        worker.start()
        return True

    # -- public API --------------------------------------------------------

    def run_now(self, name: str) -> None:
        self.queue.enqueue(name)

    def save(self, name: str, job: JobConfig, *, schedule: Schedule,
             enabled: bool) -> bool:
        """Persist `job` under `name` (add if new, else update) and refresh.

        Returns True on success; on a registry error it logs and returns False
        rather than raising out of a Qt slot.
        """
        job = self._default_match_store_col(job)
        try:
            if name in self.registry.names():
                self.registry.update(name, job, schedule=schedule, enabled=enabled)
            else:
                self.registry.add(job, schedule=schedule, enabled=enabled)
        except Exception:  # noqa: BLE001 - never crash the save slot
            logger.exception("controller: failed to save job %r", name)
            return False
        self._refresh()
        return True

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
        job = self.registry.get(name)
        client = None
        try:
            client = self._client()
            return run_sync(
                job,
                client=client,
                state=self.state,
                should_stop=should_stop,
                throttle=0.05,
            )
        finally:
            if client is not None:
                self._close_client(client)

    def _client(self) -> STGClient:
        import os

        return STGClient(
            server=os.environ.get("STG_SERVER", ""),
            user=os.environ.get("STG_USER", ""),
            password=os.environ.get("STG_PASSWORD", ""),
        )

    @staticmethod
    def _close_client(client: STGClient) -> None:
        """Close the client's `requests.Session` (STGClient has no `close()`)."""
        try:
            client.session.close()
        except Exception:  # noqa: BLE001
            logger.debug("error closing STG client session", exc_info=True)

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
        w.delete_requested.connect(self._confirm_delete)
        w.save_requested.connect(self._save_from_detail)
        w.preflight_requested.connect(self._preflight)
        w.approve_requested.connect(self._approve)
        w.import_requested.connect(self._import_mapping)
        w.export_requested.connect(self._export_mapping)
        w.refresh_requested.connect(self._refresh)
        w.schedules_requested.connect(self._show_schedules)
        w.run_activated.connect(self._show_run)
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
            self.window.set_status(f"running {state.active}", _STATUS_WARN)
        else:
            self.window.set_status("ready", _STATUS_OK)
        # A run just finished → refresh the open job's History/Conflicts.
        if self._active_prev is not None and state.active is None:
            if self._detail_name:
                self._load_history(self._detail_name)
        self._active_prev = state.active
        self._refresh()

    def _on_due(self, name: str) -> None:
        # `Scheduler.tick` already enqueued the job; this is UI feedback only.
        self.window.set_status(f"scheduled {name}", _STATUS_WARN)

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
            name=name, schedule=dlg.schedule(), enabled=fields["enabled"]
        )
        self.window.job_detail.load(entry, job)
        self._load_history(name)
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
        self._load_history(name)
        self.window.show_job(name)

    def _load_history(self, name: str | None) -> None:
        """Fill the open job's History + Conflicts tabs from `State`."""
        detail = self.window.job_detail
        if not name:
            detail.set_history([])
            detail.set_conflicts([])
            return
        runs = self.state.recent_runs(name)
        detail.set_history(
            [
                {
                    "time": r.get("finished_at") or r.get("started_at") or "",
                    "status": r.get("status") or "",
                    "counts": _fmt_counts(r.get("counts")),
                    "id": r.get("id"),
                }
                for r in runs
            ]
        )
        latest = runs[0]["id"] if runs else None
        conflicts = self.state.conflicts_for_run(latest) if latest is not None else []
        detail.set_conflicts(
            [
                {
                    "match": c.get("match_value", ""),
                    "reason": c.get("reason", ""),
                    "side": c.get("side", ""),
                }
                for c in conflicts
            ]
        )

    def _show_run(self, run_id: int) -> None:
        """Load one run + its conflicts into the Run detail page."""
        run = self.state.get_run(int(run_id))
        if not run:
            return
        conflicts = self.state.conflicts_for_run(int(run_id))
        detail = self.window.run_detail
        detail.load(run, conflicts)
        detail.set_log(_run_log(run))
        self.window.show_run(run_id)

    def _show_schedules(self) -> None:
        """Show a read-only overview of the registry's schedules."""
        rows = [
            {"name": e.name, "schedule": e.schedule, "enabled": bool(e.enabled)}
            for e in self.list_entries()
        ]
        ScheduleOverview(rows, self.window).exec()

    def _confirm_delete(self, name: str) -> None:
        """Ask before deleting; on Yes defer to the raw `delete()`."""
        answer = QMessageBox.question(
            self.window,
            "Delete store",
            f'Delete store "{name}"?\n\n'
            "This removes its config; run history is kept.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer == QMessageBox.Yes:
            self.delete(name)

    # -- detail actions ----------------------------------------------------

    def _job_from_detail(self) -> JobConfig:
        fields = self.window.job_detail.config_fields()
        match_source = fields["match_source_var"]
        match_store = fields["match_store_col"] or match_source
        return JobConfig(
            job=self._detail_name or "",
            store=StoreConfig(fields["store_id"], fields["id_column"]),
            source=SourceConfig(fields["survey_id"]),
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
            self.window.set_status("save needs a name", _STATUS_FAIL)
            return
        detail = self.window.job_detail
        try:
            job = self._job_from_detail()
        except ValueError:
            logger.exception("controller: invalid config for %r", name)
            self.window.set_status("config error", _STATUS_FAIL)
            return
        if self.save(
            name,
            job,
            schedule=detail.schedule(),
            enabled=detail.enabled_check.isChecked(),
        ):
            self.window.set_status(f"saved {name}", _STATUS_OK)
        else:
            self.window.set_status(f"save failed: {name}", _STATUS_FAIL)

    def _approve(self) -> None:
        # `JobDetail.approve()` has already checked the enabled box; persist the
        # approved binding through the normal save path so config + enabled land.
        name = self._detail_name
        if not name:
            return
        detail = self.window.job_detail
        try:
            job = self._job_from_detail()
        except ValueError:
            logger.exception("controller: invalid config for %r", name)
            self.window.set_status("config error", _STATUS_FAIL)
            return
        if self.save(
            name,
            job,
            schedule=detail.schedule(),
            enabled=detail.enabled_check.isChecked(),
        ):
            self.window.set_status(f"binding approved: {name}", _STATUS_OK)
        else:
            self.window.set_status(f"approve failed: {name}", _STATUS_FAIL)

    def _preflight(self) -> None:
        w = self.window
        try:
            job = self._job_from_detail()
        except ValueError:
            logger.exception("controller: invalid config for preflight")
            w.set_status("config error", _STATUS_FAIL)
            return
        w.set_status("preflight...", _STATUS_WARN)
        # Client construction is pure (no I/O) → keep it on the GUI thread so a
        # bad env is reported immediately; the fetch itself runs off-thread.
        try:
            client = self._client()
        except Exception:  # noqa: BLE001 - surface any failure to operator
            logger.exception("controller: preflight client failed")
            w.set_status("preflight error", _STATUS_FAIL)
            return

        def work():
            # The worker performs the network fetch and closes its session.
            try:
                return run_preflight(job, client=client)
            finally:
                self._close_client(client)

        worker = _NetworkWorker(work)
        worker.ok.connect(self._on_preflight_ok)
        worker.failed.connect(self._on_preflight_error)
        self._start_worker("_preflight_thread", worker, label="preflight")

    def _on_preflight_ok(self, result) -> None:
        w = self.window
        report, pull_terms = result
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
            _STATUS_OK if report.ok else _STATUS_FAIL,
        )

    def _on_preflight_error(self, exc) -> None:
        self.window.set_status("preflight error", _STATUS_FAIL)

    # -- mapping import / export -------------------------------------------

    def _mapping_pool(self, job: JobConfig) -> tuple[list[str], list[str]]:
        from store_sync.core.fetcher import parse_survey_index

        client = None
        try:
            client = self._client()
            store_cols = sorted(
                {k for r in client.get_store(job.store.id) for k in r}
            )
            source_vars = sorted(
                parse_survey_index(client.get_survey_xml(job.source.survey_id)).keys()
            )
        finally:
            if client is not None:
                self._close_client(client)
        return store_cols, source_vars

    def _import_mapping(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        w = self.window
        path, _ = QFileDialog.getOpenFileName(w, "Open mapping", "", "Excel (*.xlsx)")
        if not path:
            return
        try:
            job = self._job_from_detail()
        except ValueError:
            logger.exception("controller: invalid config for import")
            w.set_status("config error", _STATUS_FAIL)
            return
        w.set_status("importing...", _STATUS_WARN)

        def work():
            store_cols, source_vars = self._mapping_pool(job)
            return parse_mapping(path, store_cols, source_vars)

        worker = _NetworkWorker(work)
        worker.ok.connect(self._on_import_ok)
        worker.failed.connect(self._on_import_error)
        self._start_worker("_mapping_thread", worker, label="import")

    def _on_import_ok(self, result) -> None:
        w = self.window
        parsed, errors = result
        if errors:
            w.set_status("import rejected", _STATUS_FAIL)
            return
        w.job_detail.binding_model.set_columns(parsed)
        w.set_status(f"imported {len(parsed)} column(s)", _STATUS_WARN)

    def _on_import_error(self, exc) -> None:
        self.window.set_status("import error", _STATUS_FAIL)

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
        except ValueError:
            logger.exception("controller: invalid config for export")
            w.set_status("config error", _STATUS_FAIL)
            return
        w.set_status("exporting...", _STATUS_WARN)

        def work():
            store_cols, _ = self._mapping_pool(job)
            export_template(path, store_cols)
            return path

        worker = _NetworkWorker(work)
        worker.ok.connect(self._on_export_ok)
        worker.failed.connect(self._on_export_error)
        self._start_worker("_mapping_thread", worker, label="export")

    def _on_export_ok(self, path) -> None:
        self.window.set_status(f"template written: {path}", _STATUS_OK)

    def _on_export_error(self, exc) -> None:
        self.window.set_status("export error", _STATUS_FAIL)


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from store_sync.ui import theme

    app = QApplication.instance() or QApplication(sys.argv)
    theme.apply(app)
    window = MainWindow()
    controller = Controller(window)
    controller.start()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
