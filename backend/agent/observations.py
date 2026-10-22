"""Compact, LLM-friendly summaries of WeatherResults ("observations"). Never raw API JSON."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.services.models import Location, TimeWindow, WeatherAlert, WeatherPoint, WeatherResult

MAX_CHARS = 1500
ALERT_CHARS = 300

# Which DailyWeather temperature answers "what will the <time of day> be like".
PART_OF_DAY = {"morning": "temp_morning_c", "afternoon": "temp_day_c", "evening": "temp_evening_c",
               "night": "temp_night_c"}


@dataclass
class HourlyStats:
    """Aggregates over a run of hourly points."""

    temp_min: float
    temp_max: float
    rain_prob_max: float | None  # 0-1
    rain_prob_max_at: datetime | None
    rain_total_mm: float
    wind_max: float


def place_name(location: Location) -> str:
    """'Bandra West, Mumbai' for areas, 'Jaipur' for cities, 'Paris, France' abroad."""
    name = location.name
    if location.city and location.city != location.name:
        name += f", {location.city}"
    elif location.country_code and location.country_code != "in" and location.country:
        name += f", {location.country}"
    return name


def window_text(window: TimeWindow) -> str:
    """'Mon 19 Oct, 17:00-21:00 IST'; multi-day windows show both ends."""
    start, end = window.start, window.end
    ends_at_midnight = (end.hour, end.minute) == (0, 0) and end.date() == start.date() + timedelta(days=1)
    if end.date() == start.date() or ends_at_midnight:
        end_hm = "24:00" if ends_at_midnight else f"{end:%H:%M}"
        return f"{start:%a %d %b}, {start:%H:%M}-{end_hm} {start:%Z}"
    return f"{start:%a %d %b %H:%M} - {end:%a %d %b %H:%M} {start:%Z}"


def hourly_stats(points: list[WeatherPoint]) -> HourlyStats:
    probs = [p for p in points if p.rain_prob is not None]
    wettest = max(probs, key=lambda p: p.rain_prob) if probs else None
    return HourlyStats(
        temp_min=min(p.temp_c for p in points),
        temp_max=max(p.temp_c for p in points),
        rain_prob_max=wettest.rain_prob if wettest else None,
        rain_prob_max_at=wettest.time if wettest else None,
        rain_total_mm=sum(p.rain_mm or 0.0 for p in points),
        wind_max=max(p.wind_kmh for p in points),
    )


def summarize(result: WeatherResult, time_expr: str | None = None, max_chars: int = MAX_CHARS) -> str:
    """One observation, at most ~max_chars. Long hourly lists are sampled; aggregates always stay."""
    for step in (1, 2, 3, None):  # None: aggregates only
        text = _render(result, time_expr, hour_step=step, alert_chars=ALERT_CHARS)
        if len(text) <= max_chars:
            return text
    text = _render(result, time_expr, hour_step=None, alert_chars=100)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


# --- rendering -------------------------------------------------------------


def _render(result: WeatherResult, time_expr: str | None, hour_step: int | None, alert_chars: int) -> str:
    kind = {
        "current": "CURRENT CONDITIONS (observed now)",
        "hourly": "FORECAST, hourly",
        "daily": "FORECAST, daily",
        "historical": "PAST WEATHER (observed, not a forecast)",
    }[result.granularity]
    lines = [f"{place_name(result.location)} | {kind} | {window_text(result.window)}"]
    lines += [_alert(a, alert_chars) for a in result.alerts]

    if result.granularity == "current" and result.hourly:
        lines.append(_current(result.hourly[0]))
    elif result.hourly:
        lines += _hourly(result.hourly, hour_step)
    for day in result.daily:
        lines.append(_day(day, time_expr))
    if not result.hourly and not result.daily:
        lines.append("No data points in this window.")
    return "\n".join(lines)


def _alert(alert: WeatherAlert, max_chars: int) -> str:
    description = " ".join(alert.description.split())
    if len(description) > max_chars:
        description = description[: max_chars - 1] + "…"
    return (f"ALERT: {alert.event} ({alert.sender}), {alert.start:%a %d %b %H:%M} - {alert.end:%a %d %b %H:%M}: "
            f"{description}")


def _current(p: WeatherPoint) -> str:
    return (f"Now ({p.time:%H:%M}): {_t(p.temp_c)}, feels {_t(p.feels_like_c)}, humidity {p.humidity:.0f}%, "
            f"wind {p.wind_kmh:.0f} km/h, {p.description or p.condition or 'n/a'}")


def _hourly(points: list[WeatherPoint], step: int | None) -> list[str]:
    stats = hourly_stats(points)
    lines = []
    if step is not None:
        shown = points[::step]
        note = {1: "", 2: " (every 2nd hour)", 3: " (every 3rd hour)"}[step]
        lines.append(f"Hours{note}: time, temp, feels like, rain chance, wind, condition")
        lines += [_hour(p) for p in shown]
    rain = (f"max rain chance {_pct(stats.rain_prob_max)} at {stats.rain_prob_max_at:%a %H:%M}"
            if stats.rain_prob_max is not None else "rain chance n/a")
    lines.append(
        f"Summary of {len(points)} hours: temp {_t(stats.temp_min)} to {_t(stats.temp_max)}; {rain}; "
        f"total rain {stats.rain_total_mm:.1f} mm; max wind {stats.wind_max:.0f} km/h"
    )
    return lines


def _hour(p: WeatherPoint) -> str:
    rain = _pct(p.rain_prob) if p.rain_prob is not None else "n/a"
    return (f"- {p.time:%a %H:%M}: {_t(p.temp_c)}, feels {_t(p.feels_like_c)}, rain {rain}, "
            f"wind {p.wind_kmh:.0f} km/h, {p.description or p.condition or 'n/a'}")


def _day(d, time_expr: str | None) -> str:
    parts = [f"{d.date:%a %d %b}: {_t(d.temp_min_c)} to {_t(d.temp_max_c)}"]
    field = PART_OF_DAY.get((time_expr or "").lower())
    if field and getattr(d, field) is not None:
        parts.append(f"{time_expr.lower()} {_t(getattr(d, field))}")
    if d.rain_prob is not None:
        parts.append(f"rain chance {_pct(d.rain_prob)}")
    if d.rain_mm is not None:
        parts.append(f"rain {d.rain_mm:.1f} mm")
    if d.wind_kmh is not None:
        parts.append(f"wind {d.wind_kmh:.0f} km/h")
    if d.wind_max_kmh is not None:
        parts.append(f"{'gusts' if d.wind_kmh is not None else 'peak wind'} {d.wind_max_kmh:.0f} km/h")
    if d.uvi is not None:
        parts.append(f"UV {d.uvi:.0f}")
    text = ", ".join(parts)
    if d.summary or d.description:
        text += f". {d.summary or d.description}"
    return text


def _t(celsius: float) -> str:
    return f"{celsius:.1f}°C"


def _pct(fraction: float) -> str:
    return f"{fraction * 100:.0f}%"
