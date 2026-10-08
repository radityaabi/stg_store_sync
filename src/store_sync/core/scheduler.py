"""Scheduler — in-app minute-granular cron for enabled jobs (spec §7).

A tiny 5-field cron evaluator (``minute hour day-of-month month day-of-week``)
plus a thin loop that, once per tick, enqueues every enabled job whose schedule
is due and that has not already fired this minute. The Scheduler owns no thread:
:meth:`Scheduler.tick` is a pure step (testable with a fake clock) and
:meth:`Scheduler.run_forever` is the blocking driver the app runs on its own
daemon thread.

Supported field syntax: ``*``, a single number, a range ``a-b``, a list
``a,b``, and a step ``*/n`` (or ``a-b/n``). An empty schedule is never due.
Day-of-week uses cron numbering where both ``0`` and ``7`` mean Sunday; it is
mapped onto :meth:`datetime.weekday` (Monday=0).
"""
from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Callable

logger = logging.getLogger(__name__)


def _parse_field(spec: str, lo: int, hi: int) -> set[int] | None:
    """Expand one cron field into the set of matching ints, or None if bad."""
    try:
        allowed: set[int] = set()
        for part in spec.split(","):
            part = part.strip()
            if part == "":
                return None
            step = 1
            if "/" in part:
                base, _, step_s = part.partition("/")
                step = int(step_s)
                if step <= 0:
                    return None
            else:
                base = part
            if base == "*":
                start, end = lo, hi
            elif "-" in base:
                a_s, _, b_s = base.partition("-")
                start, end = int(a_s), int(b_s)
            else:
                start = end = int(base)
            if start < lo or end > hi or start > end:
                return None
            allowed.update(range(start, end + 1, step))
    except (ValueError, TypeError):
        return None
    return allowed


def is_due(schedule: str, now: datetime) -> bool:
    """True if `schedule` (5-field cron) matches `now` to the minute."""
    if not schedule or not schedule.strip():
        return False
    fields = schedule.split()
    if len(fields) != 5:
        return False
    # cron Sunday is 0 or 7; datetime.weekday() is Monday=0.
    cron_dow = (now.weekday() + 1) % 7
    specs = [
        (fields[0], 0, 59, now.minute),
        (fields[1], 0, 23, now.hour),
        (fields[2], 1, 31, now.day),
        (fields[3], 1, 12, now.month),
        (fields[4], 0, 7, cron_dow),
    ]
    for spec, lo, hi, value in specs:
        allowed = _parse_field(spec, lo, hi)
        if allowed is None:
            return False
        if hi == 7 and 7 in allowed:  # day-of-week: normalize 7 -> 0 (Sun)
            allowed = (allowed - {7}) | {0}
        if value not in allowed:
            return False
    return True


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
                due = is_due(getattr(entry, "schedule", "") or "", now)
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
