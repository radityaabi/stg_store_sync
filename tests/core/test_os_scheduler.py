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
        assert plan(Schedule("day", 1, at="2:7")) is None  # not zero-padded
        assert plan(Schedule("week", 1, at="02:00", weekday=9)) is None
        assert plan(Schedule("bogus", 1)) is None


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
            os_sched, "_schtasks_create",
            lambda t, c, **kw: seen.update(task=t, cmd=c, kw=kw),
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
