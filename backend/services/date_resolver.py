"""Resolve date/time expressions like ("tomorrow", "evening") into concrete time windows.

All resolution happens in the location's IANA timezone, never the server's.
"""

import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.services.errors import InvalidInputError, UnrecognizedDateError
from backend.services.models import TimeWindow

# Local-time hour ranges (start, end). An end <= start crosses into the next day.
TIME_OF_DAY: dict[str, tuple[int, int]] = {
    "morning": (6, 12),
    "afternoon": (12, 17),
    "evening": (17, 21),
    "night": (21, 6),
    "full_day": (0, 24),
}

RELATIVE_DAYS: dict[str, int] = {
    "today": 0,
    "tomorrow": 1,
    "day_after_tomorrow": 2,
    "yesterday": -1,
    "day_before_yesterday": -2,
}

WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
THIS_WEEKEND = "this_weekend"

# Every date expression resolve() accepts, and the named times of day. Built from the
# constants above so callers (e.g. the LLM prompt) can't drift from what is supported.
DATE_EXPRESSIONS: list[str] = [*RELATIVE_DAYS, *(f"next_{day}" for day in WEEKDAYS), THIS_WEEKEND]
TIME_EXPRESSIONS: list[str] = list(TIME_OF_DAY)

# One clock time: "7am", "7_am", "7:30pm", "07:00", "19:00". Underscores stand in for spaces.
_CLOCK = r"(\d{1,2})(?::(\d{2}))?_?(am|pm)?"
_RANGE_RE = re.compile(rf"^{_CLOCK}_?(?:-|to)_?{_CLOCK}$")

_MINUTES_PER_DAY = 24 * 60


def resolve(
    date_expr: str,
    time_expr: str | None,
    tz: str,
    now: datetime | None = None,
) -> list[TimeWindow]:
    """Resolve a date and time-of-day expression into one or more windows in `tz`.

    Most expressions give a single window. `this_weekend` with a time of day gives one
    window per remaining weekend day; `this_weekend` windows that have already ended
    are dropped and one in progress is trimmed to start at `now`.

    Raises UnrecognizedDateError for unknown expressions, an unknown timezone, or a
    weekend with no time left in it.
    """
    zone = _zone(tz)
    now = _local_now(now, zone)
    time_key = _normalize(time_expr or "")
    start_min, end_min = _parse_time(time_key)
    date_key = _normalize(date_expr)

    if date_key == THIS_WEEKEND:
        return _weekend_windows(now, zone, start_min, end_min, whole_day=time_key in ("", "full_day"))

    day = _resolve_day(date_key, now.date())
    return [TimeWindow(start=_at(day, start_min, zone), end=_at(day, end_min, zone))]


def is_supported_time(time_expr: str | None) -> bool:
    """True if `time_expr` is a named time of day, an explicit range resolve() accepts, or empty."""
    try:
        _parse_time(_normalize(time_expr or ""))
    except UnrecognizedDateError:
        return False
    return True


def _normalize(expr: str) -> str:
    """Lowercase, trim, and join words with underscores ("Day After" -> "day_after")."""
    return "_".join(expr.lower().replace("_", " ").split())


def _zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnrecognizedDateError(f"Unknown timezone {tz!r}; expected an IANA name like 'Asia/Kolkata'.") from exc


def _local_now(now: datetime | None, zone: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(zone)
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidInputError("`now` must be timezone-aware")
    return now.astimezone(zone)


def _at(day: date, minutes: int, zone: ZoneInfo) -> datetime:
    """Local wall-clock time `minutes` after midnight of `day` (may roll into later days)."""
    days, minute_of_day = divmod(minutes, _MINUTES_PER_DAY)
    hour, minute = divmod(minute_of_day, 60)
    d = day + timedelta(days=days)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=zone)


def _parse_time(key: str) -> tuple[int, int]:
    """Return (start, end) in minutes after midnight; end > start, possibly past 24:00."""
    if key == "":
        key = "full_day"
    if key in TIME_OF_DAY:
        start_h, end_h = TIME_OF_DAY[key]
        start, end = start_h * 60, end_h * 60
    else:
        start, end = _parse_range(key)
    if end <= start:
        end += _MINUTES_PER_DAY
    return start, end


def _parse_range(key: str) -> tuple[int, int]:
    """Parse an explicit range like "7am-9am", "07:00-09:00" or "7-9am" into minutes.

    A start without am/pm inherits the end's ("7-9am" -> 07:00-09:00). If that would put
    the start at or after the end, the start flips to the other half of the day
    ("11-1pm" -> 11:00-13:00, "10-2am" -> 22:00-02:00).
    """
    match = _RANGE_RE.match(key)
    if not match:
        raise UnrecognizedDateError(
            f"Unrecognized time expression {key!r}. Use one of {sorted(TIME_OF_DAY)} "
            "or a range like '7am-9am', '7-9am' or '07:00-09:00'."
        )
    start_h, start_m, start_mer, end_h, end_m, end_mer = match.groups()
    if end_mer is None and end_m is None:
        raise UnrecognizedDateError(
            f"Time range {key!r} is missing am/pm on the end time. Add am/pm, e.g. 7-9am or 7am-9am."
        )
    if start_mer is None and start_m is None and end_mer is None:
        raise UnrecognizedDateError(
            f"Time range {key!r} is missing am/pm on the start time. "
            "Add am/pm, e.g. 7am-9am, or use 24-hour times, e.g. 07:00-09:00."
        )

    inherited = start_mer is None and end_mer is not None
    start = _clock_minutes(start_h, start_m, start_mer or end_mer, raw=key)
    end = _clock_minutes(end_h, end_m, end_mer, raw=key)
    if start == end:
        raise UnrecognizedDateError(f"Time range {key!r} has the same start and end.")
    if inherited and start > end:
        start = (start + 12 * 60) % _MINUTES_PER_DAY
    return start, end


def _clock_minutes(hour_s: str, minute_s: str | None, meridiem: str | None, raw: str) -> int:
    """Convert one parsed clock time to minutes after midnight, validating it strictly."""
    hour, minute = int(hour_s), int(minute_s or 0)
    if meridiem:
        if not 1 <= hour <= 12:
            raise UnrecognizedDateError(f"Invalid 12-hour time in {raw!r}.")
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    if hour > 23 or minute > 59:
        raise UnrecognizedDateError(f"Invalid clock time in {raw!r}.")
    return hour * 60 + minute


def _resolve_day(key: str, today: date) -> date:
    if key in RELATIVE_DAYS:
        return today + timedelta(days=RELATIVE_DAYS[key])
    if key.startswith("next_") and key[5:] in WEEKDAYS:
        # Strictly after today: asked on a Monday, next_monday is 7 days ahead.
        days_ahead = (WEEKDAYS.index(key[5:]) - today.weekday() - 1) % 7 + 1
        return today + timedelta(days=days_ahead)
    raise UnrecognizedDateError(
        f"Unrecognized date expression {key!r}. Use one of {sorted(RELATIVE_DAYS)}, "
        "'next_<weekday>' or 'this_weekend'."
    )


def _weekend_windows(
    now: datetime, zone: ZoneInfo, start_min: int, end_min: int, whole_day: bool
) -> list[TimeWindow]:
    """Windows for the current week's Saturday and Sunday, clipped to the future."""
    today = now.date()
    saturday = today + timedelta(days=5 - today.weekday())  # Sunday gives yesterday
    if whole_day:
        spans = [(_at(saturday, 0, zone), _at(saturday, 2 * _MINUTES_PER_DAY, zone))]
    else:
        spans = [
            (_at(day, start_min, zone), _at(day, end_min, zone))
            for day in (saturday, saturday + timedelta(days=1))
        ]

    windows = [TimeWindow(start=max(start, now), end=end) for start, end in spans if end > now]
    if not windows:
        raise UnrecognizedDateError(
            f"No part of this weekend's requested time is left as of {now:%A %H:%M} ({zone.key})."
        )
    return windows
