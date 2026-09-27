from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from music_agent.periods import PERIOD_NAMES, PeriodError, resolve

LA = ZoneInfo("America/Los_Angeles")
UTC = timezone.utc


def utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def test_period_names():
    assert PERIOD_NAMES == ("today", "last_24h", "this_week", "past_week", "this_month", "this_year")


def test_today_starts_at_local_midnight():
    now = utc("2026-09-27T18:36:00")  # 11:36 PDT, Sunday
    p = resolve("today", None, None, LA, now)
    assert p.start == utc("2026-09-27T07:00:00")
    assert p.end == now
    assert p.label == "today"


def test_last_24h_and_past_week_are_rolling():
    now = utc("2026-09-27T18:36:00")
    assert resolve("last_24h", None, None, LA, now).start == now - timedelta(hours=24)
    assert resolve("past_week", None, None, LA, now).start == now - timedelta(days=7)


def test_this_week_on_sunday_goes_back_to_monday():
    now = utc("2026-09-27T18:36:00")  # Sunday
    assert resolve("this_week", None, None, LA, now).start == utc("2026-09-21T07:00:00")


def test_this_week_just_after_monday_midnight():
    now = utc("2026-09-28T07:30:00")  # Monday 00:30 PDT
    assert resolve("this_week", None, None, LA, now).start == utc("2026-09-28T07:00:00")


def test_this_week_across_dst_start():
    now = utc("2026-03-08T19:00:00")  # Sunday 12:00 PDT; DST began 02:00 that day
    p = resolve("this_week", None, None, LA, now)
    assert p.start == utc("2026-03-02T08:00:00")  # Monday 00:00 PST
    assert resolve("today", None, None, LA, now).start == utc("2026-03-08T08:00:00")


def test_this_month():
    now = utc("2026-09-27T18:36:00")
    assert resolve("this_month", None, None, LA, now).start == utc("2026-09-01T07:00:00")


def test_this_year_just_after_new_year():
    now = utc("2027-01-01T08:10:00")  # 00:10 PST Jan 1
    assert resolve("this_year", None, None, LA, now).start == utc("2027-01-01T08:00:00")


def test_explicit_dates_end_is_inclusive_day():
    now = utc("2026-10-15T00:00:00")
    p = resolve(None, "2026-09-01", "2026-09-30", LA, now)
    assert p.start == utc("2026-09-01T07:00:00")
    assert p.end == utc("2026-10-01T07:00:00")


def test_explicit_start_only_ends_now():
    now = utc("2026-09-27T18:36:00")
    p = resolve(None, "2026-09-20T09:00", None, LA, now)
    assert p.start == utc("2026-09-20T16:00:00")
    assert p.end == now


def test_period_name_is_case_insensitive():
    now = utc("2026-09-27T18:36:00")
    assert resolve(" Today ", None, None, LA, now).label == "today"


@pytest.mark.parametrize(
    ("period", "start", "end", "message"),
    [
        ("yesteryear", None, None, "Unknown period"),
        ("today", "2026-09-01", None, "either"),
        (None, None, None, "Give a period"),
        (None, "2026-09-10", "2026-09-01", "after"),
        (None, "not-a-date", None, "not-a-date"),
    ],
)
def test_errors(period, start, end, message):
    with pytest.raises(PeriodError, match=message):
        resolve(period, start, end, LA, utc("2026-09-27T18:36:00"))
