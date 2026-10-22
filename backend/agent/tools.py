"""The weather services as LangChain tools.

Each returns (content, artifact): content is a compact text observation for an LLM, the artifact
is the structured result for code (cards). In this step code calls them; in step 3 an LLM will.
"""

from datetime import datetime
from typing import Annotated

from langchain_core.tools import InjectedToolArg, tool

from backend.agent.observations import place_name, summarize
from backend.agent.resolution import option_label
from backend.services import geocoding, weather
from backend.services.models import Location, TimeWindow, WeatherResult


@tool("geocode_place", response_format="content_and_artifact")
async def geocode_place(query: str) -> tuple[str, list[Location]]:
    """Find places matching a name such as "Jaipur", "Bandra" or "Paris, Texas".

    Returns up to 5 candidates with state/country, coordinates and timezone.
    """
    candidates = await geocoding.geocode(query)
    lines = [
        f"{i}. {option_label(c, query)} ({c.result_type}; {c.lat:.3f}, {c.lon:.3f}; {c.timezone})"
        for i, c in enumerate(candidates, 1)
    ]
    return "\n".join(lines), candidates


@tool("get_current_weather", response_format="content_and_artifact")
async def get_current_weather(location: Location) -> tuple[str, WeatherResult]:
    """Current observed conditions at a location returned by geocode_place."""
    result = await weather.get_current(location)
    return summarize(result), result


@tool("get_weather_for_windows", response_format="content_and_artifact")
async def get_weather_for_windows(
    location: Location,
    windows: list[TimeWindow],
    time_expr: str | None = None,
    now: Annotated[datetime | None, InjectedToolArg] = None,
) -> tuple[str, list[WeatherResult]]:
    """Weather for one location over one or more time windows (forecast up to 8 days, or past).

    `time_expr` (e.g. "evening") picks the part-of-day temperature shown for daily forecasts.
    All windows share one forecast request.
    """
    results = await weather.get_weather_multi(location, windows, now=now)
    return "\n\n".join(summarize(r, time_expr) for r in results), results


def describe_args(args: dict) -> dict:
    """Short, readable tool arguments for debug output."""
    out = {}
    for key, value in args.items():
        if value is None:
            continue
        if isinstance(value, Location):
            out[key] = place_name(value)
        elif isinstance(value, list) and value and isinstance(value[0], TimeWindow):
            out[key] = [f"{w.start:%a %d %b %H:%M}-{w.end:%a %H:%M}" for w in value]
        elif isinstance(value, datetime):
            out[key] = value.isoformat(timespec="minutes")
        else:
            out[key] = value
    return out
