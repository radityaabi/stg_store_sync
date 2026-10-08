"""Tests for store_sync.core.scheduler — interval + anchor schedules."""
from datetime import datetime
from store_sync.core.config import Schedule
from store_sync.core.scheduler import is_due, Scheduler


def test_minute_and_hour():
    assert is_due(Schedule("minute", 5), datetime(2026, 10, 8, 3, 10)) is True
    assert is_due(Schedule("minute", 5), datetime(2026, 10, 8, 3, 11)) is False
    assert is_due(Schedule("hour", 2), datetime(2026, 10, 8, 4, 0)) is True
    assert is_due(Schedule("hour", 2), datetime(2026, 10, 8, 4, 1)) is False


def test_day_anchor():
    # toordinal(2026-10-07) == 739896; 739896 % 3 == 0 → due
    assert is_due(Schedule("day", 3, "02:00"), datetime(2026, 10, 7, 2, 0)) is True
    # toordinal(2026-10-12) == 739901; 739901 % 3 == 2 → not due
    assert is_due(
        Schedule("day", 3, "02:00"), datetime(2026, 10, 12, 2, 0)
    ) is False  # ord%3 == 2
    # Wrong minute / wrong hour are never due.
    assert is_due(Schedule("day", 3, "02:00"), datetime(2026, 10, 7, 2, 1)) is False
    assert is_due(Schedule("day", 3, "02:00"), datetime(2026, 10, 7, 3, 0)) is False


def test_week_anchor():
    # 2026-10-12: ISO week 42 → 2026*53+42 == 107420; % 2 == 0 → due (Monday)
    assert is_due(
        Schedule("week", 2, "02:00", 0), datetime(2026, 10, 12, 2, 0)
    ) is True
    # 2026-10-19: ISO week 43 → 107421 % 2 == 1 → not due
    assert is_due(
        Schedule("week", 2, "02:00", 0), datetime(2026, 10, 19, 2, 0)
    ) is False
    # Wrong weekday is never due (2026-10-13 is a Tuesday).
    assert is_due(
        Schedule("week", 2, "02:00", 0), datetime(2026, 10, 13, 2, 0)
    ) is False


def test_blank_and_malformed_never_due():
    assert is_due(Schedule(""), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("day", 1, ""), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("minute", 0), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("nope", 1), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("day", 1, "2:00"), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("day", 1, "99:00"), datetime(2026, 10, 8, 2, 0)) is False


def test_out_of_bounds_every_and_weekday_never_due():
    # Spec §4.1 bounds — a schedule `Schedule.validate` rejects must also never
    # fire, so the dashboard label ("manual only") and the engine agree.
    assert is_due(Schedule("day", 32, "02:00"), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("week", 53, "02:00", 0), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("week", 2, "02:00", 9), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("week", 2, "02:00", -1), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("hour", 24), datetime(2026, 10, 8, 2, 0)) is False
    assert is_due(Schedule("minute", 60), datetime(2026, 10, 8, 2, 0)) is False
    # In-bounds values still fire (dates computed to satisfy each modulo).
    # toordinal(2026-10-19) % 31 == 0
    assert is_due(Schedule("day", 31, "02:00"), datetime(2026, 10, 19, 2, 0)) is True
    # iso index of 2027-01-07 (Thu, week 1) % 52 == 0
    assert is_due(Schedule("week", 52, "02:00", 3), datetime(2027, 1, 7, 2, 0)) is True
    assert is_due(Schedule("hour", 23), datetime(2026, 10, 8, 23, 0)) is True
    assert is_due(Schedule("minute", 59), datetime(2026, 10, 8, 2, 59)) is True


class FakeRegistry:
    def __init__(self, entries):
        self._e = entries

    def list_entries(self):
        return self._e


class E:
    def __init__(self, name, enabled, schedule):
        self.name, self.enabled, self.schedule = name, enabled, schedule


def test_tick_fires_once_per_minute():
    fired = []
    s = Scheduler(
        FakeRegistry([E("a", True, Schedule("minute", 1))]),
        fired.append,
        now_fn=lambda: datetime(2026, 10, 8, 3, 10, 5),
    )
    assert s.tick() == ["a"]
    assert s.tick() == []  # same minute
    s._now = lambda: datetime(2026, 10, 8, 3, 11, 5)
    assert s.tick() == ["a"]


def test_disabled_not_fired():
    s = Scheduler(
        FakeRegistry([E("a", False, Schedule("minute", 1))]),
        lambda n: None,
        now_fn=lambda: datetime(2026, 10, 8, 3, 10),
    )
    assert s.tick() == []


def test_bad_entry_is_skipped_not_fatal():
    class Bad:
        name = "bad"
        enabled = True

        @property
        def schedule(self):
            raise ValueError("corrupt row")

    s = Scheduler(
        FakeRegistry([Bad(), E("a", True, Schedule("minute", 1))]),
        lambda n: None,
        now_fn=lambda: datetime(2026, 10, 8, 3, 10),
    )
    assert s.tick() == ["a"]
