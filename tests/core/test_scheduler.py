"""Tests for store_sync.core.scheduler — minute-granular cron."""
from datetime import datetime
from store_sync.core.scheduler import is_due, Scheduler

def test_is_due_every_five_minutes():
    assert is_due("*/5 * * * *", datetime(2026, 10, 8, 3, 10)) is True
    assert is_due("*/5 * * * *", datetime(2026, 10, 8, 3, 11)) is False

def test_is_due_daily_at_two():
    assert is_due("0 2 * * *", datetime(2026, 10, 8, 2, 0)) is True
    assert is_due("0 2 * * *", datetime(2026, 10, 8, 2, 1)) is False

def test_blank_schedule_never_due():
    assert is_due("", datetime(2026, 10, 8, 2, 0)) is False

class FakeRegistry:
    def __init__(self, entries): self._e = entries
    def list_entries(self): return self._e

class E:
    def __init__(self, name, enabled, schedule):
        self.name, self.enabled, self.schedule = name, enabled, schedule

def test_tick_fires_once_per_minute():
    fired = []
    s = Scheduler(FakeRegistry([E("a", True, "* * * * *")]), fired.append,
                  now_fn=lambda: datetime(2026, 10, 8, 3, 10, 5))
    assert s.tick() == ["a"]
    assert s.tick() == []          # same minute
    s._now = lambda: datetime(2026, 10, 8, 3, 11, 5)
    assert s.tick() == ["a"]

def test_disabled_not_fired():
    s = Scheduler(FakeRegistry([E("a", False, "* * * * *")]), lambda n: None,
                  now_fn=lambda: datetime(2026, 10, 8, 3, 10))
    assert s.tick() == []
