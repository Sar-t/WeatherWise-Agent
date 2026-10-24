"""The dashboard: current conditions, next 24 hours, next days, air quality, alerts and an outdoor
outlook for one place. No LLM: services, caches and the scoring engine only."""

import logging
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.api import schemas
from backend.services import air_quality, geocoding, weather
from backend.services.errors import WeatherWiseError
from backend.services.models import Location, TimeWindow
from backend.services.scoring import assess_activity
from backend.services.scoring.blocks import block_reasons

log = logging.getLogger(__name__)

HOURS_AHEAD = 24
MIN_HOURS_LEFT_TODAY = 2  # fewer left before midnight: show tomorrow's outlook instead
TOMORROW_WINDOW = (time(6), time(22))


class AmbiguousPlace(Exception):
    def __init__(self, candidates: list[Location]) -> None:
        super().__init__("ambiguous place")
        self.candidates = candidates


async def find_location(lat: float | None, lon: float | None, place: str | None) -> Location:
    """Reverse-geocode rounded coordinates, or geocode a place name (AmbiguousPlace if unclear)."""
    if place:
        candidates = await geocoding.geocode(place)
        best, ambiguous = geocoding.pick_best(candidates)
        if ambiguous:
            raise AmbiguousPlace(geocoding.distinct_places(best, candidates))
        return best
    return await geocoding.reverse_geocode(round(lat, 2), round(lon, 2))  # never more than ~1 km precision


async def build_dashboard(location: Location, now: datetime | None = None) -> schemas.DashboardOut:
    tz = ZoneInfo(location.timezone)
    now = (now or datetime.now(tz)).astimezone(tz)
    current = await weather.get_current(location)  # one onecall request, cached and shared below
    next_hours = await weather.get_weather(location, TimeWindow(start=now, end=now + timedelta(hours=HOURS_AHEAD)),
                                           now=now)
    today = datetime.combine(now.date(), time(), tzinfo=tz)
    days = await weather.get_weather(location, TimeWindow(start=today, end=today + timedelta(days=weather.DAILY_DAYS)),
                                     now=now)
    sun = {s.date: s for s in current.sun + next_hours.sun}.get(now.date())

    return schemas.DashboardOut(
        location=schemas.location_out(location),
        local_time=now,
        current=schemas.current_out(current.hourly[0], sun.sunrise if sun else None, sun.sunset if sun else None),
        hourly=[schemas.hour_out(p) for p in next_hours.hourly[:HOURS_AHEAD]],
        daily=[schemas.day_out(d) for d in days.daily],
        air_quality=await _air(location, now),
        alerts=[schemas.alert_out(a) for a in {(a.event, a.start): a for a in next_hours.alerts}.values()],
        outdoor_today=await outdoor_outlook(location, now),
    )


async def _air(location: Location, now: datetime) -> schemas.DashboardAir | None:
    try:
        latest = await air_quality.get_current_air_quality(location)
        ahead = await air_quality.get_air_quality(location, TimeWindow(start=now, end=now + timedelta(hours=HOURS_AHEAD)),
                                                  now=now)
    except WeatherWiseError as exc:
        log.warning("dashboard air quality unavailable", extra={"error": type(exc).__name__})
        return None
    [point] = latest.points
    scale = "naqi" if location.country_code == "in" and point.naqi is not None else "owm"
    now_hour = schemas.air_hour(point, scale)
    return schemas.DashboardAir(
        scale=scale, value=now_hour.value, category=now_hour.category,
        dominant_pollutant=point.dominant_pollutant if scale == "naqi" else None,
        pm2_5=schemas.whole(point.components.pm2_5) if point.components.pm2_5 is not None else None,
        pm10=schemas.whole(point.components.pm10) if point.components.pm10 is not None else None,
        hourly=[schemas.air_hour(p, scale) for p in ahead.points])


async def outdoor_outlook(location: Location, now: datetime) -> schemas.OutdoorOut | None:
    """General outdoor outlook for the rest of today; tomorrow 06:00-22:00 if fewer than 2 hours are
    left or no block fits, so the card is never empty."""
    tz = ZoneInfo(location.timezone)
    midnight = datetime.combine(now.date() + timedelta(days=1), time(), tzinfo=tz)
    if midnight - now >= timedelta(hours=MIN_HOURS_LEFT_TODAY):
        today = await _assess(location, TimeWindow(start=now, end=midnight), now)
        if today is not None and today.best_blocks:
            return _outdoor(today, "today")
    tomorrow = now.date() + timedelta(days=1)
    window = TimeWindow(start=datetime.combine(tomorrow, TOMORROW_WINDOW[0], tzinfo=tz),
                        end=datetime.combine(tomorrow, TOMORROW_WINDOW[1], tzinfo=tz))
    assessment = await _assess(location, window, now)
    return _outdoor(assessment, "tomorrow") if assessment is not None else None


async def _assess(location: Location, window: TimeWindow, now: datetime):
    try:
        weather_result = await weather.get_weather(location, window, now=now)
    except WeatherWiseError:
        return None
    try:
        air = [await air_quality.get_air_quality(location, window, now=now)]
    except WeatherWiseError:
        air = []  # asked for, unavailable: the engine notes it
    return assess_activity("general_outdoor", [weather_result], air, now=now)


def _outdoor(a, day: str) -> schemas.OutdoorOut:
    best = a.best_blocks[0] if a.best_blocks else None
    reasons = best.reasons if best else block_reasons(a.hourly, 1)
    return schemas.OutdoorOut(day=day, verdict=a.verdict, score=a.score,
                              best_block=schemas.block_out(best) if best else None,
                              reason="; ".join(reasons[:2]) if reasons else None)
