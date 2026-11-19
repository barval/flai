# tests/test_scheduler_recurrence.py
"""When does a scheduled item fire next?

One closed function with a table of cases, so the behaviour is checkable without a clock,
a database or a thread.
"""

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest

from app.scheduler import next_after

pytestmark = pytest.mark.unit

MOSCOW = "Europe/Moscow"


def _item(**over):
    item = {
        "repeat": "daily",
        "every_minutes": None,
        "weekday": None,
        "weekdays": None,
        "day_of_month": None,
        "time_of_day": time(9, 0),
        "timezone": MOSCOW,
    }
    item.update(over)
    return item


def _moment(text: str, tz: str = MOSCOW) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(tz))


def test_once_has_no_next():
    assert next_after(_item(repeat="once"), _moment("2026-11-18T09:00")) is None


def test_minutes_adds_the_interval():
    item = _item(repeat="minutes", every_minutes=15, time_of_day=None)

    assert next_after(item, _moment("2026-11-18T09:00")) == _moment("2026-11-18T09:15")


def test_minutes_refuses_a_busy_loop():
    item = _item(repeat="minutes", every_minutes=1, time_of_day=None)

    assert next_after(item, _moment("2026-11-18T09:00")) == _moment("2026-11-18T09:15")


def test_daily_keeps_the_local_time():
    assert next_after(_item(), _moment("2026-11-18T09:00")) == _moment("2026-11-19T09:00")


def test_daily_keeps_the_wall_clock_across_a_dst_change():
    """A schedule at 09:00 is 09:00 for the person, not +24 h of absolute time."""
    berlin = "Europe/Berlin"
    item = _item(timezone=berlin)
    moment = _moment("2027-03-27T09:00", berlin)

    following = next_after(item, moment)

    assert following.hour == 9
    assert following.utcoffset() != moment.utcoffset()  # the clocks did change


def test_weekly_lands_on_the_weekday():
    item = _item(repeat="weekly", weekday=4)  # Friday

    assert next_after(item, _moment("2026-11-18T09:00")) == _moment("2026-11-20T09:00")


def test_weekly_covers_a_set_of_days():
    """«Every Tuesday and Thursday» is one schedule, not two."""
    item = _item(repeat="weekly", weekdays=[1, 3])  # Tuesday, Thursday

    assert next_after(item, _moment("2026-11-18T09:00")) == _moment("2026-11-19T09:00")


def test_weekly_set_skips_a_day_whose_moment_already_passed():
    item = _item(repeat="weekly", weekdays=[1, 3])

    # Thursday 09:00 has fired; the next Tuesday is sooner than the next Thursday.
    assert next_after(item, _moment("2026-11-19T10:00")) == _moment("2026-11-24T09:00")


def test_weekly_set_keeps_the_wall_clock_across_a_dst_change():
    berlin = "Europe/Berlin"
    item = _item(repeat="weekly", weekdays=[6, 0], timezone=berlin)  # Sunday, Monday
    moment = _moment("2027-03-26T09:00", berlin)

    following = next_after(item, moment)

    assert following.weekday() == 6
    assert following.hour == 9
    assert following.utcoffset() != moment.utcoffset()


def test_an_unusable_weekday_set_falls_back_to_a_known_day():
    """A broken set must still produce a schedule instead of an endless loop."""
    item = _item(repeat="weekly", weekdays=[9])

    assert next_after(item, _moment("2026-11-18T09:00")) == _moment("2026-11-23T09:00")


def test_monthly_with_a_missing_day_uses_the_last_one():
    item = _item(repeat="monthly", day_of_month=31)

    following = next_after(item, _moment("2027-01-31T09:00"))

    assert following.month == 2
    assert following.day == 28


def test_a_past_daily_time_rolls_to_tomorrow():
    assert next_after(_item(), _moment("2026-11-18T11:30")) == _moment("2026-11-19T09:00")


def test_the_zone_of_the_item_is_used_not_the_server_one():
    tokyo = _item(timezone="Asia/Tokyo")

    following = next_after(tokyo, datetime(2026, 11, 18, 0, 0, tzinfo=ZoneInfo("Asia/Tokyo")))

    assert following.tzinfo is not None
    assert following.astimezone(ZoneInfo("Asia/Tokyo")).hour == 9
