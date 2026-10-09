"""Headless entry point: registry-backed sync subcommands (spec §7).

    stg-store-sync-cli list
    stg-store-sync-cli run <name> [--dry-run/--no-dry-run] [--json]
    stg-store-sync-cli run-all [--enabled-only] [--json]
    stg-store-sync-cli stop <name>

The GUI is authoritative for stored config; the CLI reads the same SQLite
registry (`STG_STORE_SYNC_DB`, default `~/.stg_store_sync/state.db`) and runs the
same `run_sync` path. Credentials come from `.env`
(STG_SERVER/STG_USER/STG_PASSWORD). A `filelock` guards against two runs of the
same job racing, and every run is recorded in the SQLite state (audit log).

Exit codes (spec §7): `0` ok · `2` blocked (preflight) · `3` partial/error ·
`1` usage. The §3.3 readiness gate lives inside `run_sync` (before any write) and
surfaces as `status == "blocked"`; the CLI does not call `run_preflight`
separately.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from filelock import FileLock, Timeout

from store_sync.core.config import human, load_config
from store_sync.core.registry import Registry
from store_sync.core.stg_client import STGClient
from store_sync.core.state import State
from store_sync.core.sync import run_sync

DEFAULT_STATE = "state.db"


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

    run_all = sub.add_parser("run-all", help="run every registered job")
    run_all.add_argument(
        "--enabled-only", action="store_true",
        help="only run jobs whose scheduler flag is enabled",
    )
    run_all.add_argument("--json", action="store_true", help="print each result as JSON")

    stop = sub.add_parser("stop", help="request a running job stop (in-app only)")
    stop.add_argument("name", help="registered job name")

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


def _run_one(reg: Registry, state: State, client, name: str, dry_run, *, as_json: bool):
    """Load, run, and report one job. Returns `(exit_code, result)`."""
    try:
        job = reg.get(name)
    except KeyError:
        print(f"error: no such job: {name!r}", file=sys.stderr)
        return 3, None

    if dry_run is not None:
        job.write.dry_run = dry_run

    res = run_sync(job, client=client, state=state, throttle=0.05)
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

        code, _ = _run_one(reg, state, client, args.name, args.dry_run, as_json=args.json)
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
                    reg, state, client, name, None, as_json=args.json
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
    load_dotenv()
    args = build_parser().parse_args(argv)
    db = _db_path()

    if args.command == "list":
        return _cmd_list(db, args)
    if args.command == "run":
        return _cmd_run(db, args)
    if args.command == "run-all":
        return _cmd_run_all(db, args)
    if args.command == "stop":
        return _cmd_stop(db, args)
    return 1  # unreachable: subparsers are required


if __name__ == "__main__":
    sys.exit(main())
