# OS-level Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a registered job run on its declared per-job schedule with the GUI
closed, by registering one OS-level task per job (Windows Task Scheduler /
Linux crontab) that wakes `stg-store-sync-cli run-due --job <name>`, while
`is_due()` stays the single source of truth for the schedule.

**Architecture:** A new pure module `core/os_scheduler.py` maps a job's
`Schedule` to a coarse OS trigger and performs idempotent register / remove /
sync through thin, stubbable OS helpers. A new CLI subcommand `run-due` reuses
the existing `Scheduler` (GUI-independent `tick()` / `is_due()`) to decide which
jobs are actually due, then runs them through the existing headless `_run_one`
path. A state-DB marker (`os_fired`) prevents a GUI fire and an OS fire in the
same minute from double-running. The GUI registers/unregisters best-effort and
shows an OS-status badge.

**Tech Stack:** Python 3.12, PySide6, `requests`, `filelock`, `python-dotenv`,
SQLite, `schtasks` (Windows), `crontab` (Linux), `pytest`.

**Spec:** `docs/superpowers/specs/2026-10-12-os-scheduler-design.md`

## Global Constraints

- Python 3.12; no new runtime dependencies.
- Production target is **Windows**; Linux is the dev machine. macOS must
  report `unsupported`, never silently no-op.
- Standalone repo — **never import from `stg_automation_suite`**. Its
  `src/momo/core/cron_setup.py` is a *reference only*.
- Headless runs are **add-only** (invariant 11): `run-due` never applies an
  update; updates remain proposals.
- Dry-run is per-job config: `run-due` passes `dry_run=None` so each job's
  configured `write.dry_run` applies (same as `run-all`); it never overrides it.
- Registration failures must never mutate the state DB or block the GUI.
- Exit codes: `0` ok / nothing due · `1` usage or missing credentials ·
  `2` blocked · `3` partial/error/unknown job/refused registration.
- Every OS interaction goes through module-level helpers so tests never touch
  the real OS.
- Test command for every task: `./.venv/bin/python -m pytest -q`.
  Full-suite gate before each commit: `./.venv/bin/python -m pytest -q -W error`.

## Review Focus

Inputs / failure modes the spec implies but individual task tests may not
exercise; each gets a test in the task that owns the code:

1. **A foreign-CWD scheduled run must still find credentials.** The OS task
   starts in an arbitrary directory; `load_dotenv()` is CWD-dependent and finds
   nothing. → Task 4 pins `<project-root>/.env` resolution from a foreign CWD.
2. **A `minute`/`hour` trigger that is not clock-anchored silently never
   fires.** `is_due` uses `now.minute % every` / `now.hour % every`. → Task 3
   pins the `00:00` / minute-0 anchor in the generated OS arguments.
3. **A malformed or manual-only schedule must not create an OS task.**
   → Task 2 pins `plan()` → `None` for empty/invalid schedules.
4. **A GUI fire and an OS fire in the same minute must run once.** → Task 1
   pins `claim_due`; Task 6 pins the GUI path claiming it.
5. **`schedule sync` must remove a task whose job was deleted or disabled,
   without touching unrelated OS entries.** → Task 3 (Linux foreign-line
   preservation) and Task 5 (`sync` removes extras) pin it.

---

### Task 1: State — per-minute due marker

**Files:**
- Modify: `src/store_sync/core/state.py` (schema block ~line 82-89; add methods
  after the blacklist section)
- Test: `tests/core/test_state.py`

**Interfaces:**
- Consumes: existing `State.__init__` (`executescript(_SCHEMA)`).
- Produces:
  - table `os_fired(job TEXT, minute_key TEXT, PRIMARY KEY (job, minute_key))`
  - `State.claim_due(self, job: str, minute_key: str) -> bool`
  - `State.prune_os_fired(self, keep_after: str) -> int`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_state.py`:

```python
class TestOsFired:
    def test_first_claim_wins_second_in_same_minute_loses(self, st):
        assert st.claim_due("j", "2026-10-12T10:40") is True
        assert st.claim_due("j", "2026-10-12T10:40") is False

    def test_different_minute_claims_again(self, st):
        assert st.claim_due("j", "2026-10-12T10:40") is True
        assert st.claim_due("j", "2026-10-12T10:41") is True

    def test_claim_is_per_job(self, st):
        assert st.claim_due("a", "2026-10-12T10:40") is True
        assert st.claim_due("b", "2026-10-12T10:40") is True

    def test_prune_removes_old_keys(self, st):
        st.claim_due("j", "2026-10-11T00:00")
        st.claim_due("j", "2026-10-12T10:40")
        removed = st.prune_os_fired("2026-10-12T00:00")
        assert removed == 1
        assert st.claim_due("j", "2026-10-11T00:00") is True  # pruned → reclaimable
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_state.py::TestOsFired -q`
Expected: FAIL — `AttributeError: 'State' object has no attribute 'claim_due'`.

- [ ] **Step 3: Add the table and methods**

Append to `_SCHEMA` in `src/store_sync/core/state.py` (after the `blacklist`
table, before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS os_fired (
    job        TEXT NOT NULL,
    minute_key TEXT NOT NULL,
    PRIMARY KEY (job, minute_key)
);
```

Add a section after the blacklist methods:

```python
    # -- OS due marker -----------------------------------------------------

    def claim_due(self, job: str, minute_key: str) -> bool:
        """Claim `(job, minute_key)`; True only for the first caller.

        Guards a GUI fire and an OS fire in the same wall-clock minute against
        running a job twice. `minute_key` is `now.strftime("%Y-%m-%dT%H:%M")`.
        """
        cur = self.conn.execute(
            "INSERT INTO os_fired (job, minute_key) VALUES (?, ?) "
            "ON CONFLICT(job, minute_key) DO NOTHING",
            (job, minute_key),
        )
        self.conn.commit()
        return cur.rowcount == 1

    def prune_os_fired(self, keep_after: str) -> int:
        """Delete markers strictly older than `keep_after`; return the count."""
        cur = self.conn.execute(
            "DELETE FROM os_fired WHERE minute_key < ?", (keep_after,)
        )
        self.conn.commit()
        return cur.rowcount
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_state.py -q`
Expected: PASS (all `TestOsFired` plus the existing suite in the file).

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/state.py tests/core/test_state.py
git commit -m "feat(state): os_fired minute marker to dedupe GUI vs OS fires"
```

---

### Task 2: `os_scheduler.plan` — pure schedule → trigger mapping

**Files:**
- Create: `src/store_sync/core/os_scheduler.py`
- Test: `tests/core/test_os_scheduler.py`

**Interfaces:**
- Consumes: `store_sync.core.config.Schedule` (fields `unit`, `every`, `at`,
  `weekday`; `Schedule.validate() -> list[str]`).
- Produces:
  - `@dataclass(frozen=True) class Trigger` with `unit: str`, `every: int`,
    `at: str = ""`, `weekday: int = 0`
  - `plan(schedule: Schedule) -> Trigger | None`

- [ ] **Step 1: Write the failing tests**

Create `tests/core/test_os_scheduler.py`:

```python
"""Tests for core/os_scheduler — schedule→OS-trigger mapping and OS helpers."""
from __future__ import annotations

from store_sync.core.config import Schedule
from store_sync.core.os_scheduler import Trigger, plan


class TestPlan:
    def test_minute(self):
        assert plan(Schedule("minute", 10)) == Trigger("minute", 10)

    def test_hour(self):
        assert plan(Schedule("hour", 3)) == Trigger("hour", 3)

    def test_day_keeps_anchor(self):
        assert plan(Schedule("day", 3, at="02:07")) == Trigger("day", 3, at="02:07")

    def test_week_keeps_anchor_and_weekday(self):
        assert plan(Schedule("week", 2, at="06:30", weekday=4)) == Trigger(
            "week", 2, at="06:30", weekday=4
        )

    def test_manual_only_is_none(self):
        assert plan(Schedule("")) is None

    def test_malformed_is_none(self):
        assert plan(Schedule("minute", 0)) is None       # every out of range
        assert plan(Schedule("minute", 99)) is None
        assert plan(Schedule("day", 1, at="")) is None    # day needs `at`
        assert plan(Schedule("day", 1, at="2:7")) is None # not zero-padded
        assert plan(Schedule("week", 1, at="02:00", weekday=9)) is None
        assert plan(Schedule("bogus", 1)) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_os_scheduler.py::TestPlan -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'store_sync.core.os_scheduler'`.

- [ ] **Step 3: Implement `plan`**

Create `src/store_sync/core/os_scheduler.py`:

```python
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

from dataclasses import dataclass

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_os_scheduler.py -q`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/os_scheduler.py tests/core/test_os_scheduler.py
git commit -m "feat(os_scheduler): pure schedule to OS-trigger mapping"
```

---

### Task 3: `os_scheduler` — platform backends, add/remove/status/sync

**Files:**
- Modify: `src/store_sync/core/os_scheduler.py`
- Test: `tests/core/test_os_scheduler.py`

**Interfaces:**
- Consumes: `Trigger`, `plan` (Task 2).
- Produces:
  - `TASK_PREFIX = "StgStoreSync_"`
  - `task_name(job: str) -> str`
  - `command(job: str) -> str`
  - `_schtasks_query(task) -> bool`, `_schtasks_create(task, command) -> None`,
    `_schtasks_delete(task) -> None`
  - `_read_crontab() -> str`, `_write_crontab(content: str) -> None`
  - `_is_windows() -> bool`, `_is_linux() -> bool`
  - `add(job, schedule) -> str`, `remove(job) -> str`,
    `status(entries) -> dict[str, str]`, `sync(entries) -> str`
  - status values: `"registered" | "missing" | "stale" | "extra" | "unsupported" | "manual"`

- [ ] **Step 1: Write the failing tests**

Add to `tests/core/test_os_scheduler.py`:

```python
from store_sync.core import os_scheduler as os_sched


class _Entry:
    def __init__(self, name, schedule, enabled=True):
        self.name = name
        self.schedule = schedule
        self.enabled = enabled


class TestTaskName:
    def test_sanitises_and_prefixes(self):
        assert os_sched.task_name("DUMMY_STORE_RED_ECHINO") == (
            "StgStoreSync_DUMMY_STORE_RED_ECHINO"
        )
        assert os_sched.task_name("my job/1") == "StgStoreSync_my_job_1"


class TestWindowsArgs:
    def test_minute_is_clock_anchored(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(os_sched, "_is_windows", lambda: True)
        monkeypatch.setattr(os_sched, "_is_linux", lambda: False)
        monkeypatch.setattr(os_sched, "_schtasks_query", lambda t: False)
        monkeypatch.setattr(
            os_sched, "_schtasks_create", lambda t, c, **kw: seen.update(task=t, cmd=c, kw=kw)
        )
        os_sched.add("j", Schedule("minute", 10))
        # clock anchor: /st 00:00 so the phase matches now.minute % 10 == 0
        assert seen["kw"]["st"] == "00:00"
        assert seen["kw"]["sc"] == "minute"
        assert seen["kw"]["mo"] == 10

    def test_hour_uses_hourly(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(os_sched, "_is_windows", lambda: True)
        monkeypatch.setattr(os_sched, "_is_linux", lambda: False)
        monkeypatch.setattr(os_sched, "_schtasks_query", lambda t: False)
        monkeypatch.setattr(
            os_sched, "_schtasks_create", lambda t, c, **kw: seen.update(kw=kw)
        )
        os_sched.add("j", Schedule("hour", 3))
        assert seen["kw"]["sc"] == "hourly"
        assert seen["kw"]["mo"] == 3
        assert seen["kw"]["st"] == "00:00"

    def test_day_uses_daily_at_anchor(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(os_sched, "_is_windows", lambda: True)
        monkeypatch.setattr(os_sched, "_is_linux", lambda: False)
        monkeypatch.setattr(os_sched, "_schtasks_query", lambda t: False)
        monkeypatch.setattr(
            os_sched, "_schtasks_create", lambda t, c, **kw: seen.update(kw=kw)
        )
        os_sched.add("j", Schedule("day", 3, at="02:07"))
        assert seen["kw"]["sc"] == "daily"
        assert seen["kw"]["st"] == "02:07"

    def test_manual_only_registers_nothing(self, monkeypatch):
        called = []
        monkeypatch.setattr(os_sched, "_is_windows", lambda: True)
        monkeypatch.setattr(os_sched, "_schtasks_create", lambda *a, **k: called.append(a))
        msg = os_sched.add("j", Schedule(""))
        assert called == []
        assert "manual" in msg.lower()


class TestIdempotence:
    def test_add_twice_creates_once(self, monkeypatch):
        state = {"exists": False, "creates": 0}
        monkeypatch.setattr(os_sched, "_is_windows", lambda: True)
        monkeypatch.setattr(os_sched, "_schtasks_query", lambda t: state["exists"])
        def _create(t, c, **kw):
            state["creates"] += 1
            state["exists"] = True
        monkeypatch.setattr(os_sched, "_schtasks_create", _create)
        os_sched.add("j", Schedule("minute", 10))
        os_sched.add("j", Schedule("minute", 10))
        assert state["creates"] == 1


class TestLinuxCrontab:
    def _wire(self, monkeypatch, initial=""):
        store = {"content": initial}
        monkeypatch.setattr(os_sched, "_is_windows", lambda: False)
        monkeypatch.setattr(os_sched, "_is_linux", lambda: True)
        monkeypatch.setattr(os_sched, "_read_crontab", lambda: store["content"])
        monkeypatch.setattr(
            os_sched, "_write_crontab", lambda c: store.update(content=c)
        )
        return store

    def test_add_is_clock_anchored_and_idempotent(self, monkeypatch):
        store = self._wire(monkeypatch)
        os_sched.add("j", Schedule("minute", 10))
        os_sched.add("j", Schedule("minute", 10))
        assert store["content"].count("# stg-store-sync:j") == 1
        assert "*/10 * * * *" in store["content"]

    def test_remove_preserves_foreign_lines(self, monkeypatch):
        store = self._wire(monkeypatch, initial="# someone else\n0 5 * * * /bin/foo\n")
        os_sched.add("j", Schedule("minute", 10))
        os_sched.remove("j")
        assert "/bin/foo" in store["content"]
        assert "stg-store-sync:j" not in store["content"]


class TestSync:
    def test_adds_missing_and_removes_extra(self, monkeypatch):
        store = {"content": "# stg-store-sync:gone\n*/5 * * * * run-due --job gone\n"}
        monkeypatch.setattr(os_sched, "_is_windows", lambda: False)
        monkeypatch.setattr(os_sched, "_is_linux", lambda: True)
        monkeypatch.setattr(os_sched, "_read_crontab", lambda: store["content"])
        monkeypatch.setattr(os_sched, "_write_crontab", lambda c: store.update(content=c))
        entries = [_Entry("keep", Schedule("minute", 5))]
        os_sched.sync(entries)
        assert "stg-store-sync:keep" in store["content"]
        assert "stg-store-sync:gone" not in store["content"]

    def test_status_reports_missing_and_extra(self, monkeypatch):
        monkeypatch.setattr(os_sched, "_is_windows", lambda: False)
        monkeypatch.setattr(os_sched, "_is_linux", lambda: True)
        monkeypatch.setattr(
            os_sched, "_read_crontab",
            lambda: "# stg-store-sync:gone\n*/5 * * * * x\n",
        )
        st = os_sched.status([_Entry("keep", Schedule("minute", 5))])
        assert st["keep"] == "missing"
        assert st["gone"] == "extra"

    def test_unsupported_platform(self, monkeypatch):
        monkeypatch.setattr(os_sched, "_is_windows", lambda: False)
        monkeypatch.setattr(os_sched, "_is_linux", lambda: False)
        assert os_sched.add("j", Schedule("minute", 5)).lower().startswith("unsupported")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/core/test_os_scheduler.py -q`
Expected: FAIL — `AttributeError` on `_schtasks_query` / `add` etc.

- [ ] **Step 3: Implement the backends**

Append to `src/store_sync/core/os_scheduler.py` (imports: `shutil`,
`subprocess`, `sys`, `Path`):

```python
import shutil
import subprocess
import sys
from pathlib import Path

TASK_PREFIX = "StgStoreSync_"
_CRON_SENTINEL = "# stg-store-sync:{job}"


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
    safe = "".join(c if c.isalnum() or c in "_-" else "_" for c in job)
    return f"{TASK_PREFIX}{safe}"


def command(job: str) -> str:
    return f'"{_venv_python()}" -m store_sync.cli run-due --job "{job}"'
```

Windows helpers:

```python
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
```

Linux helpers:

```python
def _read_crontab() -> str:
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else ""


def _write_crontab(content: str) -> None:
    r = subprocess.run(["crontab", "-"], input=content,
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"crontab - failed: {r.stderr}")
```

Trigger → OS schedule translation (Windows) — **note the clock anchors**:

```python
_WD = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def _windows_sc(t: Trigger) -> dict:
    if t.unit == "minute":
        return {"sc": "minute", "mo": t.every, "st": "00:00"}
    if t.unit == "hour":
        return {"sc": "hourly", "mo": t.every, "st": "00:00"}
    if t.unit == "day":
        return {"sc": "daily", "mo": t.every, "st": t.at}
    return {"sc": "weekly", "mo": t.every, "st": t.at, "wd": _WD[t.weekday]}
```

Trigger → crontab line (Linux) — clock-anchored:

```python
def _cron_line(t: Trigger, job: str) -> str:
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
```

Linux block add/remove (sentinel-delimited, foreign lines preserved):

```python
def _cron_remove_block(content: str, job: str) -> str:
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
```

Public API:

```python
def add(job: str, schedule: Schedule) -> str:
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
    # linux
    content = _read_crontab()
    if f"# stg-store-sync:{job}" in content:
        return f"{job}: already registered"
    block = f"{_CRON_SENTINEL.format(job=job)}\n{_cron_line(t, job)}"
    new = (content.rstrip("\n") + "\n" if content.strip() else "") + block + "\n"
    _write_crontab(new)
    return f"{job}: registered (crontab)"


def remove(job: str) -> str:
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
```

`status` and `sync` (registry-aware; a job with no trigger is `"manual"`):

```python
def _registered_jobs() -> set[str]:
    if _is_windows():
        # enumerate our tasks
        r = subprocess.run(["schtasks", "/query", "/fo", "csv", "/nh"],
                           capture_output=True, text=True)
        names = set()
        for line in r.stdout.splitlines():
            first = line.split(",")[0].strip('"')
            if first.startswith(TASK_PREFIX):
                names.add(first[len(TASK_PREFIX):])
        return names
    if _is_linux():
        names = set()
        for line in _read_crontab().splitlines():
            s = line.strip()
            if s.startswith("# stg-store-sync:"):
                names.add(s[len("# stg-store-sync:"):])
        return names
    return set()


def status(entries) -> dict[str, str]:
    if not _is_windows() and not _is_linux():
        return {e.name: "unsupported" for e in entries}
    registered = _registered_jobs()
    want = {e.name for e in entries if e.enabled and plan(e.schedule) is not None}
    out: dict[str, str] = {}
    for e in entries:
        if plan(e.schedule) is None or not e.enabled:
            out[e.name] = "manual"
        elif e.name in registered:
            out[e.name] = "registered"
        else:
            out[e.name] = "missing"
    for extra in registered - {e.name for e in entries}:
        out[extra] = "extra"
    return out


def sync(entries) -> str:
    if not _is_windows() and not _is_linux():
        return f"unsupported platform {sys.platform!r}"
    st = status(entries)
    added = removed = 0
    for name, s in st.items():
        if s == "missing":
            try:
                add(name, next(e.schedule for e in entries if e.name == name))
                added += 1
            except Exception:  # noqa: BLE001 - report, keep going
                pass
        elif s in ("extra",):
            try:
                remove(name)
                removed += 1
            except Exception:  # noqa: BLE001
                pass
    return f"sync: {added} added, {removed} removed"


def manual_hint(job: str, schedule: Schedule) -> str:
    """The exact command an operator must register by hand (unsupported/failed)."""
    t = plan(schedule)
    if t is None:
        return ""
    return command(job)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/core/test_os_scheduler.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/core/os_scheduler.py tests/core/test_os_scheduler.py
git commit -m "feat(os_scheduler): schtasks/crontab backends, add/remove/status/sync"
```

---

### Task 4: CLI — CWD-independent credentials + `run-due`

**Files:**
- Modify: `src/store_sync/cli.py` (imports; `_credentials_from_env`; parser;
  `main` dispatch)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `os_scheduler` (Task 3); `Scheduler` / `is_due`
  (`core/scheduler.py`); `State.claim_due` (Task 1); existing `_run_one`,
  `_acquire_lock`, `_exit_for`, `_emit`.
- Produces:
  - `_load_env() -> None` (CWD-independent `<project-root>/.env` then
    `load_dotenv()`)
  - `_cmd_run_due(db: Path, args) -> int`
  - `run-due` subcommand (`--job`, `--json`)

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
class TestRunDue:
    def test_runs_only_due_jobs(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")           # helper already used in this file
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        ran = []
        monkeypatch.setattr(cli, "_run_one",
                            lambda reg, st, c, name, dry, **kw: (ran.append(name), (0, None))[1])
        # 10:40 is minute % 10 == 0 -> due
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 40))
        assert main(["run-due"]) == 0
        assert ran == ["a"]

    def test_not_due_does_not_run(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        ran = []
        monkeypatch.setattr(cli, "_run_one", lambda *a, **k: (ran.append(a[3]), (0, None))[1])
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 41))
        assert main(["run-due"]) == 0
        assert ran == []

    def test_second_fire_same_minute_is_skipped(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        ran = []
        monkeypatch.setattr(cli, "_run_one", lambda *a, **k: (ran.append(a[3]), (0, None))[1])
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 40))
        main(["run-due"])
        main(["run-due"])
        assert ran == ["a"]

    def test_job_filter(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a"); add_job(db, "b")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        set_enabled(db, "b", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        set_creds(monkeypatch)
        monkeypatch.setattr(cli, "STGClient", _DummyClient)
        ran = []
        monkeypatch.setattr(cli, "_run_one", lambda *a, **k: (ran.append(a[3]), (0, None))[1])
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 40))
        assert main(["run-due", "--job", "b"]) == 0
        assert ran == ["b"]

    def test_unknown_job_is_3(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        assert main(["run-due", "--job", "nope"]) == 3

    def test_missing_credentials_is_1(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        _no_creds(monkeypatch)          # helper that deletes the three vars
        monkeypatch.setattr(cli, "_now", lambda: datetime(2026, 10, 12, 10, 40))
        assert main(["run-due"]) == 1

    def test_env_resolves_from_foreign_cwd(self, tmp_path, monkeypatch):
        # A scheduled task starts in an arbitrary CWD; .env must still load.
        # NOTE: the module-level `_no_dotenv` fixture neutralises
        # `cli.load_dotenv`, so this test asserts the *path resolution*, not the
        # env values: `_load_env` must point at the project-root `.env`.
        monkeypatch.chdir(tmp_path)
        seen = {}
        monkeypatch.setattr(cli, "load_dotenv", lambda p=None, **k: seen.update(p=p))
        cli._load_env()
        assert seen["p"] is not None
        assert str(seen["p"]).endswith(".env")
        assert "stg_store_sync" in str(seen["p"])   # repo-root .env, not CWD
```

If the helpers `set_enabled`, `_no_creds`, `_now` do not exist in the file,
add them next to the existing `add_job` / `set_creds` helpers:

```python
def set_enabled(db, name, *, schedule, enabled):
    reg = Registry(db)
    try:
        reg.set_schedule(name, schedule)
        reg.set_enabled(name, enabled)
    finally:
        reg.close()


def _no_creds(monkeypatch):
    for v in ("STG_SERVER", "STG_USER", "STG_PASSWORD"):
        monkeypatch.delenv(v, raising=False)
```

`_now` is a module-level hook the plan adds in Step 3.

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_cli.py::TestRunDue -q`
Expected: FAIL — `run-due` is not a subcommand / `cli._load_env` missing.

- [ ] **Step 3: Implement `_load_env`, `_now`, and `run-due`**

In `src/store_sync/cli.py`:

```python
from datetime import datetime
from pathlib import Path
from dotenv import find_dotenv, load_dotenv

from store_sync.core.config import Schedule, human
from store_sync.core.os_scheduler import sync as os_sync, status as os_status
from store_sync.core.scheduler import Scheduler


def _now() -> datetime:
    """Clock hook (monkeypatched in tests)."""
    return datetime.now()


def _load_env() -> None:
    """Load `.env` independent of CWD, then fall back to a CWD search.

    An OS task starts in an arbitrary directory, so `load_dotenv()`'s upward
    search finds nothing. The project-root `.env` is loaded explicitly first;
    already-set environment variables win.
    """
    root = Path(__file__).resolve().parents[2]  # src/store_sync/cli.py → repo root
    project_env = root / ".env"
    if project_env.is_file():
        load_dotenv(project_env, override=False)
    else:
        load_dotenv(find_dotenv(), override=False)
```

Replace the `load_dotenv()` call in `main` with `_load_env()`.

Add the subcommand to `build_parser()`:

```python
    rd = sub.add_parser("run-due", help="run every enabled job that is due now")
    rd.add_argument("--job", help="restrict to one job (runs only if due)")
    rd.add_argument("--json", action="store_true", help="machine-readable output")
```

Add the handler:

```python
def _cmd_run_due(db: Path, args) -> int:
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
            collected.append,
            now_fn=_now,
        )
        scheduler.tick()

        # Drop anything already claimed this minute (GUI may have fired it).
        minute_key = _now().strftime("%Y-%m-%dT%H:%M")
        due = [n for n in collected if state.claim_due(n, minute_key)]
        state.prune_os_fired(
            (_now() - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M")
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
                    print(f"error: another run holds the lock for {name}",
                          file=sys.stderr)
                    codes.append(3)
                    continue
                code, res = _run_one(reg, state, client, name, None,
                                     as_json=args.json)
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
```

Add the tiny filter wrapper and the `timedelta` import:

```python
from datetime import datetime, timedelta


class _FilteredRegistry:
    """Registry view restricted to a set of names (or all when None)."""

    def __init__(self, reg, names: list[str] | None):
        self._reg = reg
        self._names = names

    def list_entries(self):
        entries = self._reg.list_entries()
        if self._names is None:
            return entries
        return [e for e in entries if e.name in self._names]
```

Dispatch in `main`:

```python
    if args.command == "run-due":
        return _cmd_run_due(db, args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): run-due subcommand + CWD-independent credentials"
```

---

### Task 5: CLI — `schedule list/add/remove/sync`

**Files:**
- Modify: `src/store_sync/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `os_scheduler.add/remove/status/sync` (Task 3); `Registry`.
- Produces: `_cmd_schedule_list/_add/_remove/_sync` and the `schedule`
  subcommand group.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_cli.py`:

```python
class TestScheduleCmd:
    def test_schedule_list_shows_status(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        monkeypatch.setattr(cli, "os_status", lambda entries: {"a": "missing"})
        assert main(["schedule", "list"]) == 0
        out = capsys.readouterr().out
        assert "a" in out and "missing" in out

    def test_schedule_add_unknown_job_is_3(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(tmp_path / "s.db"))
        assert main(["schedule", "add", "nope"]) == 3

    def test_schedule_add_manual_only_is_3(self, tmp_path, capsys, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")            # add_job leaves it manual-only/disabled
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        assert main(["schedule", "add", "a"]) == 3

    def test_schedule_add_calls_os(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        set_enabled(db, "a", schedule=Schedule("minute", 10), enabled=True)
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        seen = []
        monkeypatch.setattr(cli, "os_add", lambda name, sched: seen.append(name) or "ok")
        assert main(["schedule", "add", "a"]) == 0
        assert seen == ["a"]

    def test_schedule_remove_unknown_job_is_3(self, tmp_path, monkeypatch):
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(tmp_path / "s.db"))
        assert main(["schedule", "remove", "nope"]) == 3

    def test_schedule_sync_calls_os(self, tmp_path, monkeypatch):
        db = tmp_path / "s.db"
        add_job(db, "a")
        monkeypatch.setenv("STG_STORE_SYNC_DB", str(db))
        seen = []
        monkeypatch.setattr(cli, "os_sync", lambda entries: seen.append(1) or "synced")
        assert main(["schedule", "sync"]) == 0
        assert seen == [1]
```

Import aliases in `tests/test_cli.py` so `cli.os_add` / `cli.os_sync` /
`cli.os_status` are patchable: the plan imports them into `cli.py` as
`os_add`/`os_remove`/`os_status`/`os_sync` (Step 3).

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/test_cli.py::TestScheduleCmd -q`
Expected: FAIL — no `schedule` subcommand.

- [ ] **Step 3: Implement the subcommand group**

In `src/store_sync/cli.py`, import the aliases:

```python
from store_sync.core.os_scheduler import (
    add as os_add,
    remove as os_remove,
    status as os_status,
    sync as os_sync,
)
```

Add to `build_parser()`:

```python
    sch = sub.add_parser("schedule", help="manage OS-level schedule tasks")
    sch_sub = sch.add_subparsers(dest="schedule_command", required=True)
    sch_sub.add_parser("list", help="show each job's OS registration status")
    sch_add = sch_sub.add_parser("add", help="register one job's OS task")
    sch_add.add_argument("name")
    sch_rem = sch_sub.add_parser("remove", help="unregister one job's OS task")
    sch_rem.add_argument("name")
    sch_sub.add_parser("sync", help="reconcile all jobs' OS tasks")
```

Add the handlers:

```python
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
    if not entry.enabled or plan(entry.schedule) is None:
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
```

`plan` is imported from `os_scheduler`. Dispatch in `main`:

```python
    if args.command == "schedule":
        return {
            "list": _cmd_schedule_list,
            "add": _cmd_schedule_add,
            "remove": _cmd_schedule_remove,
            "sync": _cmd_schedule_sync,
        }[args.schedule_command](db, args)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/test_cli.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/cli.py tests/test_cli.py
git commit -m "feat(cli): schedule list/add/remove/sync subcommands"
```

---

### Task 6: GUI — OS badge, Sync action, best-effort auto-sync, `_on_due` marker

**Files:**
- Modify: `src/store_sync/ui/job_table_model.py` (add an `os` column)
- Modify: `src/store_sync/ui/dashboard.py` (render the column)
- Modify: `src/store_sync/gui.py` (`_on_due` claim; `_auto_sync_os` on
  save/delete; `sync_os_schedule` action; badge refresh in `_refresh`)
- Modify: `src/store_sync/ui/main_window.py` (a "Sync OS schedule" action +
  signal, if the window owns the action bar)
- Test: `tests/ui/test_controller.py`

**Interfaces:**
- Consumes: `os_scheduler.add/remove/status/sync` (Task 3); `State.claim_due`
  (Task 1); existing `Controller.save` / `delete` / `_on_due` / `_refresh`.
- Produces:
  - `Controller.sync_os_schedule(self) -> None`
  - `Controller._auto_sync_os(self, name: str) -> None` (best-effort)
  - `Controller._os_status(self, entries) -> dict[str, str]`

- [ ] **Step 1: Write the failing tests**

Add to `tests/ui/test_controller.py`:

```python
def test_on_due_claims_minute_marker(tmp_path):
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c._on_due("a")
    # a second call in the same minute is a no-op (marker already claimed)
    assert c.state.claim_due("a", c._minute_key()) is False
    c.stop(); w.close()


def test_auto_sync_swallows_os_error(tmp_path, monkeypatch):
    from store_sync import gui
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    c.save("a", a_job(), schedule=Schedule("minute", 10), enabled=True)
    monkeypatch.setattr(gui.os_scheduler, "add", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no schtasks")))
    # must not raise
    c._auto_sync_os("a")
    c.stop(); w.close()


def test_sync_os_schedule_reports_status(tmp_path, monkeypatch):
    from store_sync import gui
    w = MainWindow()
    c = Controller(w, db_path=tmp_path / "s.db", run_fn=lambda name, stop: None)
    monkeypatch.setattr(gui.os_scheduler, "sync", lambda entries: "sync: 0 added, 0 removed")
    c.sync_os_schedule()   # must not raise
    c.stop(); w.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./.venv/bin/python -m pytest tests/ui/test_controller.py -q -k "os or due"`
Expected: FAIL — `_auto_sync_os` / `sync_os_schedule` / `_minute_key` missing.

- [ ] **Step 3: Implement the GUI wiring**

In `src/store_sync/gui.py`:

```python
from store_sync.core import os_scheduler
```

Add a minute-key helper and claim in `_on_due`:

```python
    def _minute_key(self) -> str:
        return datetime.now().strftime("%Y-%m-%dT%H:%M")

    def _on_due(self, name: str) -> None:
        # The OS task may have fired the same minute; claim it so only one runs.
        if not self.state.claim_due(name, self._minute_key()):
            return
        self.window.set_status(f"scheduled {name}", _STATUS_WARN)
```

Best-effort auto-sync + explicit action:

```python
    def _auto_sync_os(self, name: str) -> None:
        """Register/unregister one job's OS task; never raise, never block."""
        try:
            entry = self.registry.get_entry(name)
        except KeyError:
            try:
                os_scheduler.remove(name)
            except Exception:  # noqa: BLE001
                logger.warning("os schedule remove failed for %r", name, exc_info=True)
            return
        try:
            if entry.enabled and os_scheduler.plan(entry.schedule) is not None:
                os_scheduler.add(name, entry.schedule)
            else:
                os_scheduler.remove(name)
        except Exception:  # noqa: BLE001 - schtasks/crontab may be absent
            logger.warning("os schedule sync failed for %r", name, exc_info=True)
            self.window.set_status(
                "OS schedule not updated (register manually)", _STATUS_WARN
            )

    def sync_os_schedule(self) -> None:
        try:
            msg = os_scheduler.sync(self.registry.list_entries())
        except Exception:  # noqa: BLE001
            logger.warning("os schedule sync failed", exc_info=True)
            self.window.set_status("OS schedule sync failed", _STATUS_FAIL)
            return
        self.window.set_status(msg, _STATUS_OK)
        self._refresh()

    def _os_status(self, entries) -> dict:
        try:
            return os_scheduler.status(entries)
        except Exception:  # noqa: BLE001
            logger.warning("os status failed", exc_info=True)
            return {}
```

Call `_auto_sync_os(name)` at the end of `save()` (after the registry write,
inside the try so a failure never fails the save — or immediately after, wrapped
as above) and in `delete()` after the registry delete. In `_refresh()`, pass
`self._os_status(entries)` into the dashboard row dicts under an `"os"` key.

Add the `os` column to `src/store_sync/ui/job_table_model.py` (append `"os"`
to the model's field/header list) and render it in `dashboard.py`'s row build.

Add a "Sync OS schedule" action to the window: in `main_window.py` add
`sync_os_requested = Signal()` and a button/menu entry that emits it; in
`gui.py`'s `_wire`, connect `w.sync_os_requested.connect(self.sync_os_schedule)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `./.venv/bin/python -m pytest tests/ui/test_controller.py tests/ui/test_dashboard.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/store_sync/ui/job_table_model.py src/store_sync/ui/dashboard.py \
        src/store_sync/ui/main_window.py src/store_sync/gui.py tests/ui/test_controller.py
git commit -m "feat(ui): OS schedule badge, sync action, best-effort auto-sync"
```

---

### Task 7: Docs — invariant 14, vocabulary, ledger, README

**Files:**
- Modify: `AGENTS.md` (invariant 14)
- Modify: `CONTEXT.md` (vocabulary)
- Modify: `tasks/todo.md` (Phase 11 ledger)
- Modify: `README.md` (automation section)
- Test: none (documentation) — verified by reading the diff

**Interfaces:** none (docs only).

- [ ] **Step 1: Add invariant 14 to `AGENTS.md`**

After invariant 13:

```markdown
14. **OS-level scheduling only wakes the run; `is_due` is the schedule.**
    One OS task per enabled, scheduled job (`schedule sync`) runs
    `stg-store-sync-cli run-due --job <name>`. The OS trigger is coarse; the
    precise decision is always `Scheduler.tick()` / `is_due()`. `run-due` is
    add-only and passes `dry_run=None` (each job's configured dry-run applies).
    A per-minute `os_fired` marker stops a GUI fire and an OS fire in the same
    minute from running twice.
```

- [ ] **Step 2: Add vocabulary to `CONTEXT.md`**

Under "Planning & writing", after **Blacklist**:

```markdown
**OS schedule** (OS task):
The per-job entry in Windows Task Scheduler or Linux crontab that wakes
`run-due --job <name>` often enough for `is_due()` to evaluate. It is a coarse
wake-up, never the schedule itself.
_Avoid_: cron job (that is the OS artifact, not the schedule).

**Trigger**:
The coarse OS-level schedule (`Trigger`) derived from a job's `Schedule` by
`os_scheduler.plan`; `None` for a manual-only or malformed schedule.
_Avoid_: schedule, interval.
```

- [ ] **Step 3: Add the ledger to `tasks/todo.md`**

After the Phase 10 section:

```markdown
## Phase 11 — OS-level scheduler ✅ COMPLETE
> Spec: `docs/superpowers/specs/2026-10-12-os-scheduler-design.md`
> Plan: `docs/superpowers/plans/2026-10-12-os-scheduler.md`
> Jobs run on their per-job schedule with the GUI closed, via one OS task per
> job (Windows `schtasks` / Linux `crontab`) that wakes `run-due --job`.

- [x] 11.1 `core/state.py` — `os_fired` minute marker
- [x] 11.2 `core/os_scheduler.py` — `plan` schedule→trigger
- [x] 11.3 `core/os_scheduler.py` — schtasks/crontab backends + add/remove/status/sync
- [x] 11.4 `cli.py` — `run-due` + CWD-independent `.env`
- [x] 11.5 `cli.py` — `schedule list/add/remove/sync`
- [x] 11.6 GUI — OS badge, Sync action, best-effort auto-sync
- [x] 11.7 Docs — invariant 14, vocabulary, ledger, README
```

- [ ] **Step 4: Add the README automation section**

Append to `README.md`:

```markdown
## Automation without the GUI

The in-app scheduler runs only while the app is open. To run jobs on their
schedule with the app closed, register one OS task per job:

```bash
stg-store-sync-cli schedule sync        # reconcile all jobs (run once after install)
stg-store-sync-cli schedule list        # show each job's OS status
stg-store-sync-cli schedule add <name>  # register one job
stg-store-sync-cli schedule remove <name>
```

Each task runs `stg-store-sync-cli run-due --job <name>`; `run-due` decides
whether the job is actually due (the OS trigger is a coarse wake-up) and runs
it add-only, honouring the job's configured dry-run. Credentials come from the
project-root `.env` (resolved independently of the working directory).
```

- [ ] **Step 5: Verify the suite is still green**

Run: `./.venv/bin/python -m pytest -q -W error`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add AGENTS.md CONTEXT.md tasks/todo.md README.md
git commit -m "docs: OS scheduler invariant, vocabulary, ledger, README"
```

---

## Self-Review

**1. Spec coverage**

| Spec section | Task |
|---|---|
| §3 verified findings (Scheduler reuse, CWD creds, momo reference) | Tasks 2, 4 |
| §4 architecture / single source of truth | Tasks 2, 3, 4 |
| §4.1 components (`os_scheduler`, cli, gui, state) | Tasks 1, 2, 3, 4, 5, 6 |
| §4.2 mapping + anchor alignment | Tasks 2, 3 |
| §4.3 names/sentinels | Task 3 |
| §4.4 command line + CWD-independent creds | Task 4 |
| §5 CLI surface + exit codes | Tasks 4, 5 |
| §6 double-fire guard | Tasks 1, 4, 6 |
| §7 GUI | Task 6 |
| §8 risks/failure modes | Tasks 3 (unsupported/manual), 4 (creds), 6 (never block) |
| §9 testing strategy | every task |
| §10 documentation | Task 7 |
| §11 out of scope | not implemented (correct) |

No gaps.

**2. Step scan:** each step names one action with a checkable result; no
"handle edge cases" filler. Bodies appear only where the algorithm (sentinel
block removal, trigger→args) is not determined by the signature and tests.

**3. Type consistency:** `Trigger` fields (`unit`, `every`, `at`, `weekday`),
`plan` return, `task_name`, `command`, `add/remove/status/sync`, `claim_due`,
`prune_os_fired`, `_load_env`, `_cmd_run_due`, `_FilteredRegistry`,
`sync_os_schedule`, `_auto_sync_os`, `_minute_key` are used consistently across
tasks. `os_add/os_remove/os_status/os_sync` aliases in `cli.py` match the test
monkeypatch targets.

**4. Review Focus:** all five lines have a home — (1) Task 4
`test_env_resolves_from_foreign_cwd`; (2) Task 3
`test_minute_is_clock_anchored` / `test_hour_uses_hourly`; (3) Task 2
`test_manual_only_is_none` / `test_malformed_is_none`; (4) Task 1
`test_first_claim_wins_second_in_same_minute_loses` + Task 6
`test_on_due_claims_minute_marker`; (5) Task 3 `test_remove_preserves_foreign_lines`
+ Task 5 `test_schedule_sync_calls_os`.

**5. Proportion:** plan ≈ 2× the spec, mostly test code and exact signatures —
acceptable; no function body is transcribed that the tests determine.
