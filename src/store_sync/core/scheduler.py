"""Scheduler — in-app minute-granular scheduler for enabled jobs (spec §4).

A structured :class:`store_sync.core.config.Schedule` evaluator (interval +
anchor, spec §4.2) plus a thin loop that, once per tick, enqueues every enabled
job whose schedule is due and that has not already fired this minute. The
Scheduler owns no thread: :meth:`Scheduler.tick` is a pure step (testable with
a fake clock) and :meth:`Scheduler.run_forever` is the blocking driver the app
runs on its own daemon thread.

Due rules (spec §4.2):

* ``minute``: ``now.minute % every == 0``
* ``hour``: ``now.minute == 0 and now.hour % every == 0``
* ``day``: at ``HH:MM`` and ``now.date().toordinal() % every == 0``
* ``week``: on ``weekday`` at ``HH:MM`` and the ISO week index
  ``year*53 + week`` satisfies ``% every == 0``

An empty or malformed schedule (unknown unit, ``every <= 0``, bad ``at``) is
never due.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable

from store_sync.core.config import Schedule

logger = logging.getLogger(__name__)


def _parse_at(at: str) -> tuple[int, int] | None:
    """Split an "HH:MM" anchor into (hour, minute), or None if malformed."""
    try:
        h_s, m_s = at.strip().split(":")
        hour, minute = int(h_s), int(m_s)
    except (ValueError, AttributeError):
        return None
    if len(h_s) != 2 or len(m_s) != 2:  # zero-padded "HH:MM" only
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def is_due(schedule: Schedule, now: datetime) -> bool:
    """True if `schedule` (interval + anchor, spec §4.2) matches `now`."""
    if not isinstance(schedule, Schedule):
        return False
    every = schedule.every
    if schedule.unit not in ("minute", "hour", "day", "week"):
        return False
    if not isinstance(every, int) or every <= 0:
        return False
    if schedule.unit == "minute":
        return now.minute % every == 0
    if schedule.unit == "hour":
        return now.minute == 0 and now.hour % every == 0
    anchor = _parse_at(schedule.at)
    if anchor is None:
        return False
    hour, minute = anchor
    at_time = now.hour == hour and now.minute == minute
    if not at_time:
        return False
    if schedule.unit == "day":
        return now.date().toordinal() % every == 0
    # week: weekday() is Monday=0, same numbering as Schedule.weekday.
    if now.weekday() != schedule.weekday:
        return False
    iso = now.isocalendar()
    return (iso[0] * 53 + iso[1]) % every == 0


class Scheduler:
    """Evaluate enabled jobs' schedules each minute and enqueue the due ones."""

    def __init__(self, registry, enqueue: Callable[[str], object], *,
                 now_fn: Callable[[], datetime] | None = None):
        self._registry = registry
        self._enqueue = enqueue
        self._now: Callable[[], datetime] = now_fn or datetime.now
        self._last: dict[str, str] = {}

    def tick(self) -> list[str]:
        """Enqueue every enabled, due job not yet fired this minute."""
        now = self._now()
        minute_key = now.strftime("%Y-%m-%dT%H:%M")
        fired: list[str] = []
        for entry in self._registry.list_entries():
            if not getattr(entry, "enabled", False):
                continue
            name = entry.name
            if self._last.get(name) == minute_key:
                continue
            try:
                due = is_due(getattr(entry, "schedule", None), now)
            except Exception:
                logger.exception("scheduler: bad schedule for %r; skipping", name)
                continue
            if not due:
                continue
            self._last[name] = minute_key
            self._enqueue(name)
            fired.append(name)
        return fired

    def run_forever(self, interval: float = 30) -> None:
        """Tick forever, sleeping `interval` seconds between ticks."""
        while True:
            try:
                self.tick()
            except Exception:
                logger.exception("scheduler: tick raised; continuing")
            time.sleep(interval)
