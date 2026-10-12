"""Tests for rendering stored UTC timestamps in the operator's local time."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from store_sync.core.timefmt import local_time


def _with_tz(offset_hours: float):
    """Context manager forcing a fixed local UTC offset via `TZ`."""
    import contextlib
    import time as _time

    @contextlib.contextmanager
    def _cm():
        old = os.environ.get("TZ")
        # Etc/GMT signs are inverted: Etc/GMT-7 is UTC+7.
        name = f"Etc/GMT{int(-offset_hours):+d}" if offset_hours else "Etc/GMT"
        os.environ["TZ"] = name
        _time.tzset()
        try:
            yield
        finally:
            if old is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old
            _time.tzset()

    return _cm()


@pytest.mark.skipif(not hasattr(__import__("time"), "tzset"),
                    reason="tzset unavailable")
def test_utc_string_shifts_to_local():
    with _with_tz(7):
        # 2026-10-12 03:00 UTC == 10:00 in UTC+7
        assert local_time("2026-10-12 03:00:00") == "2026-10-12 10:00:00"


@pytest.mark.skipif(not hasattr(__import__("time"), "tzset"),
                    reason="tzset unavailable")
def test_local_matches_device_now():
    with _with_tz(7):
        utc = datetime.now(timezone.utc).replace(microsecond=0)
        expected = utc.astimezone().strftime("%Y-%m-%d %H:%M:%S")
        assert local_time(utc.strftime("%Y-%m-%d %H:%M:%S")) == expected


def test_none_and_empty_pass_through():
    assert local_time(None) == ""
    assert local_time("") == ""


def test_unparseable_passes_through_unchanged():
    assert local_time("not a timestamp") == "not a timestamp"
