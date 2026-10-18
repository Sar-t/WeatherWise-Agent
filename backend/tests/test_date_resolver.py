import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from backend.services.date_resolver import resolve
from backend.services.errors import UnrecognizedDateError
from backend.services.models import TimeWindow

IST = ZoneInfo("Asia/Kolkata")
LONDON = ZoneInfo("Europe/London")
TZ = "Asia/Kolkata"


def ist(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=IST)


NOW = ist(2026, 10, 18, 13)  # Sunday 13:00 IST
MONDAY = ist(2026, 10, 19, 9)
WEDNESDAY = ist(2026, 10, 14, 10)
SATURDAY_AFTERNOON = ist(2026, 10, 17, 15)
SUNDAY_NIGHT = ist(2026, 10, 18, 22)


def one(date_expr: str, time_expr: str | None = None, tz: str = TZ, now: datetime = NOW) -> TimeWindow:
    windows = resolve(date_expr, time_expr, tz, now=now)
    assert len(windows) == 1
    return windows[0]


def bounds(windows: list[TimeWindow]) -> list[tuple[datetime, datetime]]:
    return [(w.start, w.end) for w in windows]


# --- relative days ---------------------------------------------------------


@pytest.mark.parametrize(
    "expr, day",
    [
        ("today", 18),
        ("tomorrow", 19),
        ("day_after_tomorrow", 20),
        ("yesterday", 17),
        ("day_before_yesterday", 16),
    ],
)
def test_relative_days_full_day(expr: str, day: int) -> None:
    w = one(expr)
    assert (w.start, w.end) == (ist(2026, 10, day), ist(2026, 10, day + 1))


@pytest.mark.parametrize("expr", ["Day After Tomorrow", "  day after_tomorrow  ", "DAY_AFTER_TOMORROW"])
def test_input_normalization(expr: str) -> None:
    assert one(expr, " Morning ").start == ist(2026, 10, 20, 6)


# --- time of day -----------------------------------------------------------


@pytest.mark.parametrize(
    "time_expr, start, end",
    [
        ("morning", ist(2026, 10, 19, 6), ist(2026, 10, 19, 12)),
        ("afternoon", ist(2026, 10, 19, 12), ist(2026, 10, 19, 17)),
        ("evening", ist(2026, 10, 19, 17), ist(2026, 10, 19, 21)),
        ("night", ist(2026, 10, 19, 21), ist(2026, 10, 20, 6)),
        ("full_day", ist(2026, 10, 19), ist(2026, 10, 20)),
        ("full day", ist(2026, 10, 19), ist(2026, 10, 20)),
        ("", ist(2026, 10, 19), ist(2026, 10, 20)),
        (None, ist(2026, 10, 19), ist(2026, 10, 20)),
    ],
)
def test_time_of_day(time_expr: str | None, start: datetime, end: datetime) -> None:
    w = one("tomorrow", time_expr)
    assert (w.start, w.end) == (start, end)


# --- next_<weekday> --------------------------------------------------------


def test_next_monday_asked_on_sunday() -> None:
    assert one("next_monday").start == ist(2026, 10, 19)


def test_next_monday_asked_on_monday_is_a_week_ahead() -> None:
    assert one("next_monday", now=MONDAY).start == ist(2026, 10, 26)


def test_next_sunday_asked_on_sunday_is_a_week_ahead() -> None:
    assert one("next sunday", "evening").start == ist(2026, 10, 25, 17)


# --- this_weekend ----------------------------------------------------------


def test_this_weekend_on_weekday_is_whole_weekend() -> None:
    w = one("this_weekend", now=WEDNESDAY)
    assert (w.start, w.end) == (ist(2026, 10, 17), ist(2026, 10, 19))


def test_this_weekend_on_saturday_starts_now() -> None:
    w = one("this_weekend", now=SATURDAY_AFTERNOON)
    assert (w.start, w.end) == (SATURDAY_AFTERNOON, ist(2026, 10, 19))


def test_this_weekend_on_sunday_starts_now() -> None:
    w = one("this_weekend", "full_day")
    assert (w.start, w.end) == (NOW, ist(2026, 10, 19))


def test_weekend_mornings_on_weekday_gives_both_days() -> None:
    windows = resolve("this_weekend", "morning", TZ, now=WEDNESDAY)
    assert bounds(windows) == [
        (ist(2026, 10, 17, 6), ist(2026, 10, 17, 12)),
        (ist(2026, 10, 18, 6), ist(2026, 10, 18, 12)),
    ]


def test_weekend_mornings_on_saturday_afternoon_gives_only_sunday() -> None:
    windows = resolve("this_weekend", "morning", TZ, now=SATURDAY_AFTERNOON)
    assert bounds(windows) == [(ist(2026, 10, 18, 6), ist(2026, 10, 18, 12))]


def test_weekend_mornings_on_sunday_night_raises() -> None:
    with pytest.raises(UnrecognizedDateError, match="weekend"):
        resolve("this_weekend", "morning", TZ, now=SUNDAY_NIGHT)


def test_weekend_window_in_progress_is_trimmed_to_now() -> None:
    windows = resolve("this_weekend", "afternoon", TZ, now=SATURDAY_AFTERNOON)
    assert bounds(windows) == [
        (SATURDAY_AFTERNOON, ist(2026, 10, 17, 17)),
        (ist(2026, 10, 18, 12), ist(2026, 10, 18, 17)),
    ]


def test_weekend_with_explicit_range() -> None:
    windows = resolve("this_weekend", "7am-9am", TZ, now=WEDNESDAY)
    assert bounds(windows) == [
        (ist(2026, 10, 17, 7), ist(2026, 10, 17, 9)),
        (ist(2026, 10, 18, 7), ist(2026, 10, 18, 9)),
    ]


def test_weekend_nights_on_sunday_keeps_only_sunday_night() -> None:
    windows = resolve("this_weekend", "night", TZ)
    assert bounds(windows) == [(ist(2026, 10, 18, 21), ist(2026, 10, 19, 6))]


# --- explicit ranges -------------------------------------------------------


@pytest.mark.parametrize(
    "time_expr, start, end",
    [
        ("7am-9am", ist(2026, 10, 19, 7), ist(2026, 10, 19, 9)),
        ("07:00-09:00", ist(2026, 10, 19, 7), ist(2026, 10, 19, 9)),
        ("6pm-8pm", ist(2026, 10, 19, 18), ist(2026, 10, 19, 20)),
        ("18:30-20:15", ist(2026, 10, 19, 18, 30), ist(2026, 10, 19, 20, 15)),
        ("7:30am - 9:15am", ist(2026, 10, 19, 7, 30), ist(2026, 10, 19, 9, 15)),
        ("7 AM to 9 AM", ist(2026, 10, 19, 7), ist(2026, 10, 19, 9)),
        ("12am-1am", ist(2026, 10, 19, 0), ist(2026, 10, 19, 1)),
        ("11am-12pm", ist(2026, 10, 19, 11), ist(2026, 10, 19, 12)),
        ("10pm-2am", ist(2026, 10, 19, 22), ist(2026, 10, 20, 2)),
        ("23:00-01:00", ist(2026, 10, 19, 23), ist(2026, 10, 20, 1)),
    ],
)
def test_explicit_ranges(time_expr: str, start: datetime, end: datetime) -> None:
    w = one("tomorrow", time_expr)
    assert (w.start, w.end) == (start, end)


@pytest.mark.parametrize(
    "time_expr, start, end",
    [
        # Start inherits the end's am/pm.
        ("7-9am", ist(2026, 10, 19, 7), ist(2026, 10, 19, 9)),
        ("6-8pm", ist(2026, 10, 19, 18), ist(2026, 10, 19, 20)),
        ("7:30-9am", ist(2026, 10, 19, 7, 30), ist(2026, 10, 19, 9)),
        ("12-1pm", ist(2026, 10, 19, 12), ist(2026, 10, 19, 13)),
        # Inheriting would put start >= end, so the start flips half of the day.
        ("11-1pm", ist(2026, 10, 19, 11), ist(2026, 10, 19, 13)),
        ("10-2am", ist(2026, 10, 19, 22), ist(2026, 10, 20, 2)),
        # Optional spaces.
        ("7 - 9 am", ist(2026, 10, 19, 7), ist(2026, 10, 19, 9)),
        ("7-9 am", ist(2026, 10, 19, 7), ist(2026, 10, 19, 9)),
    ],
)
def test_shared_meridiem_ranges(time_expr: str, start: datetime, end: datetime) -> None:
    w = one("tomorrow", time_expr)
    assert (w.start, w.end) == (start, end)


@pytest.mark.parametrize(
    "time_expr, message",
    [
        ("7-9", "missing am/pm on the end time. Add am/pm, e.g. 7-9am"),
        ("7 - 9", "missing am/pm on the end time. Add am/pm, e.g. 7-9am"),
        ("7am-9", "missing am/pm on the end time. Add am/pm"),
        ("7-09:00", "missing am/pm on the start time. Add am/pm"),
        ("7am-7am", "same start and end"),
        ("9-9am", "same start and end"),
    ],
)
def test_rejected_ranges_say_what_is_missing(time_expr: str, message: str) -> None:
    with pytest.raises(UnrecognizedDateError, match=re.escape(message)):
        resolve("tomorrow", time_expr, TZ, now=NOW)


# --- timezones -------------------------------------------------------------


def test_non_indian_timezone_is_respected() -> None:
    # Sunday 13:00 IST is Sunday 08:30 in London (BST, UTC+1).
    w = one("tomorrow", "morning", tz="Europe/London")
    assert w.start == datetime(2026, 10, 19, 6, tzinfo=LONDON)
    assert w.start.astimezone(timezone.utc) == datetime(2026, 10, 19, 5, tzinfo=timezone.utc)


def test_today_uses_local_date_not_now_date() -> None:
    # Sunday 03:00 IST is still Saturday 22:30 in London.
    w = one("today", tz="Europe/London", now=ist(2026, 10, 18, 3))
    assert w.start == datetime(2026, 10, 17, tzinfo=LONDON)


def test_full_day_across_dst_change() -> None:
    # UK clocks go back on Sunday 2026-10-25, so that local day lasts 25 hours.
    w = one("next_sunday", tz="Europe/London")
    assert w.start == datetime(2026, 10, 25, tzinfo=LONDON)
    # Same-tzinfo subtraction is wall-clock in Python, so measure elapsed time in UTC.
    assert w.end.astimezone(timezone.utc) - w.start.astimezone(timezone.utc) == timedelta(hours=25)


def test_default_now_returns_aware_window() -> None:
    w = resolve("tomorrow", "morning", "Asia/Kolkata")[0]
    assert w.start.utcoffset() == timedelta(hours=5, minutes=30)


# --- invalid input ---------------------------------------------------------


@pytest.mark.parametrize("expr", ["", "someday", "next_week", "next_funday", "last_monday", "in 3 days"])
def test_invalid_date_expression_raises(expr: str) -> None:
    with pytest.raises(UnrecognizedDateError, match="date expression"):
        resolve(expr, "morning", TZ, now=NOW)


@pytest.mark.parametrize(
    "expr",
    ["brunch", "midday", "7-9", "7am", "13pm-2pm", "25:00-26:00", "07:60-08:00", "7am-9am-11am", "7am-7am"],
)
def test_invalid_time_expression_raises(expr: str) -> None:
    with pytest.raises(UnrecognizedDateError):
        resolve("tomorrow", expr, TZ, now=NOW)


def test_unknown_timezone_raises() -> None:
    with pytest.raises(UnrecognizedDateError, match="timezone"):
        resolve("today", None, "Mars/Olympus_Mons", now=NOW)


def test_naive_now_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve("today", None, TZ, now=datetime(2026, 10, 18, 13))


# --- TimeWindow validation -------------------------------------------------


def test_time_window_accepts_aware_increasing_range() -> None:
    w = TimeWindow(start=ist(2026, 10, 18, 6), end=ist(2026, 10, 18, 12))
    assert w.end > w.start


@pytest.mark.parametrize(
    "start, end",
    [
        (datetime(2026, 10, 18, 6), ist(2026, 10, 18, 12)),
        (ist(2026, 10, 18, 6), datetime(2026, 10, 18, 12)),
    ],
)
def test_time_window_rejects_naive_datetimes(start: datetime, end: datetime) -> None:
    with pytest.raises(ValidationError, match="timezone"):
        TimeWindow(start=start, end=end)


@pytest.mark.parametrize("end", [ist(2026, 10, 18, 6), ist(2026, 10, 18, 5)])
def test_time_window_rejects_end_not_after_start(end: datetime) -> None:
    with pytest.raises(ValidationError, match="must be after start"):
        TimeWindow(start=ist(2026, 10, 18, 6), end=end)
