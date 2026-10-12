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
