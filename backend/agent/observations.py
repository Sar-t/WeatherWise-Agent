"""Compact, LLM-friendly summaries of WeatherResults ("observations"). Never raw API JSON.

Values are display-ready, so the answer model can copy them as they are: temperatures in whole °C,
rain chance to the nearest 5%, wind and UV whole numbers. Cards keep the precise values.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.services.display import celsius as _t
from backend.services.display import chance as _pct
from backend.services.display import kmh as _kmh
from backend.services.display import whole as _int
from backend.services.scoring.blocks import block_reasons
from backend.services.scoring.models import ActivityAssessment
from backend.services.models import (
    AirQualityPoint,
    AirQualityResult,
    Location,
    TimeWindow,
    WeatherAlert,
    WeatherPoint,
    WeatherResult,
)

MAX_CHARS = 1500
AIR_MAX_CHARS = 1000
ALERT_CHARS = 300
OWM_AQI_NAMES = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}  # OpenWeather's 1-5 scale

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


def summarize_air(result: AirQualityResult, max_chars: int = AIR_MAX_CHARS) -> str:
    """One air quality observation: India's NAQI where we have it, else OpenWeather's 1-5 scale."""
    for step in (1, 2, 3, None):
        text = _render_air(result, step)
        if len(text) <= max_chars:
            return text
    return text[: max_chars - 1] + "…"


ASSESSMENT_MAX_CHARS = 1500
# Which assessment sections come first, by what was asked.
SECTION_ORDER = {
    "suitability": ("reasons", "best", "avoid", "hints"),
    "best_time": ("best", "reasons", "avoid", "hints"),
    "avoid": ("avoid", "best", "reasons", "hints"),
    "packing": ("hints", "reasons", "best", "avoid"),
    "travel_timing": ("best", "avoid", "reasons", "hints"),
}


def summarize_assessment(a: ActivityAssessment, recommendation_type: str | None = None,
                         intent: str | None = None, max_chars: int = ASSESSMENT_MAX_CHARS) -> str:
    """The scoring engine's verdict, best/avoid times, reasons and hints for one place.

    Sections are ordered by `recommendation_type` (or, without one, by intent: clothing -> packing,
    travel -> travel_timing, else suitability). Data notes go last and are dropped first if too long.
    """
    kind = recommendation_type or {"clothing_recommendation": "packing",
                                   "travel_recommendation": "travel_timing"}.get(intent or "", "suitability")
    windows = "; ".join(window_text(w) for w in a.windows)
    lines = [f"{place_name(a.location)} | ACTIVITY ASSESSMENT ({a.profile.replace('_', ' ')}) | {windows}"]
    if a.is_daily_estimate:
        lines.append("DAILY ESTIMATE: rough outlook from daily forecasts, lower confidence")
    lines.append(f"VERDICT (from the scoring engine; explain it, don't change it): "
                 f"{a.verdict.replace('_', ' ')}, score {a.score}/100")

    parts = {p.time: p.part_of_day for p in a.hourly if p.part_of_day}

    def block(b) -> str:
        part = f" ({parts[b.start]})" if b.start in parts else ""
        why = f": {'; '.join(b.reasons)}" if b.reasons else ""
        return f"{_block_time(b)}{part}, score {b.average_score} ({b.verdict.replace('_', ' ')}){why}"

    reasons = a.best_blocks[0].reasons if a.best_blocks else block_reasons(a.hourly, 3)
    sections = {
        "reasons": [f"Main reasons: {'; '.join(reasons)}" if reasons else "Main reasons: no factor lost points"],
        "best": ([f"- Best time: {block(b)}" for b in a.best_blocks] or ["Best time: none in the requested time"]),
        "avoid": [f"- Avoid: {block(b)}" for b in a.avoid_blocks] or ["Avoid: nothing in the requested time"],
        "hints": [f"Hints: {' '.join(a.hints)}"] if a.hints else [],
    }
    for name in SECTION_ORDER[kind]:
        lines += sections[name]
    notes = [f"Data note: {n}" for n in a.data_quality]
    while notes and len("\n".join(lines + notes)) > max_chars:
        notes.pop()
    text = "\n".join(lines + notes)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


def _block_time(b) -> str:
    """'Fri 06:30-08:30'; a block ending on a later day shows that day too."""
    if b.end.date() == b.start.date():
        return f"{b.start:%a %H:%M}-{b.end:%H:%M}"
    return f"{b.start:%a %H:%M}-{b.end:%a %H:%M}"


def air_unavailable(location: Location, reason: str) -> str:
    """Tells the answer model air quality was asked for but couldn't be fetched."""
    return f"{place_name(location)} | AIR QUALITY UNAVAILABLE: {reason}"


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
    return (f"Now ({p.time:%H:%M}): {_t(p.temp_c)}, feels {_t(p.feels_like_c)}, humidity {_int(p.humidity)}%, "
            f"wind {_kmh(p.wind_kmh)}, {p.description or p.condition or 'n/a'}")


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
        f"total rain {stats.rain_total_mm:.1f} mm; max wind {_kmh(stats.wind_max)}"
    )
    return lines


def _hour(p: WeatherPoint) -> str:
    rain = _pct(p.rain_prob) if p.rain_prob is not None else "n/a"
    return (f"- {p.time:%a %H:%M}: {_t(p.temp_c)}, feels {_t(p.feels_like_c)}, rain {rain}, "
            f"wind {_kmh(p.wind_kmh)}, {p.description or p.condition or 'n/a'}")


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
        parts.append(f"wind {_kmh(d.wind_kmh)}")
    if d.wind_max_kmh is not None:
        parts.append(f"{'gusts' if d.wind_kmh is not None else 'peak wind'} {_kmh(d.wind_max_kmh)}")
    if d.uvi is not None:
        parts.append(f"UV {_int(d.uvi)}")
    text = ", ".join(parts)
    if d.summary or d.description:
        text += f". {d.summary or d.description}"
    return text


def _render_air(result: AirQualityResult, step: int | None) -> str:
    points = result.points
    naqi = any(p.naqi is not None for p in points) and result.location.country_code == "in"
    scale = "India NAQI, CPCB" if naqi else "OpenWeather 1-5 scale, not NAQI"
    kind = {"current": "AIR QUALITY NOW (observed)", "hourly": "AIR QUALITY FORECAST, hourly",
            "historical": "PAST AIR QUALITY (observed, not a forecast)"}[result.granularity]
    lines = [f"{place_name(result.location)} | {kind} ({scale}) | {window_text(result.window)}"]
    if not points:
        return "\n".join(lines + ["No air quality readings in this window."])

    level = _naqi_level if naqi else _owm_level
    if step is not None and len(points) > 1:
        note = {1: "", 2: " (every 2nd hour)", 3: " (every 3rd hour)"}[step]
        header = "NAQI, category, main pollutant" if naqi else "level (1 best - 5 worst)"
        lines.append(f"Hours{note}: time, {header}")
        lines += [f"- {p.time:%a %H:%M}: {level(p)}" for p in points[::step]]
    rated = [p for p in points if (p.naqi if naqi else p.owm_aqi) is not None]
    if rated:
        key = (lambda p: p.naqi) if naqi else (lambda p: p.owm_aqi)
        worst, best = max(rated, key=key), min(rated, key=key)
        span = f"worst {level(worst)} at {worst.time:%a %H:%M}"
        if len(rated) > 1 and key(best) != key(worst):
            span += f"; best {level(best)}"
        lines.append(f"Summary of {len(points)} hours: {span}" if len(points) > 1 else f"Now: {level(worst)}")
    elif points[0].naqi_reason:
        lines.append(f"NAQI unavailable: {points[0].naqi_reason}")

    pm = [f"{name} {'averages ' if len(points) > 1 else ''}{_int(value)} µg/m³"
          for name, value in (("PM2.5", result.summary.pm2_5_avg), ("PM10", result.summary.pm10_avg))
          if value is not None]
    if pm:
        lines.append(f"PM readings (hourly concentrations, not the index): {', '.join(pm)}")
    if naqi and result.summary.indicative:
        lines.append("Note: NAQI is indicative here (fewer hours of data than CPCB requires).")
    return "\n".join(lines)


def _naqi_level(p: AirQualityPoint) -> str:
    if p.naqi is None:
        return "NAQI n/a"
    return f"NAQI {p.naqi}, {p.naqi_category}" + (f", {p.dominant_pollutant}" if p.dominant_pollutant else "")


def _owm_level(p: AirQualityPoint) -> str:
    return f"{p.owm_aqi}/5 {OWM_AQI_NAMES[p.owm_aqi]}" if p.owm_aqi is not None else "n/a"


