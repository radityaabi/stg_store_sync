"""Headless entry point: run one sync job from a YAML file.

    stg-store-sync-cli job.yaml [--state PATH] [--no-dry-run] [--json]

Credentials come from `.env` (STG_SERVER/STG_USER/STG_PASSWORD). A `filelock`
guards against two runs of the same job racing, and every run is recorded in
the SQLite state (audit log).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from filelock import FileLock, Timeout

from store_sync.core.config import load_config
from store_sync.core.stg_client import STGClient
from store_sync.core.state import State
from store_sync.core.sync import run_sync

DEFAULT_STATE = "state.db"


def _exit_for(status: str) -> int:
    """Process exit code for a run status (spec §7): blocked is its own code."""
    if status == "ok":
        return 0
    if status == "blocked":
        return 2
    return 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stg-store-sync-cli",
        description="Run one STG store sync job headlessly.",
    )
    p.add_argument("job_file", help="path to a job YAML file")
    p.add_argument("--state", dest="state_path", default=DEFAULT_STATE,
                   help=f"SQLite state path (default: {DEFAULT_STATE})")
    p.add_argument("--no-dry-run", action="store_true",
                   help="override the job's dry_run flag and actually write")
    p.add_argument("--json", action="store_true", help="print the result as JSON")
    p.add_argument("--throttle", type=float, default=0.05,
                   help="seconds between writes (default 0.05)")
    return p


def load_job_file(path):
    return load_config(path)


def _acquire_lock(lock_path: str) -> FileLock:
    return FileLock(lock_path, timeout=0)


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    args = build_parser().parse_args(argv)

    try:
        job = load_job_file(args.job_file)
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.no_dry_run:
        job.write.dry_run = False

    server = os.environ.get("STG_SERVER", "")
    user = os.environ.get("STG_USER", "")
    password = os.environ.get("STG_PASSWORD", "")
    if not (server and user and password):
        print("error: STG_SERVER/STG_USER/STG_PASSWORD must be set", file=sys.stderr)
        return 2

    lock = _acquire_lock(args.job_file + ".lock")
    try:
        lock.acquire()
    except Timeout:
        print(f"error: another run holds the lock for {args.job_file}", file=sys.stderr)
        return 3

    try:
        client = STGClient(server=server, user=user, password=password)
        state = State(args.state_path)
        try:
            res = run_sync(job, client=client, state=state, throttle=args.throttle)
        finally:
            state.close()
    finally:
        lock.release()

    if args.json:
        print(json.dumps(
            {
                "job": job.job,
                "dry_run": res.dry_run,
                "status": res.status,
                "counts": res.counts,
                "errors": res.errors,
            },
            indent=2,
        ))
    else:
        print(f"{job.job}: {res.status} (dry_run={res.dry_run}) {res.counts}")
        for e in res.errors:
            print(f"  ! {e}", file=sys.stderr)

    return _exit_for(res.status)


if __name__ == "__main__":
    sys.exit(main())
