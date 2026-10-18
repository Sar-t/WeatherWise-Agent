"""Current, forecast and historical weather via OpenWeather One Call API 3.0.

All timestamps are returned in the location's own timezone, wind in km/h.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from backend.core.config import get_settings
from backend.services.cache import Cache, InMemoryCache
from backend.services.errors import InvalidInputError, OutOfRangeError, UpstreamAPIError
from backend.services.http import get_json
from backend.services.models import DailyWeather, Location, TimeWindow, WeatherAlert, WeatherPoint, WeatherResult

log = logging.getLogger(__name__)

PROVIDER = "OpenWeather"
ONECALL_URL = "https://api.openweathermap.org/data/3.0/onecall"
TIMEMACHINE_URL = f"{ONECALL_URL}/timemachine"
DAY_SUMMARY_URL = f"{ONECALL_URL}/day_summary"

FORECAST_TTL_S = 10 * 60  # OpenWeather refreshes its model every 10 minutes
HISTORY_TTL_S = 24 * 60 * 60  # past weather doesn't change
HOURLY_HORIZON = timedelta(hours=48)
DAILY_DAYS = 8  # today + 7
SHORT_HISTORY = timedelta(hours=6)  # longer past windows use day summaries
HOUR = timedelta(hours=1)
MS_TO_KMH = 3.6

Route = Literal["timemachine", "day_summary", "hourly", "daily"]

cache: Cache = InMemoryCache()


@dataclass
class _Calls:
    """Counts upstream requests (cache misses) made while serving one call."""

    count: int = 0


async def get_current(location: Location) -> WeatherResult:
    """Current conditions as one point; the window is the hour after the observation."""
    tz = ZoneInfo(location.timezone)
    calls = _Calls()
    raw, fetched_at = await _onecall(location, calls)
    point = _point(raw["current"], tz)
    window = TimeWindow(start=point.time, end=point.time + HOUR)
    log.debug("weather upstream calls", extra={"calls": calls.count, "location": location.name})
    return WeatherResult(
        location=location,
        window=window,
        granularity="current",
        hourly=[point],
        alerts=_alerts(raw, window, tz),
        fetched_at=fetched_at.astimezone(tz),
    )


async def get_weather(location: Location, window: TimeWindow, now: datetime | None = None) -> WeatherResult:
    """Weather for one window, choosing history, hourly or daily data by where it falls.

    Raises OutOfRangeError for windows more than 8 days ahead.
    """
    [result] = await get_weather_multi(location, [window], now=now)
    return result


async def get_weather_multi(
    location: Location, windows: list[TimeWindow], now: datetime | None = None
) -> list[WeatherResult]:
    """Weather for several windows, e.g. weekend mornings, sharing one forecast request."""
    tz = ZoneInfo(location.timezone)
    now = _local_now(now, tz)
    plans = [_plan(window, now, tz) for window in windows]  # range errors before any request
    calls = _Calls()
    forecast: tuple[dict[str, Any], datetime] | None = None

    results = []
    for route, window in plans:
        if route == "timemachine":
            results.append(await _timemachine_result(location, window, tz, calls))
        elif route == "day_summary":
            results.append(await _day_summary_result(location, window, tz, calls))
        else:
            if forecast is None:
                forecast = await _onecall(location, calls)
            results.append(_forecast_result(location, window, route, *forecast, tz))

    log.debug("weather upstream calls", extra={"calls": calls.count, "windows": len(windows), "location": location.name})
    return results


# --- routing ---------------------------------------------------------------


def _local_now(now: datetime | None, tz: ZoneInfo) -> datetime:
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None or now.utcoffset() is None:
        raise InvalidInputError("`now` must be timezone-aware")
    return now.astimezone(tz)


def _plan(window: TimeWindow, now: datetime, tz: ZoneInfo) -> tuple[Route, TimeWindow]:
    """Pick the data source for `window`; a window spanning `now` is trimmed to start at `now`."""
    if window.end <= now:
        return ("timemachine" if window.end - window.start <= SHORT_HISTORY else "day_summary"), window
    if window.start < now:
        window = TimeWindow(start=now, end=window.end)
    if window.end <= now + HOURLY_HORIZON:
        return "hourly", window
    last_day = now.date() + timedelta(days=DAILY_DAYS)
    if window.end <= datetime.combine(last_day, time(), tzinfo=tz):
        return "daily", window
    raise OutOfRangeError("Forecasts are available up to 8 days ahead.")


# --- results ---------------------------------------------------------------


def _forecast_result(
    location: Location,
    window: TimeWindow,
    route: Route,
    raw: dict[str, Any],
    fetched_at: datetime,
    tz: ZoneInfo,
) -> WeatherResult:
    hourly, daily = [], []
    if route == "hourly":
        hourly = _points_in(window, [_point(h, tz) for h in raw.get("hourly") or []])
    else:
        days = set(_local_dates(window, tz))
        daily = [d for d in (_daily(d, tz) for d in raw.get("daily") or []) if d.date in days]
    return WeatherResult(
        location=location,
        window=window,
        granularity=route,
        hourly=hourly,
        daily=daily,
        alerts=_alerts(raw, window, tz),
        fetched_at=fetched_at.astimezone(tz),
    )


async def _timemachine_result(location: Location, window: TimeWindow, tz: ZoneInfo, calls: _Calls) -> WeatherResult:
    """One timemachine request per whole hour overlapping the window."""
    points, fetched = [], []
    hour = window.start.replace(minute=0, second=0, microsecond=0)
    while hour < window.end:
        ts = int(hour.timestamp())
        raw, fetched_at = await _fetch(
            TIMEMACHINE_URL, _coords(location) | {"dt": ts}, f"timemachine|{_coord_key(location)}|{ts}",
            HISTORY_TTL_S, calls,
        )
        points += [_point(p, tz) for p in raw.get("data") or []]
        fetched.append(fetched_at)
        hour = (hour.astimezone(timezone.utc) + HOUR).astimezone(tz)  # step in UTC: DST-safe
    return WeatherResult(
        location=location, window=window, granularity="historical", hourly=points, fetched_at=min(fetched).astimezone(tz)
    )


async def _day_summary_result(location: Location, window: TimeWindow, tz: ZoneInfo, calls: _Calls) -> WeatherResult:
    """One day_summary request per local date the window touches."""
    days, fetched = [], []
    for day in _local_dates(window, tz):
        params = _coords(location) | {"date": day.isoformat(), "tz": _utc_offset(day, tz)}
        raw, fetched_at = await _fetch(
            DAY_SUMMARY_URL, params, f"day_summary|{_coord_key(location)}|{day.isoformat()}", HISTORY_TTL_S, calls
        )
        days.append(_day_summary(raw, day))
        fetched.append(fetched_at)
    return WeatherResult(
        location=location, window=window, granularity="historical", daily=days, fetched_at=min(fetched).astimezone(tz)
    )


def _points_in(window: TimeWindow, points: list[WeatherPoint]) -> list[WeatherPoint]:
    """Points starting inside the window; for a very short window, the point covering its start."""
    inside = [p for p in points if window.start <= p.time < window.end]
    return inside or [p for p in points if p.time <= window.start < p.time + HOUR]


def _local_dates(window: TimeWindow, tz: ZoneInfo) -> list[date]:
    """Every local calendar date the window touches (its end is exclusive)."""
    first = window.start.astimezone(tz).date()
    last = (window.end - timedelta(microseconds=1)).astimezone(tz).date()
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def _alerts(raw: dict[str, Any], window: TimeWindow, tz: ZoneInfo) -> list[WeatherAlert]:
    """Alerts whose active period overlaps the window."""
    alerts = [
        WeatherAlert(
            sender=a.get("sender_name", ""),
            event=a.get("event", ""),
            start=_local(a["start"], tz),
            end=_local(a["end"], tz),
            description=a.get("description", ""),
        )
        for a in raw.get("alerts") or []
    ]
    return [a for a in alerts if a.start < window.end and a.end > window.start]


# --- requests --------------------------------------------------------------


async def _onecall(location: Location, calls: _Calls) -> tuple[dict[str, Any], datetime]:
    """The full current + hourly + daily + alerts response, shared by every forecast caller."""
    params = _coords(location) | {"exclude": "minutely"}
    return await _fetch(ONECALL_URL, params, f"onecall|{_coord_key(location)}", FORECAST_TTL_S, calls)


async def _fetch(
    url: str, params: dict[str, Any], key: str, ttl_s: float, calls: _Calls
) -> tuple[dict[str, Any], datetime]:
    """Return (raw JSON, when it was fetched upstream), serving from cache when possible."""
    if (hit := cache.get(key)) is not None:
        return hit["data"], datetime.fromisoformat(hit["fetched_at"])

    calls.count += 1
    query = params | {"units": "metric", "lang": "en", "appid": get_settings().openweather_api_key.get_secret_value()}
    try:
        data = await get_json(PROVIDER, url, query)
    except UpstreamAPIError as exc:
        if exc.status_code == 401:
            raise UpstreamAPIError(
                "OpenWeather rejected the API key (HTTP 401). The key may not have the "
                "'One Call API 3.0' subscription enabled; see https://openweathermap.org/api/one-call-3.",
                401,
            ) from None
        if exc.status_code == 404 and url != ONECALL_URL:
            raise OutOfRangeError("OpenWeather has no historical data for that time.") from None
        raise

    fetched_at = datetime.now(timezone.utc)
    cache.set(key, {"data": data, "fetched_at": fetched_at.isoformat()}, ttl_s=ttl_s)
    return data, fetched_at


def _coords(location: Location) -> dict[str, float]:
    """Coordinates rounded to 2 decimals (~1 km), matching the cache key."""
    return {"lat": round(location.lat, 2), "lon": round(location.lon, 2)}


def _coord_key(location: Location) -> str:
    return f"{location.lat:.2f}|{location.lon:.2f}"


def _utc_offset(day: date, tz: ZoneInfo) -> str:
    """The zone's UTC offset at local noon on `day`, as day_summary wants it: "+05:30"."""
    minutes = int(datetime.combine(day, time(12), tzinfo=tz).utcoffset().total_seconds() // 60)
    sign = "+" if minutes >= 0 else "-"
    return f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"


# --- parsing ---------------------------------------------------------------


def _local(ts: int, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(ts, tz)


def _kmh(ms: float | None) -> float | None:
    return None if ms is None else ms * MS_TO_KMH


def _condition(raw: dict[str, Any]) -> dict[str, Any]:
    return (raw.get("weather") or [{}])[0]


def _point(raw: dict[str, Any], tz: ZoneInfo) -> WeatherPoint:
    """Map a current / hourly / timemachine entry. Missing rain or snow means none fell."""
    weather = _condition(raw)
    return WeatherPoint(
        time=_local(raw["dt"], tz),
        temp_c=raw["temp"],
        feels_like_c=raw["feels_like"],
        humidity=raw["humidity"],
        rain_prob=raw.get("pop"),
        wind_kmh=_kmh(raw["wind_speed"]),
        wind_gust_kmh=_kmh(raw.get("wind_gust")),
        uvi=raw.get("uvi"),
        clouds=raw.get("clouds"),
        visibility_m=raw.get("visibility"),
        rain_mm=(raw.get("rain") or {}).get("1h", 0.0),
        snow_mm=(raw.get("snow") or {}).get("1h", 0.0),
        condition=weather.get("main"),
        description=weather.get("description"),
        icon=weather.get("icon"),
    )


def _daily(raw: dict[str, Any], tz: ZoneInfo) -> DailyWeather:
    """Map a onecall `daily` entry. Here `rain`/`snow` are plain numbers, missing when zero."""
    temp, feels, weather = raw.get("temp") or {}, raw.get("feels_like") or {}, _condition(raw)
    return DailyWeather(
        date=_local(raw["dt"], tz).date(),
        temp_min_c=temp["min"],
        temp_max_c=temp["max"],
        temp_morning_c=temp.get("morn"),
        temp_day_c=temp.get("day"),
        temp_evening_c=temp.get("eve"),
        temp_night_c=temp.get("night"),
        feels_like_day_c=feels.get("day"),
        humidity=raw.get("humidity"),
        rain_prob=raw.get("pop"),
        rain_mm=raw.get("rain", 0.0),
        snow_mm=raw.get("snow", 0.0),
        wind_kmh=_kmh(raw.get("wind_speed")),
        wind_max_kmh=_kmh(raw.get("wind_gust")),
        uvi=raw.get("uvi"),
        condition=weather.get("main"),
        description=weather.get("description"),
        summary=raw.get("summary"),
    )


def _day_summary(raw: dict[str, Any], day: date) -> DailyWeather:
    """Map a day_summary response. Its wind is the day's peak, so it fills wind_max_kmh only."""
    temp = raw.get("temperature") or {}
    return DailyWeather(
        date=day,
        temp_min_c=temp["min"],
        temp_max_c=temp["max"],
        temp_morning_c=temp.get("morning"),
        temp_day_c=temp.get("afternoon"),
        temp_evening_c=temp.get("evening"),
        temp_night_c=temp.get("night"),
        humidity=(raw.get("humidity") or {}).get("afternoon"),
        rain_mm=(raw.get("precipitation") or {}).get("total"),  # None if absent: genuinely unknown
        wind_max_kmh=_kmh(((raw.get("wind") or {}).get("max") or {}).get("speed")),
    )
