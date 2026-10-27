"""Turn weather (and optionally air quality) for a window into an activity assessment.

Pure: no I/O beyond reading activities.yaml. Every score can be traced to the factor values
and the profile ranges recorded in the result.
"""

from dataclasses import dataclass, field
from typing import Literal
from datetime import date, datetime, time, timedelta, timezone
from statistics import fmean
from zoneinfo import ZoneInfo

from backend.services.date_resolver import TIME_OF_DAY
from backend.services.display import whole
from backend.services.errors import InvalidInputError
from backend.services.models import (
    AirQualityPoint,
    AirQualityResult,
    DailyWeather,
    SunTimes,
    WeatherAlert,
    WeatherResult,
)
from backend.services.scoring import blocks, factors, hints
from backend.services.scoring.config import FACTORS, FALLBACK_PROFILE, Profile, ScoringConfig, load_config
from backend.services.scoring.models import ActivityAssessment, FactorScore, HourAssessment

HOUR = timedelta(hours=1)
SEVERE_NAQI = 401
HEAVY_RAIN_MM_H = 7.6  # AMS "heavy" rain rate
HEAVY_RAIN_MM_DAY = 64.5  # IMD "heavy rain" in 24 h
DAYLIGHT_FALLBACK = (time(6), time(18))  # for dates without sunrise/sunset data
PARTS_OF_DAY = ("morning", "afternoon", "evening", "night")
DAILY_THUNDERSTORM_CAP = 45  # a day-level thunderstorm caps each part at marginal instead of zeroing it
DAILY_THUNDERSTORM_REASON = "thunderstorms possible during the day"
DAILY_THUNDERSTORM_NOTE = "Thunderstorms possible during the day; check the hourly forecast closer to the date."

# OpenWeather's 1-5 air quality scale, mapped to the top of the matching NAQI band for scoring.
OWM_AQI_NAMES = {1: "Good", 2: "Fair", 3: "Moderate", 4: "Poor", 5: "Very Poor"}
OWM_AQI_AS_NAQI = {1: 50, 2: 100, 3: 200, 4: 300, 5: 400}

FACTOR_LABELS = {
    "feels_like_c": "Feels-like temperature", "rain_prob": "Rain chance", "rain_mm": "Rain amount",
    "wind_kmh": "Wind", "wind_gust_kmh": "Wind gusts", "uvi": "UV index", "visibility_m": "Visibility",
    "naqi": "Air quality",
}
DAILY_NOTE = (
    "Daily estimate (beyond the 48-hour hourly forecast): each part of the day is scored from daily "
    "values, so confidence is lower. Air temperature stands in for feels-like outside the afternoon, "
    "UV counts for the afternoon only, and hourly rain amount and visibility aren't available."
)
DAYLIGHT_NOTE = ("Sunrise and sunset unavailable for some dates; daylight assumed to be 06:00-18:00 "
                 "local time there.")


@dataclass
class _Air:
    """Air quality for one slot, ready to score."""

    value: float  # NAQI, or OpenWeather's scale mapped onto NAQI bands
    text: str  # "NAQI 230, Poor" / "air quality Poor (OpenWeather 4/5)"
    source: str  # "naqi" | "owm"
    indicative: bool = False


@dataclass
class _Slot:
    """Raw inputs for one hour (or part of a day) before scoring."""

    start: datetime
    end: datetime
    values: dict[str, float | None]
    air: _Air | None = None
    stops: list[tuple[str, str]] = field(default_factory=list)  # (hard-stop kind, reason)
    info: list[str] = field(default_factory=list)  # non-scoring reasons, e.g. a minor alert
    cap: tuple[int, str] | None = None  # (max score, reason), e.g. a day-level thunderstorm
    part: str | None = None
    temp_label: str = "feels like"


def assess_activity(
    activity: str,
    weather_results: list[WeatherResult],
    air_quality_results: list[AirQualityResult] | None = None,
    now: datetime | None = None,
    duration_hours: int | None = None,
    hints_from: Literal["best_block", "window"] = "best_block",
) -> ActivityAssessment:
    """Score an activity over the weather results' windows (one result per window).

    `duration_hours` overrides the profile's block length (e.g. a known trip length for travel).
    `hints_from="window"` bases hints on every upcoming hour (what to wear/pack for the whole time)
    instead of only the best block (what to take at the best time).
    """
    if not weather_results:
        raise InvalidInputError("assess_activity needs at least one weather result")
    config = load_config()
    location = weather_results[0].location
    tz = ZoneInfo(location.timezone)
    now = _local_now(now, tz)
    notes: list[str] = []

    name = _profile_name(activity, config)
    if name == FALLBACK_PROFILE and _key(activity) != FALLBACK_PROFILE:
        notes.append(f"No profile for '{activity}'; used the general outdoor profile.")
    profile = config.profiles[name]

    air_points = [p for r in air_quality_results or [] for p in r.points]
    air_by_hour = {_hour_key(p.time): p for p in air_points}
    keywords = [k.lower() for k in config.severe_alert_keywords]

    groups: list[tuple[list[HourAssessment], bool]] = []  # (scored slots, is daily)
    hourly_slots: list[_Slot] = []
    all_slots: list[_Slot] = []
    for result in weather_results:
        daily = result.granularity == "daily" or (not result.hourly and bool(result.daily))
        if daily:
            slots = _part_slots(result, air_points, keywords, tz)
        else:
            slots = _hour_slots(result, air_by_hour, keywords)
            hourly_slots += slots
        all_slots += slots
        groups.append(([_assess(s, profile) for s in slots], daily))

    is_daily = any(daily for _, daily in groups)
    if is_daily:
        notes.append(DAILY_NOTE)
    notes += _missing_notes(hourly_slots, profile)
    notes += _air_notes(all_slots, air_quality_results is not None)
    if any(s.cap for s in all_slots):
        notes.append(DAILY_THUNDERSTORM_NOTE)

    upcoming = [([h for h in slots if h.time >= now], daily) for slots, daily in groups]
    usable = upcoming
    if profile.needs_daylight:
        sun = {s.date: s for r in weather_results for s in r.sun}
        if any(_daylight_bounds(h, tz, sun)[2] for slots, _ in upcoming for h in slots):
            notes.append(DAYLIGHT_NOTE)
        usable = [([h for h in slots if _in_daylight(h, tz, sun)], daily) for slots, daily in upcoming]

    duration = duration_hours or profile.duration_hours
    best, shortened = blocks.best_blocks([(slots, 1 if daily else duration) for slots, daily in usable])
    if shortened:
        notes.append(f"No stretch of {duration} h fits the window; the best times are shorter than a "
                     f"usual {name.replace('_', ' ')} session.")
    avoid = blocks.avoid_blocks([slots for slots, _ in upcoming])

    hours = [h for slots, _ in groups for h in slots]
    upcoming_hours = [h for slots, _ in upcoming for h in slots]
    if best:
        score = best[0].average_score
        hint_slots = [h for h in upcoming_hours if best[0].start <= h.time < best[0].end]
    elif upcoming_hours:  # only possible when daylight removed every upcoming hour
        score, hint_slots = 0, upcoming_hours
        notes.append(f"No daylight hours in the requested time; {name.replace('_', ' ')} needs daylight.")
    else:
        score = round(fmean(h.score for h in hours)) if hours else 0
        hint_slots = hours
        notes.append("The requested time has already passed, so no best time is suggested.")
    if hints_from == "window" and upcoming_hours:
        hint_slots = upcoming_hours

    return ActivityAssessment(
        activity=activity,
        profile=name,
        location=location,
        windows=[r.window for r in weather_results],
        verdict=factors.verdict(score),
        score=score,
        best_blocks=best,
        avoid_blocks=avoid,
        hourly=hours,
        hints=hints.hints(config.hints, hint_slots),
        data_quality=list(dict.fromkeys(notes)),
        is_daily_estimate=is_daily,
        confidence="low" if is_daily else "normal",
    )


# --- profile ---------------------------------------------------------------


def _key(activity: str) -> str:
    return activity.strip().lower().replace(" ", "_").replace("-", "_")


def _profile_name(activity: str, config: ScoringConfig) -> str:
    key = _key(activity)
    return key if key in config.profiles else FALLBACK_PROFILE


# --- scoring ---------------------------------------------------------------


def _assess(slot: _Slot, profile: Profile) -> HourAssessment:
    scored: list[FactorScore] = []
    for name in FACTORS:
        value = slot.values.get(name)
        if value is None:
            continue
        spec = profile.factors[name]
        s = factors.factor_score(spec, value)
        label = slot.temp_label if name == "feels_like_c" else slot.air.text if name == "naqi" and slot.air else None
        scored.append(FactorScore(name=name, value=round(value, 2), score=round(s, 1),
                                  reason=None if s >= factors.REASON_BELOW else factors.reason(name, value, spec, label)))

    stops = [text for kind, text in slot.stops if kind in profile.hard_stops]
    if ("severe_naqi" in profile.hard_stops and slot.air and slot.air.source == "naqi"
            and slot.air.value >= SEVERE_NAQI):
        stops.append(slot.air.text)
    stops = list(dict.fromkeys(stops))

    score = 0 if stops or not scored else round(
        factors.combine({f.name: f.score for f in scored}, profile.weights))
    capped = []
    if slot.cap:
        limit, why = slot.cap
        score, capped = min(score, limit), [why]
    lost = [f.reason for f in sorted(scored, key=lambda f: f.score) if f.reason]
    return HourAssessment(
        time=slot.start, end=slot.end, part_of_day=slot.part, score=score, verdict=factors.verdict(score),
        factors=scored, hard_stops=stops, capped_by=capped[0] if capped else None, reasons=list(dict.fromkeys(stops + capped + lost + slot.info)),
    )


# --- slots from weather ----------------------------------------------------


def _hour_slots(result: WeatherResult, air_by_hour: dict[datetime, AirQualityPoint],
                keywords: list[str]) -> list[_Slot]:
    slots = []
    for p in sorted(result.hourly, key=lambda p: p.time):
        start, end = p.time, p.time + HOUR
        aq = air_by_hour.get(_hour_key(p.time))
        air = _air([aq] if aq else [])
        slot = _Slot(start=start, end=end, air=air, values={
            "feels_like_c": p.feels_like_c, "rain_prob": p.rain_prob, "rain_mm": p.rain_mm,
            "wind_kmh": p.wind_kmh, "wind_gust_kmh": p.wind_gust_kmh, "uvi": p.uvi,
            "visibility_m": p.visibility_m, "naqi": air.value if air else None,
        })
        slot.stops += _condition_stops(p.condition, p.description)
        if p.rain_mm is not None and p.rain_mm >= HEAVY_RAIN_MM_H:
            slot.stops.append(("heavy_rain", f"heavy rain ({p.rain_mm:.1f} mm/h)"))
        _add_alerts(slot, result.alerts, keywords)
        slots.append(slot)
    return slots


def _part_slots(result: WeatherResult, air_points: list[AirQualityPoint], keywords: list[str],
                tz: ZoneInfo) -> list[_Slot]:
    """One slot per part of day that overlaps the window, from that day's daily values."""
    slots = []
    for day in sorted(result.daily, key=lambda d: d.date):
        for part in PARTS_OF_DAY:
            start, end = _part_span(day.date, part, tz)
            if not (start < result.window.end and end > result.window.start):
                continue
            temp, label = _part_temperature(day, part)
            air = _air([p for p in air_points if start <= p.time < end])
            slot = _Slot(start=start, end=end, part=part, air=air, temp_label=label, values={
                "feels_like_c": temp, "rain_prob": day.rain_prob, "wind_kmh": day.wind_kmh,
                "wind_gust_kmh": day.wind_max_kmh, "uvi": day.uvi if part == "afternoon" else None,
                "naqi": air.value if air else None,
            })
            for kind, text in _condition_stops(day.condition, day.description):
                if kind == "thunderstorm":  # day-level: possible, not certain for this part of the day
                    slot.cap = (DAILY_THUNDERSTORM_CAP, DAILY_THUNDERSTORM_REASON)
                else:
                    slot.stops.append((kind, text))
            if day.rain_mm is not None and day.rain_mm >= HEAVY_RAIN_MM_DAY:
                slot.stops.append(("heavy_rain", f"heavy rain ({whole(day.rain_mm)} mm in the day)"))
            _add_alerts(slot, result.alerts, keywords)
            slots.append(slot)
    return slots


def _part_span(day: date, part: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    start_h, end_h = TIME_OF_DAY[part]
    start = datetime.combine(day, time(start_h), tzinfo=tz)
    end_day = day + timedelta(days=1) if end_h <= start_h else day
    return start, datetime.combine(end_day, time(end_h % 24), tzinfo=tz)


def _part_temperature(day: DailyWeather, part: str) -> tuple[float | None, str]:
    if part == "afternoon":
        if day.feels_like_day_c is not None:
            return day.feels_like_day_c, "feels like"
        return day.temp_day_c, "temperature"
    temp = {"morning": day.temp_morning_c, "evening": day.temp_evening_c, "night": day.temp_night_c}[part]
    return temp, "temperature"


def _condition_stops(condition: str | None, description: str | None) -> list[tuple[str, str]]:
    desc = (description or "").lower()
    stops = []
    if condition == "Thunderstorm" or "thunder" in desc:
        stops.append(("thunderstorm", "thunderstorm"))
    if ("heavy" in desc or "extreme" in desc) and ("rain" in desc or "shower" in desc):
        stops.append(("heavy_rain", desc))
    return stops


def _add_alerts(slot: _Slot, alerts: list[WeatherAlert], keywords: list[str]) -> None:
    for alert in alerts:
        if alert.start < slot.end and alert.end > slot.start:
            text = f"weather alert: {alert.event}"
            if any(k in alert.event.lower() for k in keywords):
                slot.stops.append(("severe_alert", text))
            else:
                slot.info.append(text)


def _air(points: list[AirQualityPoint]) -> _Air | None:
    """The worst air quality among `points`: NAQI if any has it, else OpenWeather's 1-5 scale."""
    rated = [p for p in points if p.naqi is not None]
    if rated:
        worst = max(rated, key=lambda p: p.naqi)
        return _Air(worst.naqi, f"NAQI {worst.naqi}, {worst.naqi_category}", "naqi", worst.indicative)
    owm = [p.owm_aqi for p in points if p.owm_aqi is not None]
    if owm:
        level = max(owm)
        return _Air(OWM_AQI_AS_NAQI[level], f"air quality {OWM_AQI_NAMES[level]} (OpenWeather {level}/5)", "owm")
    return None


# --- notes -----------------------------------------------------------------


def _missing_notes(slots: list[_Slot], profile: Profile) -> list[str]:
    """One note per weather factor missing from some or all hours (air quality: see _air_notes;
    daily estimates are covered by DAILY_NOTE)."""
    notes = []
    for name in FACTORS:
        if name == "naqi" or name not in profile.factors or not slots:
            continue
        missing = sum(1 for s in slots if s.values.get(name) is None)
        if missing == len(slots):
            notes.append(f"{FACTOR_LABELS[name]} unavailable; not scored.")
        elif missing:
            notes.append(f"{FACTOR_LABELS[name]} missing for {missing} of {len(slots)} hours; "
                         "those hours are scored without it.")
    return notes


AIR_NOT_REQUESTED_NOTE = "Air quality not provided; scored on weather alone."


def _air_notes(slots: list[_Slot], air_given: bool) -> list[str]:
    notes = []
    missing = sum(1 for s in slots if s.air is None)
    unit = "parts of the day" if all(s.part for s in slots) else "hours"
    if not air_given:
        notes.append(AIR_NOT_REQUESTED_NOTE)
    elif slots and missing == len(slots):
        notes.append("Air quality unavailable; not scored.")
    elif missing:
        notes.append(f"Air quality missing for {missing} of {len(slots)} {unit}; those are scored without it.")
    if any(s.air and s.air.source == "owm" for s in slots):
        notes.append("NAQI unavailable for some hours; OpenWeather's 1-5 air quality scale was mapped "
                     "onto NAQI bands for scoring.")
    if any(s.air and s.air.indicative for s in slots):
        notes.append("NAQI is indicative for some hours (fewer hours of data than CPCB requires).")
    return notes


# --- time ------------------------------------------------------------------


def _local_now(now: datetime | None, tz: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidInputError("`now` must be timezone-aware")
    return now.astimezone(tz)


def _hour_key(t: datetime) -> datetime:
    """The UTC hour a time falls in, for aligning weather and air quality readings."""
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _daylight_bounds(slot: HourAssessment, tz: ZoneInfo,
                     sun: dict[date, SunTimes]) -> tuple[datetime, datetime, bool]:
    """(sunrise, sunset, used_fallback) for the local date the slot starts on."""
    day = slot.time.astimezone(tz).date()
    known = sun.get(day)
    if known and known.sunrise and known.sunset:
        return known.sunrise, known.sunset, False
    sunrise, sunset = (datetime.combine(day, t, tzinfo=tz) for t in DAYLIGHT_FALLBACK)
    return sunrise, sunset, True


def _in_daylight(slot: HourAssessment, tz: ZoneInfo, sun: dict[date, SunTimes]) -> bool:
    """Whether the slot's midpoint is between sunrise and sunset."""
    sunrise, sunset, _ = _daylight_bounds(slot, tz, sun)
    mid = slot.time + (slot.end - slot.time) / 2
    return sunrise <= mid <= sunset
