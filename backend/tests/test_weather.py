import copy
import json
import logging
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend.core.config import get_settings
from backend.services import weather
from backend.services.cache import InMemoryCache
from backend.services.date_resolver import resolve
from backend.services.errors import InvalidInputError, OutOfRangeError, UpstreamAPIError
from backend.services.models import Location, TimeWindow
from backend.services.weather import (
    DAY_SUMMARY_URL,
    ONECALL_URL,
    TIMEMACHINE_URL,
    get_current,
    get_weather,
    get_weather_multi,
)

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures"
IST = ZoneInfo("Asia/Kolkata")
TZ = "Asia/Kolkata"
FAKE_KEY = "test-ow-key-do-not-leak"

JAIPUR = Location(
    name="Jaipur",
    city="Jaipur",
    lat=26.9155,
    lon=75.7880,
    country="India",
    country_code="in",
    state="Rajasthan",
    formatted="Jaipur, RJ, India",
    result_type="city",
    timezone=TZ,
)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


ONECALL = fixture("openweather_onecall_jaipur.json")
TIMEMACHINE = fixture("openweather_timemachine_jaipur.json")
DAY_SUMMARY = fixture("openweather_day_summary_jaipur.json")
NOW = datetime.fromtimestamp(ONECALL["current"]["dt"], IST)  # Sunday 2026-10-18 20:17 IST


def ist(y: int, m: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=IST)


def window(date_expr: str, time_expr: str | None = None) -> TimeWindow:
    [w] = resolve(date_expr, time_expr, TZ, now=NOW)
    return w


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, clock: FakeClock) -> None:
    """Fresh cache on a fake clock, and a fake API key, for every test."""
    monkeypatch.setattr(weather, "cache", InMemoryCache(clock=clock))
    monkeypatch.setenv("OPENWEATHER_API_KEY", FAKE_KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def onecall(respx_mock):
    return respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(200, json=ONECALL))


@pytest.fixture
def timemachine(respx_mock):
    return respx_mock.get(TIMEMACHINE_URL).mock(return_value=httpx.Response(200, json=TIMEMACHINE))


@pytest.fixture
def day_summary(respx_mock):
    return respx_mock.get(DAY_SUMMARY_URL).mock(return_value=httpx.Response(200, json=DAY_SUMMARY))


# --- routing ---------------------------------------------------------------


async def test_short_past_window_uses_one_timemachine_call_per_hour(timemachine) -> None:
    result = await get_weather(JAIPUR, window("yesterday", "evening"), now=NOW)  # 17:00-21:00

    assert result.granularity == "historical"
    sent = [int(c.request.url.params["dt"]) for c in timemachine.calls]
    assert sent == [int(ist(2026, 10, 17, h).timestamp()) for h in (17, 18, 19, 20)]
    assert len(result.hourly) == 4 and not result.daily


async def test_six_hour_past_window_still_uses_timemachine(timemachine) -> None:
    await get_weather(JAIPUR, window("yesterday", "12:00-18:00"), now=NOW)
    assert timemachine.call_count == 6


async def test_timemachine_point_is_parsed(timemachine) -> None:
    result = await get_weather(JAIPUR, window("yesterday", "6pm-7pm"), now=NOW)
    [point] = result.hourly
    raw = TIMEMACHINE["data"][0]

    assert point.time == ist(2026, 10, 17, 18)
    assert point.temp_c == raw["temp"]
    assert point.wind_kmh == pytest.approx(raw["wind_speed"] * 3.6)
    assert point.wind_gust_kmh is None  # timemachine has no gust here
    assert point.rain_prob is None  # observations have no pop
    assert (point.condition, point.description, point.icon) == ("Clear", "clear sky", "01n")


async def test_long_past_window_uses_day_summary(day_summary) -> None:
    result = await get_weather(JAIPUR, window("yesterday"), now=NOW)

    params = day_summary.calls.last.request.url.params
    assert (params["date"], params["tz"]) == ("2026-10-17", "+05:30")
    assert result.granularity == "historical"
    [day] = result.daily
    assert day.date == date(2026, 10, 17)
    assert (day.temp_min_c, day.temp_max_c) == (21.62, 30.62)
    assert (day.temp_morning_c, day.temp_day_c, day.temp_evening_c, day.temp_night_c) == (21.87, 28.37, 28.87, 23.62)
    assert day.humidity == 56.75


async def test_day_summary_one_call_per_local_date(day_summary) -> None:
    two_days = TimeWindow(start=ist(2026, 10, 16), end=ist(2026, 10, 18))
    result = await get_weather(JAIPUR, two_days, now=NOW)
    assert [c.request.url.params["date"] for c in day_summary.calls] == ["2026-10-16", "2026-10-17"]
    assert [d.date for d in result.daily] == [date(2026, 10, 16), date(2026, 10, 17)]


async def test_window_within_48h_is_hourly(onecall) -> None:
    result = await get_weather(JAIPUR, window("tomorrow", "evening"), now=NOW)

    assert result.granularity == "hourly"
    assert not result.daily
    params = onecall.calls.last.request.url.params
    assert (params["exclude"], params["units"], params["lang"]) == ("minutely", "metric", "en")
    assert (params["lat"], params["lon"]) == ("26.92", "75.79")  # rounded to 2 decimals


async def test_tomorrow_evening_has_four_hourly_points(onecall) -> None:
    result = await get_weather(JAIPUR, window("tomorrow", "evening"), now=NOW)  # 17:00-21:00
    # OpenWeather hours are whole UTC hours, i.e. :30 in IST.
    assert [p.time for p in result.hourly] == [ist(2026, 10, 19, h, 30) for h in (17, 18, 19, 20)]


# --- sunrise / sunset --------------------------------------------------------


def _sun_of(entry: dict) -> tuple[datetime, datetime]:
    return datetime.fromtimestamp(entry["sunrise"], IST), datetime.fromtimestamp(entry["sunset"], IST)


async def test_hourly_and_daily_results_carry_sun_times_per_local_date(onecall) -> None:
    hourly = await get_weather(JAIPUR, window("tomorrow", "evening"), now=NOW)
    [sun] = hourly.sun
    assert sun.date == date(2026, 10, 19) and (sun.sunrise, sun.sunset) == _sun_of(ONECALL["daily"][1])
    assert sun.sunrise.tzinfo.key == TZ

    night = await get_weather(JAIPUR, window("today", "night"), now=NOW)  # 21:00-06:00
    assert [s.date for s in night.sun] == [date(2026, 10, 18), date(2026, 10, 19)]

    daily = await get_weather(JAIPUR, window("next_thursday"), now=NOW)
    assert [(s.date, s.sunrise, s.sunset) for s in daily.sun] == [(date(2026, 10, 22), *_sun_of(ONECALL["daily"][4]))]

    current = await get_current(JAIPUR)
    assert [s.date for s in current.sun] == [date(2026, 10, 18)]


async def test_past_hours_take_sun_times_from_timemachine(timemachine) -> None:
    result = await get_weather(JAIPUR, window("yesterday", "6pm-7pm"), now=NOW)
    assert [(s.date, s.sunrise, s.sunset) for s in result.sun] == [(date(2026, 10, 17), *_sun_of(TIMEMACHINE["data"][0]))]


async def test_day_summary_has_no_sun_times(day_summary) -> None:
    assert (await get_weather(JAIPUR, window("yesterday"), now=NOW)).sun == []


async def test_points_carry_condition_code_pressure_and_wind_direction(onecall) -> None:
    current = (await get_current(JAIPUR)).hourly[0]
    raw = ONECALL["current"]
    assert (current.condition_id, current.pressure_hpa, current.wind_deg) == (
        raw["weather"][0]["id"], raw["pressure"], raw["wind_deg"])
    daily = (await get_weather(JAIPUR, window("next_thursday"), now=NOW)).daily[0]
    assert daily.condition_id == ONECALL["daily"][4]["weather"][0]["id"]


async def test_window_within_8_days_is_daily(onecall) -> None:
    result = await get_weather(JAIPUR, window("next_thursday"), now=NOW)  # 2026-10-22
    assert result.granularity == "daily"
    assert [d.date for d in result.daily] == [date(2026, 10, 22)]
    assert not result.hourly


async def test_last_forecast_day_is_in_range(onecall) -> None:
    result = await get_weather(JAIPUR, window("next_sunday"), now=NOW)  # 2026-10-25 = today + 7
    assert [d.date for d in result.daily] == [date(2026, 10, 25)]


async def test_beyond_8_days_is_out_of_range(respx_mock) -> None:
    nine_days = TimeWindow(start=ist(2026, 10, 26), end=ist(2026, 10, 27))
    with pytest.raises(OutOfRangeError, match="up to 8 days ahead"):
        await get_weather(JAIPUR, nine_days, now=NOW)
    assert not respx_mock.calls


async def test_window_spanning_now_is_trimmed_and_forecast(onecall) -> None:
    result = await get_weather(JAIPUR, window("today", "evening"), now=NOW)  # 17:00-21:00, now 20:17

    assert result.granularity == "hourly"
    assert (result.window.start, result.window.end) == (NOW, ist(2026, 10, 18, 21))
    assert [p.time for p in result.hourly] == [ist(2026, 10, 18, 20, 30)]


async def test_short_trimmed_window_gets_the_covering_point(onecall) -> None:
    result = await get_weather(JAIPUR, TimeWindow(start=ist(2026, 10, 18, 20), end=ist(2026, 10, 18, 20, 25)), now=NOW)
    assert [p.time for p in result.hourly] == [ist(2026, 10, 18, 19, 30)]


async def test_naive_now_rejected() -> None:
    with pytest.raises(InvalidInputError, match="timezone-aware"):
        await get_weather(JAIPUR, window("tomorrow"), now=datetime(2026, 10, 18, 20))


# --- parsing ---------------------------------------------------------------


async def test_wind_is_converted_to_kmh(onecall) -> None:
    result = await get_weather(JAIPUR, window("tomorrow"), now=NOW)
    first = result.hourly[0]
    raw = next(h for h in ONECALL["hourly"] if datetime.fromtimestamp(h["dt"], IST) == first.time)
    assert first.wind_kmh == pytest.approx(raw["wind_speed"] * 3.6)
    assert first.wind_gust_kmh == pytest.approx(raw["wind_gust"] * 3.6)


async def test_timestamps_are_in_location_timezone(onecall) -> None:
    result = await get_weather(JAIPUR, window("tomorrow"), now=NOW)
    assert result.hourly
    for point in result.hourly:
        assert point.time.tzinfo.key == TZ
        assert point.time.utcoffset() == timedelta(hours=5, minutes=30)
    assert result.fetched_at.utcoffset() == timedelta(hours=5, minutes=30)


async def test_daily_entries_chosen_by_local_date(onecall) -> None:
    three_days = TimeWindow(start=ist(2026, 10, 21, 21), end=ist(2026, 10, 23, 6))  # touches 21st, 22nd, 23rd
    result = await get_weather(JAIPUR, three_days, now=NOW)

    assert [d.date for d in result.daily] == [date(2026, 10, 21), date(2026, 10, 22), date(2026, 10, 23)]
    by_date = {datetime.fromtimestamp(d["dt"], IST).date(): d for d in ONECALL["daily"]}
    for day in result.daily:
        assert day.temp_max_c == by_date[day.date]["temp"]["max"]


async def test_daily_forecast_fields(onecall) -> None:
    result = await get_weather(JAIPUR, window("next_thursday"), now=NOW)
    [day] = result.daily
    raw = next(d for d in ONECALL["daily"] if datetime.fromtimestamp(d["dt"], IST).date() == date(2026, 10, 22))

    assert day.wind_kmh == pytest.approx(raw["wind_speed"] * 3.6)
    assert day.wind_max_kmh == pytest.approx(raw["wind_gust"] * 3.6)
    assert day.rain_prob == raw["pop"]
    assert day.summary == raw["summary"]
    assert (day.temp_morning_c, day.temp_night_c) == (raw["temp"]["morn"], raw["temp"]["night"])


async def test_missing_optional_fields_do_not_crash(respx_mock) -> None:
    stripped = copy.deepcopy(ONECALL)
    optional = ("pop", "wind_gust", "uvi", "visibility", "clouds", "rain", "snow", "weather", "summary", "feels_like")
    for entry in stripped["hourly"] + stripped["daily"]:
        for key in optional:
            if key != "feels_like" or entry in stripped["daily"]:
                entry.pop(key, None)
    respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(200, json=stripped))

    hourly = (await get_weather(JAIPUR, window("tomorrow", "evening"), now=NOW)).hourly[0]
    daily = (await get_weather(JAIPUR, window("next_thursday"), now=NOW)).daily[0]

    assert (hourly.rain_prob, hourly.wind_gust_kmh, hourly.uvi, hourly.visibility_m) == (None, None, None, None)
    assert (hourly.condition, hourly.description, hourly.icon) == (None, None, None)
    assert (daily.rain_prob, daily.wind_max_kmh, daily.uvi, daily.summary, daily.feels_like_day_c) == (
        None, None, None, None, None,
    )


# --- rain/snow default to 0.0; genuinely unknown stays None ----------------


async def test_hourly_rain_present_and_missing(onecall) -> None:
    result = await get_weather(JAIPUR, window("tomorrow"), now=NOW)  # rain at 05:30 and 06:30 only
    by_time = {p.time: p for p in result.hourly}
    for raw in ONECALL["hourly"]:
        point = by_time.get(datetime.fromtimestamp(raw["dt"], IST))
        if point is not None:
            assert point.rain_mm == raw.get("rain", {}).get("1h", 0.0)
            assert point.snow_mm == 0.0
    assert any(p.rain_mm > 0 for p in result.hourly) and any(p.rain_mm == 0.0 for p in result.hourly)


async def test_daily_rain_present_and_missing(onecall) -> None:
    # In the recorded response the 24th has rain (0.66 mm) and the 21st has no rain key.
    result = await get_weather(JAIPUR, TimeWindow(start=ist(2026, 10, 21), end=ist(2026, 10, 25)), now=NOW)
    rain = {d.date.day: (d.rain_mm, d.snow_mm) for d in result.daily}
    assert rain[21] == (0.0, 0.0)
    assert rain[24] == (0.66, 0.0)


async def test_current_and_timemachine_rain_missing_means_zero(onecall, timemachine) -> None:
    assert "rain" not in ONECALL["current"] and "rain" not in TIMEMACHINE["data"][0]
    current = (await get_current(JAIPUR)).hourly[0]
    past = (await get_weather(JAIPUR, window("yesterday", "6pm-7pm"), now=NOW)).hourly[0]
    assert (current.rain_mm, current.snow_mm) == (0.0, 0.0)
    assert (past.rain_mm, past.snow_mm) == (0.0, 0.0)


async def test_day_summary_missing_precipitation_is_unknown(respx_mock) -> None:
    data = copy.deepcopy(DAY_SUMMARY)
    del data["precipitation"]
    respx_mock.get(DAY_SUMMARY_URL).mock(return_value=httpx.Response(200, json=data))
    [day] = (await get_weather(JAIPUR, window("yesterday"), now=NOW)).daily
    assert day.rain_mm is None and day.snow_mm is None


async def test_day_summary_precipitation_total_is_rain(day_summary) -> None:
    [day] = (await get_weather(JAIPUR, window("yesterday"), now=NOW)).daily
    assert day.rain_mm == DAY_SUMMARY["precipitation"]["total"] == 0.0


# --- wind: typical vs peak -------------------------------------------------


async def test_day_summary_fills_only_wind_max(day_summary) -> None:
    [day] = (await get_weather(JAIPUR, window("yesterday"), now=NOW)).daily
    assert day.wind_kmh is None
    assert day.wind_max_kmh == pytest.approx(DAY_SUMMARY["wind"]["max"]["speed"] * 3.6)


async def test_daily_forecast_without_gust_has_no_wind_max(respx_mock) -> None:
    data = copy.deepcopy(ONECALL)
    for d in data["daily"]:
        d.pop("wind_gust")
    respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(200, json=data))
    [day] = (await get_weather(JAIPUR, window("next_thursday"), now=NOW)).daily
    assert day.wind_kmh is not None and day.wind_max_kmh is None


# --- alerts ----------------------------------------------------------------


async def test_alerts_filtered_to_window(respx_mock) -> None:
    # The recorded response has no alerts, so add two to a copy of it.
    data = copy.deepcopy(ONECALL)
    data["alerts"] = [
        {"sender_name": "IMD", "event": "Thunderstorm", "description": "Evening storms",
         "start": int(ist(2026, 10, 19, 15).timestamp()), "end": int(ist(2026, 10, 19, 18).timestamp()), "tags": []},
        {"sender_name": "IMD", "event": "Heat wave", "description": "Hot",
         "start": int(ist(2026, 10, 21, 9).timestamp()), "end": int(ist(2026, 10, 21, 18).timestamp()), "tags": []},
    ]
    respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(200, json=data))

    result = await get_weather(JAIPUR, window("tomorrow", "evening"), now=NOW)
    [alert] = result.alerts
    assert (alert.sender, alert.event, alert.description) == ("IMD", "Thunderstorm", "Evening storms")
    assert (alert.start, alert.end) == (ist(2026, 10, 19, 15), ist(2026, 10, 19, 18))

    later = await get_weather(JAIPUR, window("day_after_tomorrow", "morning"), now=NOW)
    assert later.alerts == []


# --- current ---------------------------------------------------------------


async def test_get_current(onecall) -> None:
    result = await get_current(JAIPUR)
    [point] = result.hourly

    assert result.granularity == "current"
    assert point.time == NOW
    assert point.temp_c == ONECALL["current"]["temp"]
    assert (result.window.start, result.window.end) == (NOW, NOW + timedelta(hours=1))


# --- multi -----------------------------------------------------------------


async def test_multi_reuses_one_onecall_for_two_windows(onecall) -> None:
    windows = [window("tomorrow", "morning"), window("next_thursday")]
    hourly, daily = await get_weather_multi(JAIPUR, windows, now=NOW)

    assert onecall.call_count == 1
    assert (hourly.granularity, daily.granularity) == ("hourly", "daily")
    assert len(hourly.hourly) == 6 and len(daily.daily) == 1


async def test_multi_mixes_history_and_forecast(onecall, day_summary) -> None:
    results = await get_weather_multi(JAIPUR, [window("yesterday"), window("tomorrow")], now=NOW)
    assert [r.granularity for r in results] == ["historical", "hourly"]
    assert (onecall.call_count, day_summary.call_count) == (1, 1)


async def test_multi_checks_range_before_any_request(respx_mock) -> None:
    far = TimeWindow(start=ist(2026, 10, 30), end=ist(2026, 10, 31))
    with pytest.raises(OutOfRangeError):
        await get_weather_multi(JAIPUR, [window("tomorrow"), far], now=NOW)
    assert not respx_mock.calls


async def test_upstream_call_count_is_logged(onecall, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="backend.services.weather")
    await get_weather_multi(JAIPUR, [window("tomorrow", "morning"), window("tomorrow", "evening")], now=NOW)
    [record] = [r for r in caplog.records if r.getMessage() == "weather upstream calls"]
    assert (record.calls, record.windows) == (1, 2)


# --- cache -----------------------------------------------------------------


async def test_cache_hit_within_10_minutes_then_expires(onecall, clock: FakeClock) -> None:
    w = window("tomorrow")
    await get_weather(JAIPUR, w, now=NOW)
    clock.t += 9 * 60 + 59
    await get_weather(JAIPUR, w, now=NOW)
    assert onecall.call_count == 1

    clock.t += 2
    await get_weather(JAIPUR, w, now=NOW)
    assert onecall.call_count == 2


async def test_cache_shared_by_nearby_coordinates(onecall) -> None:
    nearby = JAIPUR.model_copy(update={"lat": 26.9201, "lon": 75.7899})  # same 2-decimal cell
    await get_weather(JAIPUR, window("tomorrow"), now=NOW)
    await get_current(nearby)
    assert onecall.call_count == 1


async def test_history_cached_for_24_hours(day_summary, clock: FakeClock) -> None:
    w = window("yesterday")
    await get_weather(JAIPUR, w, now=NOW)
    clock.t += 23 * 3600
    await get_weather(JAIPUR, w, now=NOW)
    assert day_summary.call_count == 1
    clock.t += 3600
    await get_weather(JAIPUR, w, now=NOW)
    assert day_summary.call_count == 2


async def test_cached_result_keeps_original_fetch_time(onecall, clock: FakeClock) -> None:
    first = await get_weather(JAIPUR, window("tomorrow"), now=NOW)
    clock.t += 60
    second = await get_weather(JAIPUR, window("tomorrow"), now=NOW)
    assert second.fetched_at == first.fetched_at


# --- errors ----------------------------------------------------------------


async def test_401_mentions_one_call_subscription(respx_mock, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(401, json={"cod": 401}))
    with pytest.raises(UpstreamAPIError, match="One Call API 3.0' subscription") as exc_info:
        await get_weather(JAIPUR, window("tomorrow"), now=NOW)
    assert exc_info.value.status_code == 401
    assert FAKE_KEY not in str(exc_info.value) and FAKE_KEY not in caplog.text


async def test_404_from_history_is_out_of_range(respx_mock) -> None:
    respx_mock.get(DAY_SUMMARY_URL).mock(return_value=httpx.Response(404, json={"cod": 404}))
    with pytest.raises(OutOfRangeError, match="no historical data"):
        await get_weather(JAIPUR, window("yesterday"), now=NOW)


async def test_404_from_onecall_is_upstream_error(respx_mock) -> None:
    respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(404, json={"cod": 404}))
    with pytest.raises(UpstreamAPIError, match="HTTP 404") as exc_info:
        await get_weather(JAIPUR, window("tomorrow"), now=NOW)
    assert not isinstance(exc_info.value, OutOfRangeError)


async def test_errors_are_not_cached(respx_mock) -> None:
    route = respx_mock.get(ONECALL_URL).mock(
        side_effect=[httpx.Response(429), httpx.Response(200, json=ONECALL)]
    )
    with pytest.raises(UpstreamAPIError, match="rate limit"):
        await get_weather(JAIPUR, window("tomorrow"), now=NOW)
    await get_weather(JAIPUR, window("tomorrow"), now=NOW)
    assert route.call_count == 2


# --- helpers ---------------------------------------------------------------


@pytest.mark.parametrize(
    "tz, day, offset",
    [
        ("Asia/Kolkata", date(2026, 10, 17), "+05:30"),
        ("Europe/London", date(2026, 10, 17), "+01:00"),
        ("Europe/London", date(2026, 10, 26), "+00:00"),
        ("America/New_York", date(2026, 10, 17), "-04:00"),
    ],
)
def test_day_summary_tz_offset(tz: str, day: date, offset: str) -> None:
    assert weather._utc_offset(day, ZoneInfo(tz)) == offset
