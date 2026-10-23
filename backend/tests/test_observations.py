import copy
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend.agent.observations import MAX_CHARS, hourly_stats, place_name, summarize, window_text
from backend.services import weather
from backend.services.cache import InMemoryCache
from backend.services.models import Location, TimeWindow, WeatherAlert
from backend.services.weather import DAY_SUMMARY_URL, ONECALL_URL, get_weather
from backend.tests.agent_fakes import candidates, fixture_now, onecall

pytestmark = pytest.mark.anyio

IST = ZoneInfo("Asia/Kolkata")
NOW = fixture_now("jaipur")  # Sun 18 Oct 2026 20:17 IST
JAIPUR = candidates("geoapify_search_jaipur.json")[0]
BANDRA_WEST = candidates("geoapify_search_bandra.json")[0]
FIXTURES = Path(__file__).parent / "fixtures"


def ist(d: int, h: int = 0) -> datetime:
    return datetime(2026, 10, d, h, tzinfo=IST)


def r(value: float) -> int:
    """Display rounding, half up."""
    return math.floor(value + 0.5)


def pct(fraction: float) -> str:
    return f"{5 * r(fraction * 20)}%"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weather, "cache", InMemoryCache())


@pytest.fixture
def jaipur_onecall(respx_mock):
    return respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(200, json=onecall("jaipur")))


async def result_for(start: datetime, end: datetime, location: Location = JAIPUR):
    return await get_weather(location, TimeWindow(start=start, end=end), now=NOW)


# --- labels ------------------------------------------------------------------


def test_place_name() -> None:
    assert place_name(BANDRA_WEST) == "Bandra West, Mumbai"
    assert place_name(JAIPUR) == "Jaipur"
    paris = candidates("geoapify_search_paris_city.json")[0]
    assert place_name(paris) == "Paris, France"


def test_window_text() -> None:
    assert window_text(TimeWindow(start=ist(19, 17), end=ist(19, 21))) == "Mon 19 Oct, 17:00-21:00 IST"
    assert window_text(TimeWindow(start=ist(19), end=ist(20))) == "Mon 19 Oct, 00:00-24:00 IST"
    assert window_text(TimeWindow(start=ist(19, 21), end=ist(20, 6))) == "Mon 19 Oct 21:00 - Tue 20 Oct 06:00 IST"


# --- hourly ------------------------------------------------------------------


async def test_hourly_observation(jaipur_onecall) -> None:
    result = await result_for(ist(19, 17), ist(19, 21))
    text = summarize(result)
    lines = text.splitlines()

    assert lines[0] == "Jaipur | FORECAST, hourly | Mon 19 Oct, 17:00-21:00 IST"
    hours = [line for line in lines if line.startswith("- ")]
    assert [h[:11] for h in hours] == ["- Mon 17:30", "- Mon 18:30", "- Mon 19:30", "- Mon 20:30"]
    first = result.hourly[0]
    assert f"{r(first.temp_c)}°C, feels {r(first.feels_like_c)}°C" in hours[0]
    assert f"wind {r(first.wind_kmh)} km/h" in hours[0]

    stats = hourly_stats(result.hourly)
    summary = lines[-1]
    assert summary.startswith("Summary of 4 hours: ")
    assert f"temp {r(stats.temp_min)}°C to {r(stats.temp_max)}°C" in summary
    assert f"max rain chance {pct(stats.rain_prob_max)} at {stats.rain_prob_max_at:%a %H:%M}" in summary
    assert f"total rain {stats.rain_total_mm:.1f} mm" in summary and f"max wind {r(stats.wind_max)} km/h" in summary


async def test_hourly_stats_find_the_wettest_hour(jaipur_onecall) -> None:
    result = await result_for(ist(19, 0), ist(19, 12))  # recorded rain at 05:30 and 06:30
    stats = hourly_stats(result.hourly)
    assert stats.rain_total_mm == pytest.approx(0.33 + 0.52)
    assert stats.rain_prob_max == max(p.rain_prob for p in result.hourly)


async def test_long_hourly_window_is_capped_and_sampled(jaipur_onecall) -> None:
    result = await result_for(NOW, NOW + timedelta(hours=47))  # every hourly point the fixture has
    assert len(result.hourly) >= 40
    full = summarize(result, max_chars=100_000)
    assert len(full) > MAX_CHARS

    text = summarize(result)
    assert len(text) <= MAX_CHARS
    assert "(every 2nd hour)" in text or "(every 3rd hour)" in text or "Hours" not in text
    assert f"Summary of {len(result.hourly)} hours" in text  # aggregates cover every hour, not the sample
    stats = hourly_stats(result.hourly)
    assert f"temp {r(stats.temp_min)}°C to {r(stats.temp_max)}°C" in text


async def test_cap_keeps_aggregates_even_when_hours_must_go(jaipur_onecall) -> None:
    result = await result_for(NOW, NOW + timedelta(hours=47))
    text = summarize(result, max_chars=400)
    assert len(text) <= 400
    assert "Summary of" in text
    assert not any(line.startswith("- ") for line in text.splitlines())  # no hour lines left


# --- daily, historical, current, alerts --------------------------------------


async def test_daily_observation_with_part_of_day(jaipur_onecall) -> None:
    result = await result_for(ist(22), ist(23))
    [day] = result.daily
    text = summarize(result, time_expr="evening")

    assert "Jaipur | FORECAST, daily | Thu 22 Oct, 00:00-24:00 IST" in text
    assert f"Thu 22 Oct: {r(day.temp_min_c)}°C to {r(day.temp_max_c)}°C, evening {r(day.temp_evening_c)}°C" in text
    assert f"rain chance {pct(day.rain_prob)}" in text and f"wind {r(day.wind_kmh)} km/h" in text
    assert day.summary in text
    assert "evening" not in summarize(result)  # no part of day asked -> not shown


async def test_historical_is_labelled_observed(respx_mock) -> None:
    data = json.loads((FIXTURES / "openweather_day_summary_jaipur.json").read_text(encoding="utf-8"))
    respx_mock.get(DAY_SUMMARY_URL).mock(return_value=httpx.Response(200, json=data))
    result = await result_for(ist(17), ist(18))
    text = summarize(result)
    assert "PAST WEATHER (observed, not a forecast)" in text
    assert "FORECAST" not in text
    assert "peak wind 15 km/h" in text  # day_summary wind is the day's peak


async def test_current_observation(jaipur_onecall) -> None:
    result = await weather.get_current(JAIPUR)
    [p] = result.hourly
    text = summarize(result)
    assert "CURRENT CONDITIONS (observed now)" in text
    assert text.splitlines()[1] == "Now (20:17): 25°C, feels 25°C, humidity 52%, wind 16 km/h, scattered clouds"
    assert (p.temp_c, p.feels_like_c) == (25.03, 24.95)  # the card keeps the precise values


# --- air quality ---------------------------------------------------------------


def air_result(country_code: str = "in", points=None, granularity: str = "hourly", **summary):
    from backend.services.air_quality import summarize as summarize_points
    from backend.services.models import AirComponents, AirQualityPoint, AirQualityResult

    if points is None:
        points = [AirQualityPoint(time=ist(23, 6 + i), components=AirComponents(pm2_5=60.4 + i, pm10=108.6),
                                  owm_aqi=3, naqi=n, naqi_category=c, dominant_pollutant="PM2.5")
                  for i, (n, c) in enumerate([(101, "Moderately Polluted"), (230, "Poor"), (97, "Satisfactory")])]
    location = JAIPUR if country_code == "in" else JAIPUR.model_copy(
        update={"country_code": country_code, "country": "France", "name": "Paris", "city": None})
    s = summarize_points(points).model_copy(update=summary)
    return AirQualityResult(location=location, window=TimeWindow(start=ist(23, 6), end=ist(23, 9)),
                            granularity=granularity, points=points, summary=s, fetched_at=ist(22, 12))


def test_air_observation_india_uses_naqi_and_labels_pm_as_concentrations() -> None:
    from backend.agent.observations import summarize_air

    lines = summarize_air(air_result()).splitlines()
    assert lines[0] == "Jaipur | AIR QUALITY FORECAST, hourly (India NAQI, CPCB) | Fri 23 Oct, 06:00-09:00 IST"
    assert lines[2] == "- Fri 06:00: NAQI 101, Moderately Polluted, PM2.5"
    assert lines[5] == ("Summary of 3 hours: worst NAQI 230, Poor, PM2.5 at Fri 07:00; "
                        "best NAQI 97, Satisfactory, PM2.5")
    assert lines[6] == "PM readings (hourly concentrations, not the index): PM2.5 averages 61 µg/m³, PM10 averages 109 µg/m³"


def test_air_observation_abroad_uses_openweather_scale_not_naqi() -> None:
    from backend.agent.observations import summarize_air

    text = summarize_air(air_result("fr"))
    assert "Paris, France | AIR QUALITY FORECAST, hourly (OpenWeather 1-5 scale, not NAQI)" in text
    assert "- Fri 06:00: 3/5 Moderate" in text and "worst 3/5 Moderate at Fri 06:00" in text
    assert "NAQI 101" not in text


def test_air_observation_india_without_naqi_falls_back_to_owm_with_reason() -> None:
    from backend.agent.observations import summarize_air
    from backend.services.models import AirComponents, AirQualityPoint

    points = [AirQualityPoint(time=ist(23, 6), components=AirComponents(pm2_5=40), owm_aqi=2,
                              naqi_reason="only 1 pollutant(s) available; NAQI needs 3")]
    text = summarize_air(air_result(points=points, granularity="current"))
    assert "AIR QUALITY NOW (observed) (OpenWeather 1-5 scale, not NAQI)" in text
    assert "Now: 2/5 Fair" in text and "PM2.5 40 µg/m³" in text  # one reading: no "averages"


def test_air_observation_marks_indicative_naqi_and_past_data() -> None:
    from backend.agent.observations import summarize_air

    text = summarize_air(air_result(granularity="historical", indicative=True))
    assert "PAST AIR QUALITY (observed, not a forecast)" in text
    assert "NAQI is indicative here" in text


def test_long_air_observation_is_sampled_under_the_cap() -> None:
    from backend.agent.observations import AIR_MAX_CHARS, summarize_air
    from backend.services.models import AirComponents, AirQualityPoint

    points = [AirQualityPoint(time=ist(23) + timedelta(hours=h), components=AirComponents(pm2_5=50), owm_aqi=3,
                              naqi=150 + h, naqi_category="Moderately Polluted", dominant_pollutant="PM10")
              for h in range(72)]
    text = summarize_air(air_result(points=points))
    assert len(text) <= AIR_MAX_CHARS and "Summary of 72 hours: worst NAQI 221" in text


def test_air_unavailable_note() -> None:
    from backend.agent.observations import air_unavailable

    assert air_unavailable(BANDRA_WEST, "OpenWeather request failed (HTTP 503).") == (
        "Bandra West, Mumbai | AIR QUALITY UNAVAILABLE: OpenWeather request failed (HTTP 503).")


@pytest.mark.parametrize("fraction, expected", [(0.0, "0%"), (0.04, "5%"), (0.024, "0%"), (0.025, "5%"),
                                                (0.26, "25%"), (0.775, "80%"), (1.0, "100%")])
def test_rain_chance_rounds_to_nearest_5(fraction: float, expected: str) -> None:
    from backend.agent.observations import _pct

    assert _pct(fraction) == expected


@pytest.mark.parametrize("celsius, expected", [(24.87, "25°C"), (24.5, "25°C"), (24.49, "24°C"), (-0.5, "0°C"),
                                               (-0.51, "-1°C")])
def test_temperatures_round_half_up_to_whole_degrees(celsius: float, expected: str) -> None:
    from backend.agent.observations import _t

    assert _t(celsius) == expected


async def test_alerts_come_first_and_are_trimmed(respx_mock) -> None:
    data = copy.deepcopy(onecall("jaipur"))
    data["alerts"] = [{"sender_name": "IMD", "event": "Thunderstorm", "description": "Storm warning. " * 60,
                       "start": int(ist(19, 15).timestamp()), "end": int(ist(19, 22).timestamp()), "tags": []}]
    respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(200, json=data))
    text = summarize(await result_for(ist(19, 17), ist(19, 21)))

    alert_line = text.splitlines()[1]
    assert alert_line.startswith("ALERT: Thunderstorm (IMD), Mon 19 Oct 15:00 - Mon 19 Oct 22:00: Storm warning.")
    description = alert_line.split(": ", 2)[2]
    assert len(description) <= 300 and description.endswith("…")


def test_empty_window_says_so() -> None:
    from backend.services.models import WeatherResult

    result = WeatherResult(location=JAIPUR, window=TimeWindow(start=ist(19), end=ist(20)), granularity="hourly",
                           fetched_at=NOW)
    assert summarize(result).endswith("No data points in this window.")


def test_alert_model_round_trip() -> None:
    alert = WeatherAlert(sender="IMD", event="Heat", start=ist(19), end=ist(20), description="")
    assert alert.end > alert.start
