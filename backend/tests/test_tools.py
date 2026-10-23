import httpx
import pytest

from backend.agent.tools import describe_args, geocode_place, get_current_weather, get_weather_for_windows
from backend.services import weather
from backend.services.cache import InMemoryCache
from backend.services.models import Location, TimeWindow
from backend.services.weather import ONECALL_URL
from backend.tests.agent_fakes import IST, candidates, fixture_now, install_fake_geocoding, onecall

pytestmark = pytest.mark.anyio

NOW = fixture_now("jaipur")
JAIPUR = candidates("geoapify_search_jaipur.json")[0]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch, respx_mock) -> None:
    install_fake_geocoding(monkeypatch)
    monkeypatch.setattr(weather, "cache", InMemoryCache())
    respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(200, json=onecall("jaipur")))


def as_call(tool, **args) -> dict:
    return {"type": "tool_call", "id": "call-1", "name": tool.name, "args": args}


async def test_tools_have_names_descriptions_and_llm_safe_schemas() -> None:
    for tool in (geocode_place, get_current_weather, get_weather_for_windows):
        assert tool.description and tool.response_format == "content_and_artifact"
    assert list(get_weather_for_windows.tool_call_schema.model_json_schema()["properties"]) == [
        "location", "windows", "time_expr",
    ]  # `now` is injected by code, never chosen by an LLM


async def test_air_quality_tool_returns_observation_and_cards(respx_mock, monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import datetime

    from backend.agent.tools import get_air_quality_for_windows
    from backend.services import air_quality
    from backend.tests.agent_fakes import mock_air_quality

    monkeypatch.setattr(air_quality, "cache", InMemoryCache())
    mock_air_quality(respx_mock)
    delhi = candidates("geoapify_search_delhi_city.json")[0]
    windows = [TimeWindow(start=datetime(2026, 10, 23, h, tzinfo=IST), end=datetime(2026, 10, 23, h + 3, tzinfo=IST))
               for h in (6, 17)]
    message = await get_air_quality_for_windows.ainvoke(as_call(
        get_air_quality_for_windows, location=delhi, windows=windows, now=fixture_now("delhi_aq")))

    assert [len(r.points) for r in message.artifact] == [3, 3]
    assert message.content.count("New Delhi | AIR QUALITY FORECAST, hourly (India NAQI, CPCB)") == 2
    assert list(get_air_quality_for_windows.tool_call_schema.model_json_schema()["properties"]) == [
        "location", "windows"]


async def test_geocode_place_lists_candidates() -> None:
    message = await geocode_place.ainvoke(as_call(geocode_place, query="Aurangabad"))
    lines = message.content.splitlines()
    assert lines[0].startswith("1. Chhatrapati Sambhajinagar (Aurangabad), Maharashtra (city; 19.877, 75.339;")
    assert len(lines) == len(message.artifact) == 5
    assert all(isinstance(c, Location) for c in message.artifact)


async def test_get_weather_for_windows_returns_observation_and_results() -> None:
    from datetime import datetime

    window = TimeWindow(start=datetime(2026, 10, 19, 17, tzinfo=IST), end=datetime(2026, 10, 19, 21, tzinfo=IST))
    message = await get_weather_for_windows.ainvoke(
        as_call(get_weather_for_windows, location=JAIPUR, windows=[window], now=NOW)
    )
    assert message.content.startswith("Jaipur | FORECAST, hourly | Mon 19 Oct, 17:00-21:00 IST")
    [result] = message.artifact
    assert len(result.hourly) == 4
    assert '"dt"' not in message.content


async def test_get_current_weather() -> None:
    message = await get_current_weather.ainvoke(as_call(get_current_weather, location=JAIPUR))
    assert "CURRENT CONDITIONS" in message.content
    assert message.artifact.granularity == "current"


def test_describe_args_is_readable() -> None:
    from datetime import datetime

    window = TimeWindow(start=datetime(2026, 10, 19, 17, tzinfo=IST), end=datetime(2026, 10, 19, 21, tzinfo=IST))
    assert describe_args({"location": JAIPUR, "windows": [window], "time_expr": "evening"}) == {
        "location": "Jaipur", "windows": ["Mon 19 Oct 17:00-Mon 21:00"], "time_expr": "evening",
    }
