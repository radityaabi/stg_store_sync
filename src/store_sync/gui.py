"""PySide6 entry point: launch the STG Store Sync window.

Wires the stacked window (Dashboard / JobDetail / RunDetail) to the persisted
registry, the per-run run queue, and the interval scheduler:

* **Registry** (`core/registry.py`) — the job configs, same SQLite file as `State`.
* **RunQueue** (`core/run_queue.py`) — one thread per accepted run: concurrent
  across jobs, one run per job, never two runs against one store.
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
from datetime import datetime
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
)
from store_sync.core.registry import Registry  # noqa: E402
from store_sync.core.run_queue import RunQueue  # noqa: E402
from store_sync.core.scheduler import Scheduler  # noqa: E402
from store_sync.core import os_scheduler  # noqa: E402
from store_sync.core.stg_client import STGClient  # noqa: E402
from store_sync.core.state import State  # noqa: E402
from store_sync.core.timefmt import local_time  # noqa: E402
from store_sync.core.planner import Action  # noqa: E402
from store_sync.core.writer import apply_updates  # noqa: E402
from store_sync.core.sync import run_sync  # noqa: E402
from store_sync.ui.job_detail import _progress_line  # noqa: E402
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


def _fmt_counts(counts, *, dry_run: bool = False) -> str:
    """`{add: 1}` → `"add 1"`; a dry run's counts get a `(dry run)` suffix.

    Without the marker the History/counts cells look identical for a simulated
    and a real run, which misleads the operator into thinking writes happened.
    """
    if not counts:
        return "(dry run)" if dry_run else ""
    text = ", ".join(f"{k} {v}" for k, v in counts.items())
    return f"{text} (dry run)" if dry_run else text


def _short(exc: Exception) -> str:
    """A one-line, length-capped reason for a status-bar error message."""
    msg = " ".join(str(exc).split()) or exc.__class__.__name__
    return msg if len(msg) <= 90 else msg[:87] + "..."


def _action_from_proposal(p: dict) -> Action:
    """Rebuild the `Action` `writer.apply_updates` needs from a stored proposal."""
    return Action(
        "update",
        p["match_value"],
        "",
        p.get("id_value"),
        p.get("payload", {}),
        diffs=p.get("diff", []),
    )


def _run_log(run: dict) -> str:
    """A readable log for the Run detail console (stored log or a summary)."""
    lines = [
        f"run #{run.get('id')} · {run.get('status') or 'unknown'}",
        f"started:  {local_time(run.get('started_at')) or '-'}",
        f"finished: {local_time(run.get('finished_at')) or '-'}",
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


class _ProgressBridge(QObject):
    """Marshal `run_sync` progress events onto the GUI thread.

    `run_sync` calls its `progress` callback synchronously from the queue worker
    thread; emitting this signal delivers the event queued on the GUI thread so
    the JobDetail timeline can be updated safely.
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
        self._active_prev: set[str] = set()
        # Per-job live progress buffers: `{job: [event, ...]}`. The open job's
        # detail panel replays its own buffer; another job's run never bleeds in.
        self._progress_by_job: dict[str, list[dict]] = {}

        # In-flight off-thread network workers (pairing / mapping pool). The
        # `_pair_thread`/`_mapping_thread` attrs are "the current worker for
        # this slot" (status/busy checks); `_workers` is the strong-reference
        # container that guarantees a running QThread is never GC'd mid-run. Each
        # worker is discarded on its `finished` signal; all are joined in `stop()`.
        self._pair_thread: QThread | None = None
        self._mapping_thread: QThread | None = None
        self._apply_thread: QThread | None = None
        self._workers: set[QThread] = set()

        self._bridge = _QueueBridge()
        self._bridge.changed.connect(self._on_queue_changed)
        self.queue.subscribe(self._notify_queue)

        self._progress_bridge = _ProgressBridge()
        self._progress_bridge.changed.connect(self._on_run_progress)

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
        # A run row with a NULL status can only be left by a previous process
        # that died mid-run; mark it `interrupted` before starting fresh so the
        # run log never shows a perpetually running row.
        try:
            self._state().recover_orphan_runs()
        except Exception:  # noqa: BLE001 - recovery must never block startup
            logger.exception("orphan run recovery failed")
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
        self._join_worker("_pair_thread")
        self._join_worker("_mapping_thread")
        self._join_worker("_apply_thread")
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
        self._pair_thread = None
        self._mapping_thread = None
        self._apply_thread = None

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
        """Start `name` now; refuse (with a status) if it or its store is busy."""
        try:
            job = self.registry.get(name)
            store_key = str(job.store.id)
        except KeyError:
            return
        if not self.queue.enqueue(name, store_key=store_key):
            self.window.set_status(
                f"{name} not started (already running, or store {store_key} busy)",
                _STATUS_WARN,
            )

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
        self._auto_sync_os(name)
        self._refresh()
        return True

    def delete(self, name: str) -> None:
        try:
            self.registry.delete(name)
        except KeyError:
            return
        self._auto_sync_os(name)
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
                progress=self._emit_progress,
            )
        finally:
            if client is not None:
                self._close_client(client)

    def _client(self) -> STGClient:
        """Build a client from the environment, failing fast on missing vars.

        `STGClient.__init__` validates only `server`; a missing `user`/`password`
        would surface later as a confusing HTTP 401 from the worker thread. All
        three vars are checked here so the operator sees one actionable
        message ("set STG_SERVER/STG_USER/STG_PASSWORD in .env") instead of a
        traceback or a 401.
        """
        import os

        missing = [
            var
            for var in ("STG_SERVER", "STG_USER", "STG_PASSWORD")
            if not (os.environ.get(var) or "").strip()
        ]
        if missing:
            raise ValueError(
                f"missing {'/'.join(missing)} — set them in .env"
                " (copy .env.example)"
            )
        return STGClient(
            server=os.environ["STG_SERVER"],
            user=os.environ["STG_USER"],
            password=os.environ["STG_PASSWORD"],
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
                    match.source_var, match.source_var
                ))
            )
            return fixed
        return job

    # -- wiring ------------------------------------------------------------

    def _wire_signals(self) -> None:
        w = self.window
        w.new_requested.connect(self._new)
        w.run_requested.connect(self.run_now)
        w.stop_requested.connect(lambda: self.queue.stop_current())  # dashboard: all
        w.stop_job_requested.connect(lambda name: self.queue.stop_current(name))
        w.edit_requested.connect(self._open_job)
        w.delete_requested.connect(self._confirm_delete)
        w.save_requested.connect(self._save_from_detail)
        w.pair_requested.connect(self._pair_survey)
        w.automap_requested.connect(self._automap_columns)
        w.import_requested.connect(self._import_mapping)
        w.export_requested.connect(self._export_mapping)
        w.refresh_requested.connect(self._refresh)
        w.schedules_requested.connect(self._show_schedules)
        w.os_sync_requested.connect(self.sync_os_schedule)
        w.run_activated.connect(self._show_run)
        w.apply_proposals_requested.connect(self._apply_proposals)
        w.dismiss_proposals_requested.connect(self._dismiss_proposals)
        w.blacklist_add_requested.connect(self._blacklist_add)
        w.blacklist_remove_requested.connect(self._blacklist_remove)
        w.back.connect(w.show_dashboard)
        # Double-clicking a dashboard row opens (and loads) the job detail.
        w.dashboard.job_activated.connect(self._open_job)

    # -- queue / scheduler callbacks ---------------------------------------

    def _notify_queue(self, state) -> None:
        """Called by `RunQueue` (possibly off-thread); hop to the GUI thread."""
        self._bridge.changed.emit(state)

    def _emit_progress(self, event: dict) -> None:
        """`run_sync` progress callback (worker thread) → queued GUI signal."""
        self._progress_bridge.changed.emit(event)

    def _on_run_progress(self, event: dict) -> None:
        """Buffer one run event per job, and render only the open job's log.

        Events are keyed by their `job` (missing → the open job, for old
        callers/tests). A job's buffer is reset on its `start`; the panel shows
        only the open job's buffer, so job A's run never bleeds into job B's
        panel. The header status mirrors the stage (prefixed with the job) so
        progress is visible while another page is in front.
        """
        job = event.get("job") or self._detail_name or ""
        stage = event.get("stage", "")
        buf = self._progress_by_job.setdefault(job, [])
        if stage == "start":
            buf.clear()
        buf.append(event)
        if self._detail_name and job == self._detail_name:
            self.window.job_detail.set_run_progress(event)
        # Mirror the stage into the header status so the operator sees progress
        # even while another page (dashboard) is in front.
        prefix = f"{job} · " if job else ""
        if stage == "done":
            status = event.get("status") or "?"
            color = _STATUS_OK if status == "ok" else _STATUS_FAIL
            self.window.set_status(f"{prefix}run {status}", color)
        elif stage == "start":
            dry = " (dry run)" if event.get("dry_run") else ""
            self.window.set_status(f"running {job}{dry}".strip(), _STATUS_WARN)
        else:
            line = _progress_line(event).lstrip("· ").strip()
            if line:
                self.window.set_status(f"{prefix}{line}", _STATUS_WARN)

    def _on_queue_changed(self, state) -> None:
        self.window.dashboard.set_queue(state.active, state.pending)
        now_active = set(state.active)
        # Runs that just finished (this callback may report more than one) →
        # one toast each, plus a History refresh for the open job.
        for finished in sorted(self._active_prev - now_active):
            last = None
            try:
                runs = self.state.recent_runs(finished)
                last = runs[0] if runs else None
            except Exception:  # noqa: BLE001 - toast must not crash the slot
                logger.debug("could not read finished run", exc_info=True)
            if last:
                status = last.get("status") or "unknown"
                dry = bool(last.get("dry_run"))
                counts = _fmt_counts(last.get("counts"), dry_run=dry)
                kind = "ok" if status == "ok" else "fail"
                msg = f'"{finished}" finished: {status}'
                if dry:
                    msg += " — DRY RUN (no writes)"
                if counts:
                    msg += f" ({counts})"
                self.window.toast(msg, kind=kind)
                # The progress stream already set this, but a run that produced
                # no progress events (e.g. a crash) still needs a final status.
                self.window.set_status(
                    f"{finished}: {status}" + (" (dry run)" if dry else ""),
                    _STATUS_OK if status == "ok" else _STATUS_FAIL,
                )
        if self._detail_name and self._detail_name in (self._active_prev - now_active):
            self._load_history(self._detail_name)
        self._active_prev = now_active
        # The header mirrors live work last, so a finish never hides a run that
        # is still going (the operator's only cross-page indicator).
        if now_active:
            self.window.set_status(
                "running " + ", ".join(sorted(now_active)), _STATUS_WARN
            )
        else:
            self.window.set_status("ready", _STATUS_OK)
        self._refresh()

    def _on_due(self, name: str) -> None:
        # The OS task may have fired the same minute; claim it so only one runs.
        # `Scheduler.tick` already enqueued the job; this is UI feedback only.
        if not self.state.claim_due(name, self._minute_key()):
            return
        self.window.set_status(f"scheduled {name}", _STATUS_WARN)

    def _minute_key(self) -> str:
        """The cross-process minute key shared with `run-due` (spec §6)."""
        return datetime.now().strftime("%Y-%m-%dT%H:%M")

    def _auto_sync_os(self, name: str) -> None:
        """Register/unregister one job's OS task; never raise, never block."""
        try:
            entry = self.registry.get_entry(name)
        except KeyError:
            try:
                os_scheduler.remove(name)
            except Exception:  # noqa: BLE001 - schtasks/crontab may be absent
                logger.warning(
                    "os schedule remove failed for %r", name, exc_info=True
                )
            return
        try:
            if entry.enabled and os_scheduler.plan(entry.schedule) is not None:
                os_scheduler.add(name, entry.schedule)
            else:
                os_scheduler.remove(name)
        except Exception:  # noqa: BLE001 - never block/fail the save
            logger.warning("os schedule sync failed for %r", name, exc_info=True)
            self.window.set_status(
                "OS schedule not updated (register manually)", _STATUS_WARN
            )

    def sync_os_schedule(self) -> None:
        """Reconcile every job's OS task; report in the status bar."""
        try:
            msg = os_scheduler.sync(self.registry.list_entries())
        except Exception:  # noqa: BLE001 - schtasks/crontab may be absent
            logger.warning("os schedule sync failed", exc_info=True)
            self.window.set_status("OS schedule sync failed", _STATUS_FAIL)
            return
        self.window.set_status(msg, _STATUS_OK)
        self._refresh()

    def _os_status(self, entries) -> dict:
        """Best-effort per-job OS registration status for the dashboard badge."""
        try:
            return os_scheduler.status(entries)
        except Exception:  # noqa: BLE001
            logger.warning("os status failed", exc_info=True)
            return {}

    # -- refresh -----------------------------------------------------------

    def _refresh(self) -> None:
        if self._stopped:
            return
        entries = self.registry.list_entries()
        dash = self.window.dashboard
        dash.set_entries(entries, os_status=self._os_status(entries))

        snap = self.queue.snapshot()
        running = len(snap.active)
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
        self._replay_progress(name)
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
        self._replay_progress(name)
        self.window.show_job(name)

    def _replay_progress(self, name: str | None) -> None:
        """Show the open job's buffered live log (empty/hidden when none).

        Switching jobs must never show another job's log, so the panel is
        always reset from *this* job's buffer — even when that buffer is empty.
        """
        detail = self.window.job_detail
        detail.clear_run_progress()
        for event in self._progress_by_job.get(name or "", []):
            detail.set_run_progress(event)

    def _load_history(self, name: str | None) -> None:
        """Fill the open job's History + Conflicts tabs from `State`."""
        detail = self.window.job_detail
        if not name:
            detail.set_history([])
            detail.set_conflicts([])
            detail.set_proposals([])
            detail.set_blacklist([])
            return
        runs = self.state.recent_runs(name)
        detail.set_history(
            [
                {
                    "time": local_time(r.get("finished_at") or r.get("started_at")),
                    "status": r.get("status") or "",
                    "counts": _fmt_counts(r.get("counts"), dry_run=r.get("dry_run")),
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
        self._load_proposals(name)
        self._load_blacklist(name)

    def _load_blacklist(self, name: str) -> None:
        rows = self.state.list_blacklist(name)
        self.window.job_detail.set_blacklist(
            [
                {
                    "match": r.get("match_value", ""),
                    "reason": r.get("reason", ""),
                    "added": local_time(r.get("added_at")),
                }
                for r in rows
            ]
        )

    def _blacklist_add(self, key: str) -> None:
        name = self._detail_name
        if not name or not key:
            return
        self.state.blacklist_key(name, key, reason="manual")
        self._load_blacklist(name)
        self.window.set_status(f"blacklisted {key}", _STATUS_OK)

    def _blacklist_remove(self, keys: list) -> None:
        name = self._detail_name
        if not name or not keys:
            return
        for key in keys:
            self.state.unblacklist_key(name, key)
        self._load_blacklist(name)
        self.window.set_status(
            f"unblacklisted {len(keys)} key(s) — will re-add on next run", _STATUS_OK
        )

    def _load_proposals(self, name: str) -> None:
        """Fill the Proposed-updates tab with this job's pending proposals."""
        rows = self.state.pending_proposals(name)
        self.window.job_detail.set_proposals(
            [
                {
                    "id": p["id"],
                    "match_value": p.get("match_value", ""),
                    "id_value": p.get("id_value", ""),
                    "diff": p.get("diff", []),
                }
                for p in rows
            ]
        )

    def _show_run(self, run_id: int) -> None:
        """Load one run + its conflicts into the Run detail page."""
        run = self.state.get_run(int(run_id))
        if not run:
            return
        conflicts = self.state.conflicts_for_run(int(run_id))
        detail = self.window.run_detail
        detail.load(
            run, conflicts,
            notification=self.state.notification_for_run(int(run_id)),
        )
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
            source=SourceConfig(
                fields["survey_id"],
                survey_index=fields.get("survey_index", {}),
                paired_survey_id=fields.get("paired_survey_id", ""),
            ),
            binding=BindingConfig(
                match=ColumnBinding(match_source, match_store),
                columns=self.window.job_detail.bindings(),
            ),
            write=WriteConfig(
                dry_run=fields["dry_run"],
            ),
            pic=fields.get("pic", ""),
            notify_enabled=fields.get("notify_enabled", True),
        )

    def _save_from_detail(self) -> None:
        name = self._detail_name or ""
        if not name:
            self.window.set_status("save needs a name", _STATUS_FAIL)
            self.window.toast("Save needs a job name", kind="fail")
            return
        detail = self.window.job_detail
        try:
            job = self._job_from_detail()
        except ValueError:
            logger.exception("controller: invalid config for %r", name)
            self.window.set_status("config error", _STATUS_FAIL)
            self.window.toast("Config error — check the form fields", kind="fail")
            return
        if self.save(
            name,
            job,
            schedule=detail.schedule(),
            enabled=detail.enabled_check.isChecked(),
        ):
            self.window.set_status(f"saved {name}", _STATUS_OK)
            self.window.toast(f'"{name}" saved', kind="ok")
        else:
            self.window.set_status(f"save failed: {name}", _STATUS_FAIL)
            self.window.toast(f'Save failed: "{name}" — see log', kind="fail")

    # -- update proposals (manual approval, spec §6) -----------------------

    def _apply_proposals(self, ids: list) -> None:
        """Apply the ticked update proposals via compare-and-swap.

        Runs off the GUI thread (it re-reads the store). On success each
        proposal is marked `applied`; a stale one is marked `stale` and
        reported, never written.
        """
        name = self._detail_name
        if not name or not ids:
            return
        state = self.state
        proposals = []
        for pid in ids:
            p = state.get_proposal(int(pid))
            if p is not None:
                proposals.append(p)
        if not proposals:
            return
        w = self.window
        w.set_status(f"applying {len(proposals)} update(s)...", _STATUS_WARN)

        try:
            job = self.registry.get(name)
        except KeyError:
            w.set_status("config error", _STATUS_FAIL)
            w.toast("Config error — job not found", kind="fail")
            return
        id_column = job.store.id_column

        def work():
            client = self._client()
            try:
                actions = [_action_from_proposal(p) for p in proposals]
                res = apply_updates(
                    client, job.store.id, actions, id_column=id_column,
                    throttle=0.05,
                )
            finally:
                self._close_client(client)
            return res, proposals

        worker = _NetworkWorker(work)
        worker.ok.connect(self._on_apply_ok)
        worker.failed.connect(self._on_apply_error)
        self._start_worker("_apply_thread", worker, label="apply")

    def _on_apply_ok(self, result) -> None:
        res, proposals = result
        state = self.state
        applied_ids = {a.match_value for a in res.applied}
        stale_ids = {a.match_value for a in res.stale}
        for p in proposals:
            mv = p["match_value"]
            if mv in applied_ids:
                state.set_proposal_status(p["id"], "applied")
                state.track_row(
                    job=p["job"],
                    match_value=mv,
                    row_hash=state.row_hash(p["payload"]),
                )
            elif mv in stale_ids:
                state.set_proposal_status(p["id"], "stale")

        w = self.window
        n_ok, n_stale = len(res.applied), len(res.stale)
        if res.errors and not n_ok:
            w.set_status("apply failed", _STATUS_FAIL)
            w.toast(f"Apply failed — {res.errors[0]}", kind="fail")
        elif n_stale:
            w.set_status(f"applied {n_ok}, {n_stale} stale", _STATUS_WARN)
            w.toast(
                f"Applied {n_ok}; {n_stale} stale (store changed since proposal)",
                kind="warn",
            )
        else:
            w.set_status(f"applied {n_ok} update(s)", _STATUS_OK)
            w.toast(f"Applied {n_ok} update(s)", kind="ok")
        if self._detail_name:
            self._load_proposals(self._detail_name)

    def _on_apply_error(self, exc) -> None:
        msg = _short(exc)
        self.window.set_status(f"apply error: {msg}", _STATUS_FAIL)
        self.window.toast(f"Apply error: {msg}", kind="fail")

    def _dismiss_proposals(self, ids: list) -> None:
        """Mark the ticked proposals dismissed (no store write)."""
        state = self.state
        for pid in ids:
            state.set_proposal_status(int(pid), "dismissed")
        if self._detail_name:
            self._load_proposals(self._detail_name)
        self.window.set_status(f"dismissed {len(ids)} proposal(s)", _STATUS_OK)

    # -- survey pairing ----------------------------------------------------

    def _pair_survey(self) -> None:
        """Fetch the survey XML once, resolve each column's question base name.

        Runs off the GUI thread (the XML is a large download); on success the
        grid's `pull_term` column is filled and the index is stored for save.
        """
        from store_sync.core.fetcher import pair_columns, parse_survey_index

        w = self.window
        try:
            job = self._job_from_detail()
        except ValueError:
            logger.exception("controller: invalid config for pairing")
            w.set_status("config error", _STATUS_FAIL)
            return
        try:
            client = self._client()
        except Exception:  # noqa: BLE001 - surface any failure to operator
            logger.exception("controller: pairing client failed")
            w.set_status("pairing error", _STATUS_FAIL)
            w.toast("Pairing error: could not build STG client (check .env)",
                    kind="fail")
            return
        survey_id = job.source.survey_id
        w.set_status("pairing...", _STATUS_WARN)

        def work():
            # The worker performs the network fetch and closes its session.
            try:
                index = parse_survey_index(client.get_survey_xml(survey_id))
                terms, unresolved = pair_columns(
                    [c.source_var for c in job.binding.columns], index
                )
                return survey_id, index, terms, unresolved
            finally:
                self._close_client(client)

        worker = _NetworkWorker(work)
        worker.ok.connect(self._on_pair_ok)
        worker.failed.connect(self._on_pair_error)
        self._start_worker("_pair_thread", worker, label="pairing")

    def _on_pair_ok(self, result) -> None:
        survey_id, index, terms, unresolved = result
        self.window.job_detail.set_pairing(survey_id, index, terms)
        if unresolved:
            self.window.set_status(
                f"paired — {len(unresolved)} unresolved", _STATUS_WARN
            )
            self.window.toast(
                f"Paired, but unresolved: {', '.join(unresolved)}", kind="warn"
            )
        else:
            self.window.set_status("paired", _STATUS_OK)
            self.window.toast("Survey paired — save to persist", kind="ok")

    def _on_pair_error(self, exc) -> None:
        msg = _short(exc)
        self.window.set_status(f"pairing error: {msg}", _STATUS_FAIL)
        self.window.toast(f"Pairing error: {msg}", kind="fail")

    # -- one-click column auto-map ----------------------------------------

    def _automap_columns(self) -> None:
        """Fill the column grid from the store's columns + the survey index.

        One click replaces the manual export/fill/import/pair loop: the store's
        column list comes from a single `GetStore`, the source var for each
        column from the survey index (cached pairing, else one XML fetch — the
        pairing is then stored implicitly). `core.automap.auto_map` does the
        matching: exact name, then a question base name with exactly one answer
        var; anything ambiguous is left unmapped and reported rather than
        guessed. Nothing is saved here — the operator reviews the grid and hits
        Save, exactly like a manual edit.
        """
        from store_sync.core.automap import auto_map
        from store_sync.core.fetcher import parse_survey_index
        from store_sync.core.stg_client import STGError

        w = self.window
        detail = w.job_detail
        try:
            job = self._job_from_detail()
        except ValueError:
            logger.exception("controller: invalid config for auto-map")
            w.set_status("config error", _STATUS_FAIL)
            w.toast("Config error — check the form fields", kind="fail")
            return
        try:
            client = self._client()
        except Exception:  # noqa: BLE001 - surface any failure to operator
            logger.exception("controller: auto-map client failed")
            w.set_status("auto-map error", _STATUS_FAIL)
            w.toast("Auto-map error: could not build STG client (check .env)",
                    kind="fail")
            return

        survey_id = job.source.survey_id
        cached_index = dict(job.source.survey_index)
        cached = (
            cached_index
            if cached_index and job.source.paired_survey_id == survey_id
            else None
        )
        existing = detail.bindings()
        match = job.binding.match
        skip = {
            job.store.id_column,
            match.store_col if match else "",
            match.source_var if match else "",
        }
        w.set_status("auto-mapping columns...", _STATUS_WARN)

        def work():
            try:
                store_cols = self._store_cols_of(client, job)
                index = cached if cached is not None else parse_survey_index(
                    client.get_survey_xml(survey_id)
                )
            finally:
                self._close_client(client)
            if not store_cols:
                raise STGError("store has no columns (empty store?)")
            return (
                survey_id,
                index,
                auto_map(store_cols, index, skip=skip, existing=existing),
            )

        worker = _NetworkWorker(work)
        worker.ok.connect(self._on_automap_ok)
        worker.failed.connect(self._on_automap_error)
        self._start_worker("_mapping_thread", worker, label="auto-map")

    def _on_automap_ok(self, result) -> None:
        survey_id, index, res = result
        w = self.window
        detail = w.job_detail

        merge = True
        if detail.bindings():
            answer = QMessageBox.question(
                w,
                "Auto-map columns",
                f"The Columns tab already has {len(detail.bindings())} binding(s).\n\n"
                f"Merge — keep existing source vars, add/fill the rest\n"
                f"Replace — rebuild the grid from the store + survey\n"
                f"Cancel — leave the grid untouched\n\n"
                f"Auto-map found {res.mapped}/{res.total} column(s).",
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes,
            )
            if answer == QMessageBox.Cancel:
                w.set_status("auto-map cancelled", _STATUS_WARN)
                return
            merge = answer == QMessageBox.Yes

        if not merge:
            # Rebuild from scratch: the previous grid is discarded entirely.
            from store_sync.core.automap import auto_map

            res = auto_map(
                [c.store_col for c in res.columns], index, merge=False
            )

        detail.binding_model.set_columns(res.columns)
        detail.set_pairing(
            survey_id, index, {c.source_var: c.pull_term for c in res.columns}
        )

        if res.unmapped:
            w.set_status(
                f"auto-mapped {res.mapped}/{res.total} · {len(res.unmapped)} unmapped",
                _STATUS_WARN,
            )
            shown = ", ".join(res.unmapped[:6])
            more = "" if len(res.unmapped) <= 6 else f" +{len(res.unmapped) - 6} more"
            w.toast(
                f"Auto-mapped {res.mapped}/{res.total}. No source var for: "
                f"{shown}{more} — fill by hand, then Save",
                kind="warn",
            )
        else:
            w.set_status(f"auto-mapped {res.mapped}/{res.total}", _STATUS_OK)
            w.toast("Columns auto-mapped — review, then Save", kind="ok")

    def _on_automap_error(self, exc) -> None:
        msg = _short(exc)
        self.window.set_status(f"auto-map error: {msg}", _STATUS_FAIL)
        self.window.toast(f"Auto-map error: {msg}", kind="fail")

    # -- mapping import / export -------------------------------------------

    def _store_pool(self, job: JobConfig) -> list[str]:
        """Fetch just the store's column names (GetStore only — fast).

        Used by the import path: the survey XML (the only source of the
        source-var list) is a 30 s+ download for large surveys, so the import
        validates store columns only; a source var is validated against a live
        pull by the run's own readiness gate, which must pass before any write.
        """
        client = None
        try:
            client = self._client()
            return self._store_cols_of(client, job)
        finally:
            if client is not None:
                self._close_client(client)

    @staticmethod
    def _store_cols_of(client, job: JobConfig) -> list[str]:
        """Store column names in the store's own order (first-seen key order).

        JSON row objects preserve key order, so the first row's key order is
        the store's column order; later rows only append columns the first row
        lacked. An empty store yields [] — callers must reject that (no
        columns to map, template would be header-only).
        """
        cols: dict[str, None] = {}  # dict as an insertion-ordered set
        for r in client.get_store(job.store.id):
            for k in r:
                cols.setdefault(k)
        return list(cols)

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
            # Fast path: GetStore only. Source vars are NOT validated here —
            # the var list costs a full survey-XML download; the run's readiness
            # gate (the mandatory pre-write gate) validates them on a live pull.
            store_cols = self._store_pool(job)
            return parse_mapping(
                path,
                store_cols,
                valid_source_vars=None,
                id_column=job.store.id_column,
            )

        worker = _NetworkWorker(work)
        worker.ok.connect(self._on_import_ok)
        worker.failed.connect(self._on_import_error)
        self._start_worker("_mapping_thread", worker, label="import")

    def _on_import_ok(self, result) -> None:
        w = self.window
        parsed, errors = result
        if errors:
            logger.warning("mapping import rejected: %s", "; ".join(errors))
            # Surface the first reason in the status bar — a bare
            # "import rejected" hides the fixable cause from the operator.
            reason = errors[0]
            if len(reason) > 80:
                reason = reason[:77] + "..."
            w.set_status(f"import rejected: {reason}", _STATUS_FAIL)
            w.toast(f"Import rejected — {reason}", kind="fail")
            return
        w.job_detail.binding_model.set_columns(parsed)
        w.set_status(f"imported {len(parsed)} column(s)", _STATUS_WARN)
        w.toast(
            f"Imported {len(parsed)} column(s) — save to persist",
            kind="ok",
        )

    def _on_import_error(self, exc) -> None:
        msg = _short(exc)
        self.window.set_status(f"import error: {msg}", _STATUS_FAIL)
        self.window.toast(f"Import error: {msg}", kind="fail")

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
            w.toast("Config error — check the form fields", kind="fail")
            return
        w.set_status("exporting...", _STATUS_WARN)

        def work():
            store_cols = self._store_pool(job)
            if not store_cols:
                # An empty store has no columns to map; a header-only
                # template would silently mislead the operator.
                raise ValueError("store has no columns (empty store?)")
            export_template(path, store_cols)
            return path

        worker = _NetworkWorker(work)
        worker.ok.connect(self._on_export_ok)
        worker.failed.connect(self._on_export_error)
        self._start_worker("_mapping_thread", worker, label="export")

    def _on_export_ok(self, path) -> None:
        self.window.set_status(f"template written: {path}", _STATUS_OK)
        self.window.toast(f"Template written: {path}", kind="ok")

    def _on_export_error(self, exc) -> None:
        msg = _short(exc)
        self.window.set_status(f"export error: {msg}", _STATUS_FAIL)
        self.window.toast(f"Export error: {msg}", kind="fail")


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
