"""Schedule model: units, bounds, round-trip, and human() labels."""
from store_sync.core.config import SCHEDULE_UNITS, Schedule, human


def test_schedule_units():
    assert SCHEDULE_UNITS == ("", "minute", "hour", "day", "week")


def test_human_labels():
    assert human(Schedule("")) == "manual only"
    assert human(Schedule("minute", 1)) == "every minute"
    assert human(Schedule("minute", 5)) == "every 5 minutes"
    assert human(Schedule("hour", 1)) == "hourly"
    assert human(Schedule("hour", 2)) == "every 2 hours"
    assert human(Schedule("day", 1, "02:00")) == "daily at 02:00"
    assert human(Schedule("day", 3, "06:30")) == "every 3 days at 06:30"
    assert human(Schedule("week", 1, "02:00", 0)) == "weekly on Monday at 02:00"
    assert human(Schedule("week", 2, "02:00", 0)) == "every 2 weeks on Monday at 02:00"


def test_human_malformed_falls_back():
    assert human(Schedule("nope")) == "manual only"
    assert human(Schedule("day", 1)) == "manual only"  # missing `at`
    assert human(Schedule("week", 1)) == "manual only"


def test_validate_bounds():
    assert Schedule("minute", 60).validate()  # 60 out of 1..59
    assert Schedule("hour", 0).validate()  # 0 out of 1..23
    assert Schedule("day", 1, "").validate()  # day needs `at`
    assert Schedule("week", 1, "02:00", 9).validate()  # weekday out of 0..6
    assert Schedule("nope").validate()  # unknown unit
    assert Schedule("day", 1, "02:00").validate() == []
    assert Schedule("week", 2, "06:30", 4).validate() == []


def test_validate_every_bounds_and_at_format():
    assert Schedule("minute", 0).validate()
    assert Schedule("hour", 24).validate()
    assert Schedule("day", 32).validate()
    assert Schedule("day", 1, "2:00").validate()  # at must be HH:MM zero-padded
    assert Schedule("day", 1, "24:00").validate()
    assert Schedule("day", 1, "02:60").validate()
    assert Schedule("week", 0, "02:00").validate()
    assert Schedule("week", 53, "02:00").validate()
    assert Schedule("week", 1, "02:00", -1).validate()
    assert Schedule("").validate() == []  # manual-only is valid
    assert Schedule("minute", 30).validate() == []
    assert Schedule("hour", 12).validate() == []


def test_round_trip():
    s = Schedule("week", 2, "02:00", 3)
    assert Schedule.from_dict(s.to_dict()) == s


def test_from_dict_coerces_and_defaults():
    assert Schedule.from_dict(None) == Schedule()
    assert Schedule.from_dict({}) == Schedule()
    assert Schedule.from_dict({"unit": "day", "every": "3", "at": "06:30"}) == Schedule(
        "day", 3, "06:30"
    )
    assert Schedule.from_dict({"unit": "week", "weekday": "5"}) == Schedule(
        "week", 1, "", 5
    )


def test_to_dict_keys():
    assert Schedule("day", 3, "06:30").to_dict() == {
        "unit": "day",
        "every": 3,
        "at": "06:30",
        "weekday": 0,
    }


def test_frozen():
    s = Schedule()
    try:
        s.unit = "minute"  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Schedule must be frozen")
