"""Render stored UTC timestamps in the operator's local time (display only).

The state DB stamps rows with SQLite's `datetime('now')`, which is **always
UTC** regardless of the host's timezone. The GUI/CLI therefore keep storing UTC
(single, unambiguous instant) and convert only at the point of display, so a
run started at 03:00 UTC reads as 10:00 on a UTC+7 device.
"""
from __future__ import annotations

from datetime import datetime, timezone

_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M")


def local_time(value: str | None) -> str:
    """Convert a stored UTC timestamp to the device's local time.

    `None`/empty renders as `""`. A value that is not a recognised UTC stamp is
    returned unchanged, so a legacy or foreign string is never mangled.
    """
    if not value:
        return ""
    for fmt in _FORMATS:
        try:
            utc = datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        return utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    return value
