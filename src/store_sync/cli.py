"""Headless entry point: registry-backed sync subcommands (spec §7).

    stg-store-sync-cli list
    stg-store-sync-cli run <name> [--dry-run/--no-dry-run] [--no-notify] [--json]
    stg-store-sync-cli run-all [--enabled-only] [--no-notify] [--json]
    stg-store-sync-cli run-due [--job <name>] [--no-notify] [--json]
    stg-store-sync-cli send-report <name> [--run-id N] [--dry-run --out DIR]
                                  [--force] [--json]
    stg-store-sync-cli stop <name>

The GUI is authoritative for stored config; the CLI reads the same SQLite
registry (`STG_STORE_SYNC_DB`, default `~/.stg_store_sync/state.db`) and runs the
same `run_sync` path. Credentials come from `.env`
(STG_SERVER/STG_USER/STG_PASSWORD). A `filelock` guards against two runs of the
same job racing, and every run is recorded in the SQLite state (audit log).

Exit codes (spec §7): `0` ok · `2` blocked (preflight) · `3` partial/error ·
`1` usage. The §3.3 readiness gate (plus the §4.2.1 id-column probe on live
adds) lives inside `run_sync` before any write and surfaces as
`status == "blocked"`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from filelock import FileLock, Timeout

from store_sync.core.config import human, load_config
from store_sync.core.notify import notify_run_report
from store_sync.core.os_scheduler import (
    add as os_add,
    plan as os_plan,
    remove as os_remove,
    status as os_status,
    sync as os_sync,
)
from store_sync.core.planner import Plan
from store_sync.core.registry import Registry
from store_sync.core.scheduler import Scheduler
from store_sync.core.stg_client import STGClient
from store_sync.core.state import State
from store_sync.core.sync import SyncResult, run_sync

DEFAULT_STATE = "state.db"


def _now() -> datetime:
    """Clock hook (monkeypatched in tests)."""
    return datetime.now()


def _load_env() -> None:
    """Load `.env` independent of CWD, then fall back to a CWD search.

    An OS task starts in an arbitrary directory, so `load_dotenv()`'s upward
    search finds nothing. The project-root `.env` is loaded explicitly first;
    already-set environment variables win (`override=False`).
    """
    root = Path(__file__).resolve().parents[2]  # src/store_sync/cli.py → repo root
    project_env = root / ".env"
    if project_env.is_file():
        load_dotenv(project_env, override=False)
    else:
        load_dotenv(find_dotenv(), override=False)


class _FilteredRegistry:
    """Registry view restricted to a set of names (or all when `names` is None)."""

    def __init__(self, reg, names: list[str] | None):
        self._reg = reg
        self._names = names

    def list_entries(self):
        entries = self._reg.list_entries()
        if self._names is None:
            return entries
        return [e for e in entries if e.name in self._names]


def _exit_for(status: str) -> int:
    """Process exit code for a run status (spec §7)."""
    if status == "ok":
        return 0
    if status == "blocked":
        return 2
    return 3


class _Parser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors exit `1` (spec §7), not argparse's 2."""

    def error(self, message: str):  # noqa: D401 - argparse hook
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="stg-store-sync-cli",
        description="Run registered STG store sync jobs headlessly.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="print registered jobs")

    run = sub.add_parser("run", help="run one registered job")
    run.add_argument("name", help="registered job name")
    run.add_argument(
        "--dry-run", dest="dry_run", action="store_true", default=None,
        help="override the job's dry_run flag and only simulate",
    )
    run.add_argument(
        "--no-dry-run", dest="dry_run", action="store_false",
        help="override the job's dry_run flag and actually write",
    )
    run.add_argument("--json", action="store_true", help="print the result as JSON")
    run.add_argument(
        "--no-notify", action="store_true",
        help="skip the PIC run-notification email for this run",
    )

    run_all = sub.add_parser("run-all", help="run every registered job")
    run_all.add_argument(
        "--enabled-only", action="store_true",
        help="only run jobs whose scheduler flag is enabled",
    )
    run_all.add_argument("--json", action="store_true", help="print each result as JSON")
    run_all.add_argument(
        "--no-notify", action="store_true",
        help="skip the PIC run-notification email for every run",
    )

    stop = sub.add_parser("stop", help="request a running job stop (in-app only)")
    stop.add_argument("name", help="registered job name")

    bl = sub.add_parser("blacklist", help="list a job's suppressed keys")
    bl.add_argument("name", help="registered job name")

    bla = sub.add_parser("blacklist-add", help="suppress a key for a job")
    bla.add_argument("name", help="registered job name")
    bla.add_argument("key", help="match-key value to suppress")

    blr = sub.add_parser("blacklist-remove", help="unsuppress a key for a job")
    blr.add_argument("name", help="registered job name")
    blr.add_argument("key", help="match-key value to unsuppress")

    rd = sub.add_parser("run-due", help="run every enabled job that is due now")
    rd.add_argument("--job", help="restrict to one job (runs only if due)")
    rd.add_argument("--json", action="store_true", help="machine-readable output")
    rd.add_argument(
        "--no-notify", action="store_true",
        help="skip the PIC run-notification email for every run",
    )

    sr = sub.add_parser(
        "send-report", help="re-send a finished run's report to the job's PIC"
    )
    sr.add_argument("name", help="registered job name")
    sr.add_argument(
        "--run-id", type=int, default=None, help="run id (default: the newest run)"
    )
    sr.add_argument(
        "--dry-run", action="store_true",
        help="render the report and write it out, but do not send",
    )
    sr.add_argument("--out", default=None, help="directory for the --dry-run HTML")
    sr.add_argument("--force", action="store_true", help="send even if already sent")
    sr.add_argument("--json", action="store_true", help="machine-readable output")

    sch = sub.add_parser("schedule", help="manage OS-level schedule tasks")
    sch_sub = sch.add_subparsers(dest="schedule_command", required=True)
    sch_sub.add_parser("list", help="show each job's OS registration status")
    sch_add = sch_sub.add_parser("add", help="register one job's OS task")
    sch_add.add_argument("name")
    sch_rem = sch_sub.add_parser("remove", help="unregister one job's OS task")
    sch_rem.add_argument("name")
    sch_sub.add_parser("sync", help="reconcile all jobs' OS tasks")

    return p


def load_job_file(path):
    """Back-compat: load a job from a YAML file (superseded by the registry)."""
    return load_config(path)


def _acquire_lock(lock_path: str) -> FileLock:
    return FileLock(lock_path, timeout=0)


def _db_path() -> Path:
    env = os.environ.get("STG_STORE_SYNC_DB")
    if env:
        return Path(env)
    return Path.home() / ".stg_store_sync" / "state.db"


def _client_from_env() -> STGClient | None:
    server = os.environ.get("STG_SERVER", "")
    user = os.environ.get("STG_USER", "")
    password = os.environ.get("STG_PASSWORD", "")
    if not (server and user and password):
        return None
    return STGClient(server=server, user=user, password=password)


def _close_client(client) -> None:
    session = getattr(client, "session", None)
    if session is not None:
        try:
            session.close()
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass


def _status_line(name: str, res) -> str:
    base = f"{name}: {res.status} (dry_run={res.dry_run}) {res.counts}"
    if getattr(res, "proposals", 0):
        base += f" · {res.proposals} update proposal(s) pending review"
    if getattr(res, "suppressed", 0):
        base += f" · {res.suppressed} suppressed (blacklisted)"
    report = getattr(res, "report_status", "")
    if report:
        base += f" · report={report}"
    return base


def _emit(name: str, res, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(
            {
                "job": name,
                "dry_run": res.dry_run,
                "status": res.status,
                "counts": res.counts,
                "proposals": getattr(res, "proposals", 0),
                "suppressed": getattr(res, "suppressed", 0),
                "report_status": getattr(res, "report_status", ""),
                "errors": res.errors,
            },
            indent=2,
        ))
    else:
        print(_status_line(name, res))
        for e in res.errors:
            print(f"  ! {e}", file=sys.stderr)


def _cmd_list(db: Path, args) -> int:
    reg = Registry(db)
    try:
        entries = reg.list_entries()
    finally:
        reg.close()
    for e in entries:
        schedule = human(e.schedule)
        state = "enabled" if e.enabled else "disabled"
        print(f"{e.name} · store {e.store_id} · {schedule} · {state}")
    return 0


def _run_one(reg: Registry, state: State, client, name: str, dry_run, *,
             as_json: bool, notify: bool = True):
    """Load, run, and report one job. Returns `(exit_code, result)`."""
    try:
        job = reg.get(name)
    except KeyError:
        print(f"error: no such job: {name!r}", file=sys.stderr)
        return 3, None

    if dry_run is not None:
        job.write.dry_run = dry_run

    res = run_sync(job, client=client, state=state, throttle=0.05, notify=notify)
    # The notification is recorded, not returned: read it back for display.
    row = state.notification_for_run(res.run_id) if res.run_id else None
    res.report_status = (row or {}).get("status", "")
    _emit(name, res, as_json=as_json)
    return _exit_for(res.status), res


def _cmd_run(db: Path, args) -> int:
    reg = Registry(db)
    state = State(db)
    lock = None
    client = None
    try:
        # unknown job is detected before touching credentials
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3

        lock = _acquire_lock(f"{db}.{args.name}.lock")
        try:
            lock.acquire()
        except Timeout:
            print(
                f"error: another run holds the lock for {args.name}", file=sys.stderr
            )
            return 3

        client = _client_from_env()
        if client is None:
            print(
                "error: STG_SERVER/STG_USER/STG_PASSWORD must be set", file=sys.stderr
            )
            return 1

        code, _ = _run_one(reg, state, client, args.name, args.dry_run,
                           as_json=args.json, notify=not args.no_notify)
        return code
    finally:
        if client is not None:
            _close_client(client)
        if lock is not None:
            lock.release()
        state.close()
        reg.close()


def _worst_code(codes: list[int]) -> int:
    if 2 in codes:
        return 2
    if 3 in codes:
        return 3
    if 1 in codes:
        return 1
    return 0


def _cmd_run_all(db: Path, args) -> int:
    reg = Registry(db)
    state = State(db)
    client = None
    try:
        entries = reg.list_entries()
        names = [e.name for e in entries if (e.enabled or not args.enabled_only)]
        if args.enabled_only and not names:
            print("no enabled jobs")
            return 0

        client = _client_from_env()
        if client is None:
            print(
                "error: STG_SERVER/STG_USER/STG_PASSWORD must be set", file=sys.stderr
            )
            return 1

        codes: list[int] = []
        for name in names:
            lock = _acquire_lock(f"{db}.{name}.lock")
            try:
                try:
                    lock.acquire()
                except Timeout:
                    print(
                        f"error: another run holds the lock for {name}",
                        file=sys.stderr,
                    )
                    codes.append(3)
                    continue
                code, _ = _run_one(
                    reg, state, client, name, None, as_json=args.json,
                    notify=not args.no_notify,
                )
                codes.append(code)
            finally:
                if lock.is_locked:
                    lock.release()
        return _worst_code(codes)
    finally:
        if client is not None:
            _close_client(client)
        state.close()
        reg.close()


def _cmd_run_due(db: Path, args) -> int:
    """Run every enabled job that is due now (the OS-task wake-up target)."""
    reg = Registry(db)
    state = State(db)
    client = None
    try:
        if args.job and args.job not in reg.names():
            print(f"error: no such job: {args.job!r}", file=sys.stderr)
            return 3

        names = [args.job] if args.job else None
        collected: list[str] = []
        scheduler = Scheduler(
            _FilteredRegistry(reg, names),
            lambda name, **kw: collected.append(name),
            now_fn=_now,
        )
        scheduler.tick()

        # Drop anything already claimed this minute (a GUI fire may have run it).
        now = _now()
        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        due = [n for n in collected if state.claim_due(n, minute_key)]
        state.prune_os_fired(
            (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
        )
        if not due:
            print("no jobs due")
            return 0

        client = _client_from_env()
        if client is None:
            print(
                "error: STG_SERVER/STG_USER/STG_PASSWORD must be set", file=sys.stderr
            )
            return 1

        codes: list[int] = []
        fired: list[dict] = []
        for name in due:
            lock = _acquire_lock(f"{db}.{name}.lock")
            try:
                try:
                    lock.acquire()
                except Timeout:
                    print(
                        f"error: another run holds the lock for {name}",
                        file=sys.stderr,
                    )
                    codes.append(3)
                    continue
                code, res = _run_one(reg, state, client, name, None,
                                     as_json=args.json,
                                     notify=not args.no_notify)
                codes.append(code)
                fired.append({"job": name, "status": getattr(res, "status", None)})
            finally:
                if lock.is_locked:
                    lock.release()
        if args.json:
            print(json.dumps({"fired": fired}, indent=2))
        return _worst_code(codes)
    finally:
        if client is not None:
            _close_client(client)
        state.close()
        reg.close()


def _cmd_schedule_list(db: Path, args) -> int:
    reg = Registry(db)
    try:
        entries = reg.list_entries()
        st = os_status(entries)
    finally:
        reg.close()
    for e in entries:
        print(f"{e.name} · {human(e.schedule)} · OS: {st.get(e.name, '?')}")
    return 0


def _cmd_schedule_add(db: Path, args) -> int:
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
        entry = reg.get_entry(args.name)
    finally:
        reg.close()
    if not entry.enabled or os_plan(entry.schedule) is None:
        print(
            f"error: {args.name!r} is disabled or manual-only — nothing to register",
            file=sys.stderr,
        )
        return 3
    print(os_add(args.name, entry.schedule))
    return 0


def _cmd_schedule_remove(db: Path, args) -> int:
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
    finally:
        reg.close()
    print(os_remove(args.name))
    return 0


def _cmd_schedule_sync(db: Path, args) -> int:
    reg = Registry(db)
    try:
        entries = reg.list_entries()
    finally:
        reg.close()
    print(os_sync(entries))
    return 0


def _cmd_blacklist(db: Path, args) -> int:
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
    finally:
        reg.close()
    state = State(db)
    try:
        for row in state.list_blacklist(args.name):
            print(f"{row['match_value']} · {row['reason']}")
    finally:
        state.close()
    return 0


def _cmd_blacklist_add(db: Path, args) -> int:
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
    finally:
        reg.close()
    state = State(db)
    try:
        state.blacklist_key(args.name, args.key, reason="manual")
    finally:
        state.close()
    print(f"{args.name}: blacklisted {args.key}")
    return 0


def _cmd_blacklist_remove(db: Path, args) -> int:
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
    finally:
        reg.close()
    state = State(db)
    try:
        state.unblacklist_key(args.name, args.key)
    finally:
        state.close()
    print(f"{args.name}: unblacklisted {args.key}")
    return 0


def _cmd_send_report(db: Path, args) -> int:
    """Re-send (or rehearse) one finished run's report to the job's PIC.

    Exit codes: `0` sent / skipped / dry-run, `3` unknown job or no such run,
    `1` the transport failed. A skip is never an error — the job may simply
    have no PIC, or the report may already have gone out.
    """
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
        job = reg.get(args.name)
    finally:
        reg.close()

    if args.dry_run and not args.out:
        print("error: --dry-run requires --out DIR", file=sys.stderr)
        return 1

    state = State(db)
    try:
        run = None
        if args.run_id is not None:
            run = state.get_run(args.run_id)
            if run is None or run["job"] != args.name:
                print(f"error: no such run: {args.run_id}", file=sys.stderr)
                return 3
        else:
            runs = state.recent_runs(args.name, limit=1)
            if not runs:
                print(f"error: {args.name!r} has no runs to report", file=sys.stderr)
                return 3
            run = runs[0]

        # Rebuild the minimum a report needs from the stored run row.
        result = SyncResult(
            plan=Plan(),
            dry_run=bool(args.dry_run) or bool(run["dry_run"]),
            status=run["status"] or "ok",
            counts=run["counts"] or {},
            run_id=run["id"],
            finished_at=run.get("finished_at"),
        )
        # A re-send uses the *stored* body so it is byte-identical to the
        # original (spec §5.2) — the run's added rows are long gone.
        stored = state.notification_for_run(run["id"]) or {}
        outcome = notify_run_report(
            job, state, result,
            force=args.force,
            out_dir=args.out,
            subject=stored.get("subject") or None,
            body_html=stored.get("body_html") or None,
        )
        if args.json:
            print(json.dumps(
                {
                    "job": args.name,
                    "run_id": run["id"],
                    "report_status": outcome.status,
                    "reason": outcome.reason,
                    "to": list(outcome.to),
                },
                indent=2,
            ))
        else:
            line = f"{args.name} run {run['id']}: report={outcome.status}"
            if outcome.reason:
                line += f" · {outcome.reason}"
            print(line)
        return 0 if outcome.status in ("sent", "skipped", "dry_run") else 1
    finally:
        state.close()


def _cmd_stop(db: Path, args) -> int:
    reg = Registry(db)
    try:
        if args.name not in reg.names():
            print(f"error: no such job: {args.name!r}", file=sys.stderr)
            return 3
    finally:
        reg.close()
    print(
        f"{args.name}: the run queue is in-app; a headless CLI cannot signal it"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    _load_env()
    args = build_parser().parse_args(argv)
    db = _db_path()

    if args.command == "list":
        return _cmd_list(db, args)
    if args.command == "run":
        return _cmd_run(db, args)
    if args.command == "run-all":
        return _cmd_run_all(db, args)
    if args.command == "run-due":
        return _cmd_run_due(db, args)
    if args.command == "schedule":
        return {
            "list": _cmd_schedule_list,
            "add": _cmd_schedule_add,
            "remove": _cmd_schedule_remove,
            "sync": _cmd_schedule_sync,
        }[args.schedule_command](db, args)
    if args.command == "stop":
        return _cmd_stop(db, args)
    if args.command == "send-report":
        return _cmd_send_report(db, args)
    if args.command == "blacklist":
        return _cmd_blacklist(db, args)
    if args.command == "blacklist-add":
        return _cmd_blacklist_add(db, args)
    if args.command == "blacklist-remove":
        return _cmd_blacklist_remove(db, args)
    return 1  # unreachable: subparsers are required


if __name__ == "__main__":
    sys.exit(main())
