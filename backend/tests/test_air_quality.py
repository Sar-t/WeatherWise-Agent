import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from backend.services import air_quality, naqi
from backend.services.air_quality import (
    CURRENT_URL,
    FORECAST_URL,
    HISTORY_URL,
    get_air_quality,
    get_air_quality_multi,
    get_current_air_quality,
)
from backend.services.cache import InMemoryCache
from backend.services.errors import InvalidInputError, OutOfRangeError
from backend.services.models import Location, TimeWindow

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures"
IST = ZoneInfo("Asia/Kolkata")


def fixture(kind: str) -> dict:
    return json.loads((FIXTURES / f"openweather_air_{kind}_delhi.json").read_text(encoding="utf-8"))


CURRENT, FORECAST, HISTORY = fixture("current"), fixture("forecast"), fixture("history")
NOW = datetime.fromtimestamp(CURRENT["list"][0]["dt"], IST)  # Thu 22 Oct 2026 11:51 IST
FIRST_FORECAST = datetime.fromtimestamp(FORECAST["list"][0]["dt"], timezone.utc)  # Thu 06:00 UTC = 11:30 IST
DELHI = Location(name="New Delhi", lat=28.6139, lon=77.209, country="India", country_code="in",
                 formatted="New Delhi, India", result_type="city", timezone="Asia/Kolkata")


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=IST)


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
def _fresh_cache(monkeypatch: pytest.MonkeyPatch, clock: FakeClock) -> None:
    monkeypatch.setattr(air_quality, "cache", InMemoryCache(clock=clock))


@pytest.fixture
def routes(respx_mock):
    return {
        "current": respx_mock.get(CURRENT_URL).mock(return_value=httpx.Response(200, json=CURRENT)),
        "forecast": respx_mock.get(FORECAST_URL).mock(return_value=httpx.Response(200, json=FORECAST)),
        "history": respx_mock.get(HISTORY_URL).mock(return_value=httpx.Response(200, json=HISTORY)),
    }


def sent(route, name: str) -> int:
    return int(route.calls.last.request.url.params[name])


# --- current -----------------------------------------------------------------


async def test_current_uses_last_24h_for_naqi(routes) -> None:
    result = await get_current_air_quality(DELHI)
    [point] = result.points

    assert result.granularity == "current"
    assert point.time == NOW and point.time.tzinfo.key == "Asia/Kolkata"
    assert point.components.pm2_5 == CURRENT["list"][0]["components"]["pm2_5"]
    assert point.owm_aqi == CURRENT["list"][0]["main"]["aqi"]
    assert point.naqi is not None and point.naqi_category and point.dominant_pollutant
    assert not point.indicative  # 24 h of history behind it
    hour = datetime(2026, 10, 22, 6, tzinfo=timezone.utc)  # 11:51 IST floored to the UTC hour
    assert (sent(routes["history"], "start"), sent(routes["history"], "end")) == (
        int((hour - timedelta(hours=24)).timestamp()), int(hour.timestamp()),
    )
    params = routes["current"].calls.last.request.url.params
    assert (params["lat"], params["lon"]) == ("28.61", "77.21")


# --- routing -------------------------------------------------------------------


async def test_future_window_uses_forecast_points_only(routes) -> None:
    result = await get_air_quality(DELHI, TimeWindow(start=ist(23, 6), end=ist(23, 12)), now=NOW)

    assert result.granularity == "hourly"
    assert [p.time for p in result.points] == [ist(23, h, 30) for h in range(6, 12)]
    assert all(p.time.utcoffset() == timedelta(hours=5, minutes=30) for p in result.points)
    assert routes["forecast"].call_count == 1 and routes["history"].call_count == 1
    assert sent(routes["history"], "end") == int(FIRST_FORECAST.timestamp())  # history only feeds averages
    assert sent(routes["history"], "start") == int((FIRST_FORECAST - timedelta(hours=24)).timestamp())


async def test_first_forecast_hours_average_over_history(routes, respx_mock) -> None:
    window = TimeWindow(start=ist(22, 11, 30), end=ist(22, 13))
    with_history = await get_air_quality(DELHI, window, now=ist(22, 11))

    first = with_history.points[0]
    series = air_quality._series(HISTORY["list"] + FORECAST["list"])
    expected = naqi.compute_naqi(naqi.rolling_averages(series, first.time))
    assert (first.naqi, first.sub_indices, first.indicative) == (expected.value, expected.sub_indices, False)
    assert all(p.time >= window.start for p in with_history.points)  # history hours not returned as points

    air_quality.cache.clear()
    respx_mock.get(HISTORY_URL).mock(return_value=httpx.Response(200, json={"coord": {}, "list": []}))
    without_history = await get_air_quality(DELHI, window, now=ist(22, 11))
    assert without_history.points[0].indicative  # only 1 forecast hour to average


async def test_window_spanning_now_is_trimmed_and_forecast(routes) -> None:
    result = await get_air_quality(DELHI, TimeWindow(start=ist(22, 0), end=ist(22, 14)), now=NOW)
    assert result.granularity == "hourly"
    assert result.window.start == NOW
    assert [p.time for p in result.points] == [ist(22, 12, 30), ist(22, 13, 30)]


async def test_beyond_forecast_horizon_is_out_of_range(routes) -> None:
    last = datetime.fromtimestamp(FORECAST["list"][-1]["dt"], IST)  # Mon 26 Oct 10:30
    with pytest.raises(OutOfRangeError, match="Air quality forecasts are available up to 4 days ahead"):
        await get_air_quality(DELHI, TimeWindow(start=last, end=last + timedelta(hours=2)), now=NOW)
    # the last forecast hour itself is still in range
    result = await get_air_quality(DELHI, TimeWindow(start=last, end=last + timedelta(hours=1)), now=NOW)
    assert [p.time for p in result.points] == [last]


async def test_past_window_uses_history_with_averaging_lead(routes) -> None:
    window = TimeWindow(start=ist(21, 17), end=ist(21, 21))  # Wed evening
    result = await get_air_quality(DELHI, window, now=NOW)

    assert result.granularity == "historical"
    assert [p.time for p in result.points] == [ist(21, h, 30) for h in (17, 18, 19, 20)]
    assert routes["forecast"].call_count == 0
    # Wed 17:00 IST = 11:30 UTC, floored to 11:00 UTC, minus 24 h of averaging lead.
    assert sent(routes["history"], "start") == int(datetime(2026, 10, 20, 11, tzinfo=timezone.utc).timestamp())
    # The recorded history starts Wed 11:30, so the first evening hours average < 16 h: indicative.
    assert result.points[0].indicative


async def test_history_before_november_2020_is_out_of_range(respx_mock) -> None:
    old = TimeWindow(start=datetime(2020, 11, 1, tzinfo=IST), end=datetime(2020, 11, 2, tzinfo=IST))
    with pytest.raises(OutOfRangeError, match="only available from 27 November 2020"):
        await get_air_quality(DELHI, old, now=NOW)
    assert not respx_mock.calls


async def test_naive_now_rejected() -> None:
    with pytest.raises(InvalidInputError):
        await get_air_quality(DELHI, TimeWindow(start=ist(23, 6), end=ist(23, 12)), now=datetime(2026, 10, 22))


# --- summary -------------------------------------------------------------------


async def test_summary(routes) -> None:
    result = await get_air_quality(DELHI, TimeWindow(start=ist(23, 0), end=ist(24, 0)), now=NOW)
    points, s = result.points, result.summary
    worst = max(points, key=lambda p: p.naqi)
    assert (s.naqi_max, s.naqi_max_at, s.naqi_category, s.dominant_pollutant) == (
        worst.naqi, worst.time, worst.naqi_category, worst.dominant_pollutant,
    )
    assert s.pm2_5_avg == pytest.approx(sum(p.components.pm2_5 for p in points) / len(points), abs=0.05)
    assert s.pm10_avg == pytest.approx(sum(p.components.pm10 for p in points) / len(points), abs=0.05)
    assert s.owm_aqi_max == max(p.owm_aqi for p in points)


# --- multi, cache ----------------------------------------------------------------


async def test_multi_reuses_one_forecast_and_one_history(routes) -> None:
    windows = [TimeWindow(start=ist(23, 6), end=ist(23, 12)), TimeWindow(start=ist(24, 17), end=ist(24, 21))]
    morning, evening = await get_air_quality_multi(DELHI, windows, now=NOW)
    assert (routes["forecast"].call_count, routes["history"].call_count) == (1, 1)
    assert (len(morning.points), len(evening.points)) == (6, 4)


async def test_forecast_cached_30_minutes(routes, clock: FakeClock) -> None:
    window = TimeWindow(start=ist(23, 6), end=ist(23, 12))
    await get_air_quality(DELHI, window, now=NOW)
    clock.t += 29 * 60
    await get_air_quality(DELHI, window, now=NOW)
    assert routes["forecast"].call_count == 1
    clock.t += 2 * 60
    await get_air_quality(DELHI, window, now=NOW)
    assert routes["forecast"].call_count == 2
    assert routes["history"].call_count == 1  # history is cached for 24 h


async def test_history_cached_24_hours(routes, clock: FakeClock) -> None:
    window = TimeWindow(start=ist(21, 17), end=ist(21, 21))
    await get_air_quality(DELHI, window, now=NOW)
    clock.t += 23 * 3600
    await get_air_quality(DELHI, window, now=NOW)
    assert routes["history"].call_count == 1
    clock.t += 3600
    await get_air_quality(DELHI, window, now=NOW)
    assert routes["history"].call_count == 2


# --- missing data ----------------------------------------------------------------


async def test_missing_components_do_not_crash(respx_mock) -> None:
    sparse = copy.deepcopy(FORECAST)
    for entry in sparse["list"]:
        entry["components"] = {"pm2_5": entry["components"]["pm2_5"], "no2": None}
        entry.pop("main")
    respx_mock.get(FORECAST_URL).mock(return_value=httpx.Response(200, json=sparse))
    respx_mock.get(HISTORY_URL).mock(return_value=httpx.Response(200, json={"list": []}))

    result = await get_air_quality(DELHI, TimeWindow(start=ist(23, 6), end=ist(23, 8)), now=NOW)
    point = result.points[0]
    assert point.components.pm10 is None and point.owm_aqi is None
    assert point.naqi is None and "needs 3" in point.naqi_reason
    assert result.summary.naqi_max is None and result.summary.pm2_5_avg is not None
