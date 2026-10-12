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
