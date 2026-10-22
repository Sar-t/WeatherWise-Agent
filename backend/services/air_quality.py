"""Air quality (current, ~4-day hourly forecast, history) via OpenWeather Air Pollution API,
with India's NAQI computed from trailing averages. Times are in the location's timezone.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from backend.core.config import get_settings
from backend.services import naqi
from backend.services.cache import Cache, InMemoryCache
from backend.services.errors import InvalidInputError, OutOfRangeError
from backend.services.http import get_json
from backend.services.models import (
    AirComponents,
    AirQualityPoint,
    AirQualityResult,
    AirQualitySummary,
    Location,
    TimeWindow,
)

log = logging.getLogger(__name__)

PROVIDER = "OpenWeather"
CURRENT_URL = "https://api.openweathermap.org/data/2.5/air_pollution"
FORECAST_URL = f"{CURRENT_URL}/forecast"
HISTORY_URL = f"{CURRENT_URL}/history"

FORECAST_TTL_S = 30 * 60
HISTORY_TTL_S = 24 * 60 * 60
HISTORY_START = datetime(2020, 11, 27, tzinfo=timezone.utc)  # earliest history OpenWeather has
AVERAGING_LEAD = timedelta(hours=24)  # history fetched before a window, for the 24 h averages
HOUR = timedelta(hours=1)

cache: Cache = InMemoryCache()

Series = list[tuple[datetime, dict[str, float | None]]]


@dataclass
class _Calls:
    """Counts upstream requests (cache misses) made while serving one call."""

    count: int = 0


async def get_current_air_quality(location: Location) -> AirQualityResult:
    """The latest reading, with NAQI from the preceding 24 hours of history."""
    tz, calls = ZoneInfo(location.timezone), _Calls()
    current, fetched_at = await _fetch(CURRENT_URL, location, {}, f"aq_current|{_coord_key(location)}",
                                       FORECAST_TTL_S, calls)
    entries = current.get("list") or []
    if not entries:
        raise OutOfRangeError("No current air quality reading is available for this location.")
    now_utc = datetime.fromtimestamp(entries[0]["dt"], timezone.utc)
    history, _ = await _history(location, _floor_hour(now_utc) - AVERAGING_LEAD, _floor_hour(now_utc), calls)
    series = _series(history + entries)
    point = _point(entries[0], series, tz)
    log.debug("air quality upstream calls", extra={"calls": calls.count, "location": location.name})
    return _result(location, TimeWindow(start=point.time, end=point.time + HOUR), "current", [point], fetched_at)


async def get_air_quality(location: Location, window: TimeWindow, now: datetime | None = None) -> AirQualityResult:
    """Hourly air quality for one window: history if it's past, else the ~4-day forecast."""
    [result] = await get_air_quality_multi(location, [window], now=now)
    return result


async def get_air_quality_multi(
    location: Location, windows: list[TimeWindow], now: datetime | None = None
) -> list[AirQualityResult]:
    """Several windows, sharing one forecast response and one recent-history response."""
    tz = ZoneInfo(location.timezone)
    now = _local_now(now, tz)
    plans = [_plan(w, now) for w in windows]  # history range errors before any request
    calls = _Calls()

    forecast_data = None
    results = []
    for kind, window in plans:
        if kind == "history":
            start = _floor_hour(window.start) - AVERAGING_LEAD
            entries, fetched_at = await _history(location, start, _ceil_hour(window.end), calls)
            series = _series(entries)
            points = [_point(e, series, tz) for e in _in_window(entries, window)]
            results.append(_result(location, window, "historical", points, fetched_at))
            continue

        if forecast_data is None:
            raw, fetched_at = await _fetch(FORECAST_URL, location, {}, f"aq_forecast|{_coord_key(location)}",
                                           FORECAST_TTL_S, calls)
            forecast = raw.get("list") or []
            if not forecast:
                raise OutOfRangeError("No air quality forecast is available for this location.")
            first = datetime.fromtimestamp(forecast[0]["dt"], timezone.utc)
            recent, _ = await _history(location, first - AVERAGING_LEAD, first, calls)  # averages only
            forecast_data = (forecast, _series(recent + forecast), fetched_at)
        forecast, series, fetched_at = forecast_data
        horizon = datetime.fromtimestamp(forecast[-1]["dt"], timezone.utc) + HOUR
        if window.end > horizon:
            raise OutOfRangeError("Air quality forecasts are available up to 4 days ahead.")
        points = [_point(e, series, tz) for e in _in_window(forecast, window)]
        results.append(_result(location, window, "hourly", points, fetched_at))

    log.debug("air quality upstream calls", extra={"calls": calls.count, "windows": len(windows),
                                                   "location": location.name})
    return results


# --- routing ---------------------------------------------------------------


def _local_now(now: datetime | None, tz: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidInputError("`now` must be timezone-aware")
    return now.astimezone(tz)


def _plan(window: TimeWindow, now: datetime) -> tuple[Literal["history", "forecast"], TimeWindow]:
    if window.end <= now:
        if window.start < HISTORY_START:
            raise OutOfRangeError("Air quality history is only available from 27 November 2020.")
        return "history", window
    if window.start < now:
        window = TimeWindow(start=now, end=window.end)
    return "forecast", window


def _in_window(entries: list[dict[str, Any]], window: TimeWindow) -> list[dict[str, Any]]:
    """Hours starting inside the window; for a very short window, the hour covering its start."""
    def start(e: dict[str, Any]) -> datetime:
        return datetime.fromtimestamp(e["dt"], timezone.utc)

    inside = [e for e in entries if window.start <= start(e) < window.end]
    return inside or [e for e in entries if start(e) <= window.start < start(e) + HOUR]


# --- requests --------------------------------------------------------------


async def _history(
    location: Location, start: datetime, end: datetime, calls: _Calls
) -> tuple[list[dict[str, Any]], datetime]:
    """Hourly history entries between whole-hour `start` and `end`, and when they were fetched."""
    start = max(start, HISTORY_START)
    if end <= start:
        return [], datetime.now(timezone.utc)
    params = {"start": int(start.timestamp()), "end": int(end.timestamp())}
    key = f"aq_history|{_coord_key(location)}|{params['start']}|{params['end']}"
    raw, fetched_at = await _fetch(HISTORY_URL, location, params, key, HISTORY_TTL_S, calls)
    return raw.get("list") or [], fetched_at


async def _fetch(
    url: str, location: Location, params: dict[str, Any], key: str, ttl_s: float, calls: _Calls
) -> tuple[dict[str, Any], datetime]:
    """Return (raw JSON, when it was fetched upstream), serving from cache when possible."""
    if (hit := cache.get(key)) is not None:
        return hit["data"], datetime.fromisoformat(hit["fetched_at"])
    calls.count += 1
    query = params | {
        "lat": round(location.lat, 2),
        "lon": round(location.lon, 2),
        "appid": get_settings().openweather_api_key.get_secret_value(),
    }
    data = await get_json(PROVIDER, url, query)
    fetched_at = datetime.now(timezone.utc)
    cache.set(key, {"data": data, "fetched_at": fetched_at.isoformat()}, ttl_s=ttl_s)
    return data, fetched_at


def _coord_key(location: Location) -> str:
    return f"{location.lat:.2f}|{location.lon:.2f}"


def _floor_hour(t: datetime) -> datetime:
    return t.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def _ceil_hour(t: datetime) -> datetime:
    floored = _floor_hour(t)
    return floored if floored == t.astimezone(timezone.utc) else floored + HOUR


# --- parsing ---------------------------------------------------------------


def _series(entries: list[dict[str, Any]]) -> Series:
    """Hourly (UTC time, components) for averaging; later entries win for the same hour."""
    by_time = {datetime.fromtimestamp(e["dt"], timezone.utc): e.get("components") or {} for e in entries}
    return sorted(by_time.items())


def _point(entry: dict[str, Any], series: Series, tz: ZoneInfo) -> AirQualityPoint:
    at = datetime.fromtimestamp(entry["dt"], timezone.utc)
    result = naqi.compute_naqi(naqi.rolling_averages(series, at))
    return AirQualityPoint(
        time=at.astimezone(tz),
        components=AirComponents(**{k: v for k, v in (entry.get("components") or {}).items()
                                    if k in AirComponents.model_fields}),
        owm_aqi=(entry.get("main") or {}).get("aqi"),
        naqi=result.value,
        naqi_category=result.category,
        dominant_pollutant=result.dominant,
        sub_indices=result.sub_indices,
        indicative=result.indicative,
        naqi_reason=result.reason,
    )


def _result(location: Location, window: TimeWindow, granularity: str, points: list[AirQualityPoint],
            fetched_at: datetime) -> AirQualityResult:
    tz = ZoneInfo(location.timezone)
    return AirQualityResult(location=location, window=window, granularity=granularity, points=points,
                            summary=summarize(points), fetched_at=fetched_at.astimezone(tz))


def summarize(points: list[AirQualityPoint]) -> AirQualitySummary:
    """Worst NAQI hour, its category and dominant pollutant, and mean PM over the points."""
    rated = [p for p in points if p.naqi is not None]
    worst = max(rated, key=lambda p: p.naqi) if rated else None
    owm = [p.owm_aqi for p in points if p.owm_aqi is not None]

    def mean(values: list[float | None]) -> float | None:
        values = [v for v in values if v is not None]
        return round(sum(values) / len(values), 1) if values else None

    return AirQualitySummary(
        naqi_max=worst.naqi if worst else None,
        naqi_max_at=worst.time if worst else None,
        naqi_category=worst.naqi_category if worst else None,
        dominant_pollutant=worst.dominant_pollutant if worst else None,
        owm_aqi_max=max(owm) if owm else None,
        pm2_5_avg=mean([p.components.pm2_5 for p in points]),
        pm10_avg=mean([p.components.pm10 for p in points]),
        indicative=any(p.indicative for p in points),
    )
