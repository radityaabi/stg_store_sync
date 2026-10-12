"""OS-level scheduler registration — one OS task per enabled, scheduled job.

The OS task is a coarse *wake-up*; the precise decision ("is this job due
now?") is always `Scheduler.tick()` / `is_due()`. `plan` maps a `Schedule` to
a `Trigger`; the platform backends translate a `Trigger` into `schtasks`
arguments (Windows) or a crontab line (Linux). All OS calls go through
module-level helpers so tests stub them and never touch the real OS.

Windows is the production target; Linux is supported; macOS is unsupported
(explicit message, never a silent no-op).
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from store_sync.core.config import Schedule


@dataclass(frozen=True)
class Trigger:
    """A coarse OS wake-up derived from a `Schedule` (see spec §4.2)."""

    unit: str
    every: int
    at: str = ""
    weekday: int = 0


def plan(schedule: Schedule) -> Trigger | None:
    """Map `schedule` to an OS `Trigger`, or None when manual-only/malformed.

    A `Schedule` that `validate()` rejects never fires in `is_due`, so it must
    never create an OS task either.
    """
    if not isinstance(schedule, Schedule):
        return None
    if schedule.validate():
        return None
    if schedule.unit == "":
        return None
    return Trigger(
        unit=schedule.unit,
        every=int(schedule.every),
        at=schedule.at,
        weekday=int(schedule.weekday),
    )


# ---------------------------------------------------------------------------
# Platform detection + the registered command (stubbable in tests)
# ---------------------------------------------------------------------------

TASK_PREFIX = "StgStoreSync_"
_CRON_SENTINEL = "# stg-store-sync:{job}"
_WD = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _is_windows() -> bool:
    return sys.platform in ("win32", "cygwin")


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _venv_python() -> str:
    """The project venv interpreter, preferring `pythonw.exe` on Windows.

    Derived from the package location so the registered command does not depend
    on the caller's CWD or PATH.
    """
    root = Path(__file__).resolve().parents[3]
    if _is_windows():
        exe = root / ".venv" / "Scripts" / "pythonw.exe"
    else:
        exe = root / ".venv" / "bin" / "python"
    return str(exe)


def task_name(job: str) -> str:
    """The Windows Task Scheduler task name for `job` (sanitised, prefixed)."""
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in job)
    return f"{TASK_PREFIX}{safe}"


def command(job: str) -> str:
    """The command an OS task runs: `run-due --job <name>` on the venv Python."""
    return f'"{_venv_python()}" -m store_sync.cli run-due --job "{job}"'


# ---------------------------------------------------------------------------
# Windows Task Scheduler helpers (thin wrappers for testability)
# ---------------------------------------------------------------------------

def _schtasks_query(task: str) -> bool:
    r = subprocess.run(["schtasks", "/query", "/tn", task],
                       capture_output=True, text=True)
    return r.returncode == 0


def _schtasks_create(task: str, cmd: str, *, sc: str, mo: int, st: str,
                     wd: str | None = None) -> None:
    args = ["schtasks", "/create", "/tn", task, "/tr", cmd,
            "/sc", sc, "/mo", str(mo), "/st", st, "/f"]
    if wd is not None:
        args += ["/d", wd]
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"schtasks /create failed: {r.stderr}")


def _schtasks_delete(task: str) -> None:
    r = subprocess.run(["schtasks", "/delete", "/tn", task, "/f"],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"schtasks /delete failed: {r.stderr}")


def _schtasks_list() -> set[str]:
    """Names of our tasks currently in the Task Scheduler."""
    r = subprocess.run(["schtasks", "/query", "/fo", "csv", "/nh"],
                       capture_output=True, text=True)
    names: set[str] = set()
    for line in r.stdout.splitlines():
        first = line.split(",")[0].strip('"')
        if first.startswith(TASK_PREFIX):
            names.add(first[len(TASK_PREFIX):])
    return names


# ---------------------------------------------------------------------------
# Linux crontab helpers (thin wrappers for testability)
# ---------------------------------------------------------------------------

def _read_crontab() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _write_crontab(content: str) -> None:
    r = subprocess.run(["crontab", "-"], input=content,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"crontab - failed: {r.stderr}")


def _crontab_jobs(content: str) -> set[str]:
    names: set[str] = set()
    for line in content.splitlines():
        s = line.strip()
        if s.startswith("# stg-store-sync:"):
            names.add(s[len("# stg-store-sync:"):])
    return names


# ---------------------------------------------------------------------------
# Trigger → OS schedule translation
# ---------------------------------------------------------------------------

def _windows_sc(t: Trigger) -> dict:
    """`schtasks` schedule kwargs for `t` — clock-anchored (spec §4.2)."""
    if t.unit == "minute":
        return {"sc": "minute", "mo": t.every, "st": "00:00"}
    if t.unit == "hour":
        return {"sc": "hourly", "mo": t.every, "st": "00:00"}
    if t.unit == "day":
        return {"sc": "daily", "mo": t.every, "st": t.at}
    return {"sc": "weekly", "mo": t.every, "st": t.at, "wd": _WD[t.weekday]}


def _cron_line(t: Trigger, job: str) -> str:
    """The crontab line for `t` — clock-anchored (spec §4.2)."""
    cmd = command(job)
    if t.unit == "minute":
        spec = f"*/{t.every} * * * *"
    elif t.unit == "hour":
        spec = f"0 */{t.every} * * *"
    elif t.unit == "day":
        hh, mm = t.at.split(":")
        spec = f"{int(mm)} {int(hh)} */{t.every} * *"
    else:
        hh, mm = t.at.split(":")
        spec = f"{int(mm)} {int(hh)} * * {t.weekday}"
    return f"{spec} {cmd}"


def _cron_remove_block(content: str, job: str) -> str:
    """Drop `job`'s sentinel + the line after it; keep every foreign line."""
    sentinel = _CRON_SENTINEL.format(job=job)
    out, skip_next = [], False
    for line in content.splitlines():
        if skip_next:
            skip_next = False
            continue
        if line.strip() == sentinel:
            skip_next = True
            continue
        out.append(line)
    return "\n".join(out).strip("\n")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def add(job: str, schedule: Schedule) -> str:
    """Register one OS task for `job`; idempotent. Returns a status message."""
    t = plan(schedule)
    if t is None:
        return f"{job}: manual only — no OS task"
    if not _is_windows() and not _is_linux():
        return f"unsupported platform {sys.platform!r}: register manually"
    if _is_windows():
        name = task_name(job)
        if _schtasks_query(name):
            return f"{job}: already registered"
        _schtasks_create(name, command(job), **_windows_sc(t))
        return f"{job}: registered (Windows Task Scheduler)"
    content = _read_crontab()
    if f"# stg-store-sync:{job}" in content:
        return f"{job}: already registered"
    block = f"{_CRON_SENTINEL.format(job=job)}\n{_cron_line(t, job)}"
    new = (content.rstrip("\n") + "\n" if content.strip() else "") + block + "\n"
    _write_crontab(new)
    return f"{job}: registered (crontab)"


def remove(job: str) -> str:
    """Unregister `job`'s OS task; a clean no-op when absent."""
    if not _is_windows() and not _is_linux():
        return f"unsupported platform {sys.platform!r}"
    if _is_windows():
        name = task_name(job)
        if not _schtasks_query(name):
            return f"{job}: not registered"
        _schtasks_delete(name)
        return f"{job}: removed"
    content = _read_crontab()
    if f"# stg-store-sync:{job}" not in content:
        return f"{job}: not registered"
    _write_crontab(_cron_remove_block(content, job) + "\n")
    return f"{job}: removed"


def _registered_jobs() -> set[str]:
    """Job names that currently have one of our OS tasks."""
    if _is_windows():
        return _schtasks_list()
    if _is_linux():
        return _crontab_jobs(_read_crontab())
    return set()


def status(entries) -> dict[str, str]:
    """Map job name → `registered|missing|extra|manual|unsupported`."""
    if not _is_windows() and not _is_linux():
        return {e.name: "unsupported" for e in entries}
    registered = _registered_jobs()
    out: dict[str, str] = {}
    for e in entries:
        if not e.enabled or plan(e.schedule) is None:
            out[e.name] = "manual"
        elif e.name in registered:
            out[e.name] = "registered"
        else:
            out[e.name] = "missing"
    for extra in registered - {e.name for e in entries}:
        out[extra] = "extra"
    return out


def sync(entries) -> str:
    """Reconcile every job's OS task: add missing, remove extra."""
    if not _is_windows() and not _is_linux():
        return f"unsupported platform {sys.platform!r}"
    st = status(entries)
    by_name = {e.name: e for e in entries}
    added = removed = 0
    for name, s in st.items():
        if s == "missing":
            try:
                add(name, by_name[name].schedule)
                added += 1
            except Exception:  # noqa: BLE001 - report, keep going
                pass
        elif s == "extra":
            try:
                remove(name)
                removed += 1
            except Exception:  # noqa: BLE001
                pass
    return f"sync: {added} added, {removed} removed"


def manual_hint(job: str, schedule: Schedule) -> str:
    """The exact command an operator must register by hand, or ""."""
    if plan(schedule) is None:
        return ""
    return command(job)
