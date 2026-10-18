import json
import logging
from pathlib import Path

import httpx
import pytest

from backend.services import geocoding
from backend.services.errors import (
    InvalidInputError,
    LocationNotFoundError,
    UpstreamAPIError,
    WeatherWiseError,
)
from backend.services.geocoding import (
    REVERSE_URL,
    SEARCH_URL,
    WORLD_BIAS,
    geocode,
    pick_best,
    reverse_geocode,
)

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_KEY = "test-key-do-not-leak"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def ok(name: str) -> httpx.Response:
    return httpx.Response(200, json=fixture(name))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fresh cache and a fake API key for every test."""
    geocoding.cache.clear()
    monkeypatch.setattr(geocoding, "_api_key", lambda: FAKE_KEY)


async def candidates(respx_mock, name: str) -> list:
    respx_mock.get(SEARCH_URL).mock(return_value=ok(name))
    return await geocode("anything")


# --- parsing ---------------------------------------------------------------


async def test_geocode_parses_jaipur(respx_mock) -> None:
    route = respx_mock.get(SEARCH_URL).mock(return_value=ok("geoapify_search_jaipur.json"))
    [jaipur] = await geocode("Jaipur")

    assert (jaipur.name, jaipur.city) == ("Jaipur", "Jaipur")
    assert jaipur.state == "Rajasthan"
    assert (jaipur.country, jaipur.country_code) == ("India", "in")
    assert jaipur.timezone == "Asia/Kolkata"
    assert jaipur.result_type == "city"
    assert jaipur.confidence == 1
    assert jaipur.lat == pytest.approx(26.92, abs=0.05)
    assert jaipur.lon == pytest.approx(75.79, abs=0.05)
    assert "Jaipur" in jaipur.formatted

    params = route.calls.last.request.url.params
    assert (params["text"], params["format"], params["lang"], params["limit"]) == ("Jaipur", "json", "en", "5")
    assert "type" not in params


async def test_geocode_returns_all_matches_in_api_order(respx_mock) -> None:
    results = await candidates(respx_mock, "geoapify_search_aurangabad.json")
    assert [r.state for r in results] == ["Maharashtra", "West Bengal", "Bihar", "Uttar Pradesh", "Haryana"]


async def test_area_results_use_their_own_name_with_parent_city(respx_mock) -> None:
    bandra_west = (await candidates(respx_mock, "geoapify_search_bandra.json"))[0]
    assert bandra_west.result_type == "suburb"
    assert (bandra_west.name, bandra_west.city) == ("Bandra West", "Mumbai")
    assert bandra_west.formatted.startswith("Bandra West")


async def test_city_results_are_named_after_the_city(respx_mock) -> None:
    sambhajinagar = (await candidates(respx_mock, "geoapify_search_aurangabad.json"))[0]
    assert sambhajinagar.result_type == "city"
    assert sambhajinagar.name == sambhajinagar.city == "Chhatrapati Sambhajinagar"


async def test_missing_timezone_falls_back_to_timezonefinder(respx_mock) -> None:
    data = fixture("geoapify_search_jaipur.json")
    del data["results"][0]["timezone"]
    respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(200, json=data))
    [jaipur] = await geocode("Jaipur")
    assert jaipur.timezone == "Asia/Kolkata"


# --- bias ------------------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs, bias",
    [
        ({}, "countrycode:none"),
        ({"country": "IN"}, "countrycode:in"),
        ({"near": (26.91, 75.79)}, "proximity:75.79,26.91"),
        ({"near": (51.5, -0.12), "country": "in"}, "proximity:-0.12,51.5"),
    ],
)
async def test_bias_is_always_sent(respx_mock, kwargs: dict, bias: str) -> None:
    route = respx_mock.get(SEARCH_URL).mock(return_value=ok("geoapify_search_jaipur.json"))
    await geocode("Jaipur", **kwargs)
    assert route.calls.last.request.url.params["bias"] == bias


async def test_empty_unbiased_search_retries_with_world_bias(respx_mock) -> None:
    def by_bias(request: httpx.Request) -> httpx.Response:
        bias = request.url.params["bias"]
        return ok("geoapify_search_paris_none.json" if bias == "countrycode:none" else "geoapify_search_paris_world.json")

    route = respx_mock.get(SEARCH_URL).mock(side_effect=by_bias)
    results = await geocode("Paris")

    assert [c.request.url.params["bias"] for c in route.calls] == ["countrycode:none", WORLD_BIAS]
    assert results[0].country_code == "fr"


async def test_country_bias_does_not_retry(respx_mock) -> None:
    route = respx_mock.get(SEARCH_URL).mock(return_value=ok("geoapify_search_empty.json"))
    with pytest.raises(LocationNotFoundError):
        await geocode("Paris", country="in")
    assert route.call_count == 1


@pytest.mark.parametrize("country", ["india", "i", "1n"])
async def test_invalid_country_code_rejected(country: str) -> None:
    with pytest.raises(InvalidInputError, match="alpha-2"):
        await geocode("Jaipur", country=country)


# --- not found / invalid input ---------------------------------------------


async def test_no_results_raises_location_not_found(respx_mock) -> None:
    route = respx_mock.get(SEARCH_URL).mock(return_value=ok("geoapify_search_empty.json"))
    with pytest.raises(LocationNotFoundError, match="qxzvwplmkjhgf"):
        await geocode("qxzvwplmkjhgf")
    assert route.call_count == 2  # unbiased search, then the world-bias retry


def test_invalid_input_error_is_both_weatherwise_and_value_error() -> None:
    assert issubclass(InvalidInputError, WeatherWiseError)
    assert issubclass(InvalidInputError, ValueError)


async def test_invalid_limit_rejected(respx_mock) -> None:
    with pytest.raises(InvalidInputError, match="limit"):
        await geocode("Jaipur", limit=0)
    assert not respx_mock.calls


@pytest.mark.parametrize("query", ["", "   ", "\t\n"])
async def test_empty_query_rejected(respx_mock, query: str) -> None:
    with pytest.raises(InvalidInputError, match="empty"):
        await geocode(query)
    assert not respx_mock.calls


@pytest.mark.parametrize("lat, lon", [(90.1, 0), (-91, 0), (0, 180.5), (0, -181)])
async def test_invalid_coordinates_rejected(respx_mock, lat: float, lon: float) -> None:
    with pytest.raises(InvalidInputError, match="between"):
        await reverse_geocode(lat, lon)
    with pytest.raises(InvalidInputError, match="between"):
        await geocode("Jaipur", near=(lat, lon))
    assert not respx_mock.calls


# --- upstream errors and retry ---------------------------------------------


@pytest.mark.parametrize(
    "status, message, calls",
    [
        (401, "Geoapify rejected the API key (HTTP 401)", 1),
        (403, "Geoapify rejected the API key (HTTP 403)", 1),
        (429, "Geoapify rate limit exceeded", 1),
        (500, "Geoapify request failed (HTTP 500)", 2),
    ],
)
async def test_upstream_errors_have_distinct_messages(
    respx_mock, caplog: pytest.LogCaptureFixture, status: int, message: str, calls: int
) -> None:
    caplog.set_level(logging.DEBUG)
    route = respx_mock.get(SEARCH_URL).mock(return_value=httpx.Response(status, json={"message": "nope"}))

    with pytest.raises(UpstreamAPIError) as exc_info:
        await geocode("Jaipur")

    assert message in str(exc_info.value)
    assert route.call_count == calls
    assert FAKE_KEY not in str(exc_info.value)
    assert FAKE_KEY not in caplog.text


async def test_single_retry_on_timeout(respx_mock) -> None:
    route = respx_mock.get(SEARCH_URL).mock(
        side_effect=[httpx.ReadTimeout("slow"), ok("geoapify_search_jaipur.json")]
    )
    [jaipur] = await geocode("Jaipur")
    assert jaipur.name == "Jaipur"
    assert route.call_count == 2


async def test_second_timeout_raises(respx_mock) -> None:
    route = respx_mock.get(SEARCH_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(UpstreamAPIError, match="Geoapify request timed out after 2 attempts") as exc_info:
        await geocode("Jaipur")
    assert route.call_count == 2
    assert exc_info.value.__suppress_context__  # httpx's exception (with the URL) is not chained


async def test_single_retry_on_5xx(respx_mock) -> None:
    route = respx_mock.get(SEARCH_URL).mock(side_effect=[httpx.Response(503), ok("geoapify_search_jaipur.json")])
    await geocode("Jaipur")
    assert route.call_count == 2


# --- cache -----------------------------------------------------------------


async def test_cache_hit_avoids_second_call(respx_mock) -> None:
    route = respx_mock.get(SEARCH_URL).mock(return_value=ok("geoapify_search_jaipur.json"))
    first = await geocode("Jaipur")
    second = await geocode("  JAIPUR ")
    assert route.call_count == 1
    assert first == second


async def test_cache_key_differs_by_bias(respx_mock) -> None:
    route = respx_mock.get(SEARCH_URL).mock(return_value=ok("geoapify_search_jaipur.json"))
    await geocode("Jaipur", near=(26.91, 75.79))
    await geocode("Jaipur", near=(51.5, -0.12))
    await geocode("Jaipur", near=(26.91, 75.79))
    assert route.call_count == 2


async def test_reverse_cache_rounds_coordinates(respx_mock) -> None:
    route = respx_mock.get(REVERSE_URL).mock(return_value=ok("geoapify_reverse_jaipur.json"))
    await reverse_geocode(26.91, 75.79)
    await reverse_geocode(26.9101, 75.7899)
    await reverse_geocode(26.92, 75.79)
    assert route.call_count == 2


# --- reverse geocoding -----------------------------------------------------


async def test_reverse_geocode_jaipur(respx_mock) -> None:
    route = respx_mock.get(REVERSE_URL).mock(return_value=ok("geoapify_reverse_jaipur.json"))
    loc = await reverse_geocode(26.91, 75.79)

    # The top result is a hospital (an amenity); a reverse lookup still names the city.
    assert fixture("geoapify_reverse_jaipur.json")["results"][0]["result_type"] == "amenity"
    assert (loc.name, loc.city) == ("Jaipur", "Jaipur")
    assert (loc.state, loc.country_code, loc.timezone) == ("Rajasthan", "in", "Asia/Kolkata")
    assert (loc.lat, loc.lon) == (26.91, 75.79)  # the caller's point, not the nearest building
    params = route.calls.last.request.url.params
    assert (params["lat"], params["lon"], params["format"], params["lang"]) == ("26.91", "75.79", "json", "en")


@pytest.mark.parametrize("fixture_name", ["geoapify_reverse_ocean.json", "geoapify_search_empty.json"])
async def test_reverse_falls_back_when_nothing_found(respx_mock, fixture_name: str) -> None:
    respx_mock.get(REVERSE_URL).mock(return_value=ok(fixture_name))
    loc = await reverse_geocode(-40.0, -130.0)

    assert loc.name == "Current location"
    assert (loc.lat, loc.lon) == (-40.0, -130.0)
    assert loc.country_code is None and loc.city is None
    assert loc.timezone == "Etc/GMT+9"  # from timezonefinder, not the distant ocean feature


# --- pick_best -------------------------------------------------------------


async def test_pick_best_jaipur_not_ambiguous(respx_mock) -> None:
    best, ambiguous = pick_best(await candidates(respx_mock, "geoapify_search_jaipur.json"))
    assert (best.name, best.state) == ("Jaipur", "Rajasthan")
    assert not ambiguous


async def test_pick_best_aurangabad_ambiguous(respx_mock) -> None:
    best, ambiguous = pick_best(await candidates(respx_mock, "geoapify_search_aurangabad.json"))
    assert best.state == "Maharashtra"
    assert ambiguous


async def test_pick_best_hyderabad_prefers_india(respx_mock) -> None:
    best, ambiguous = pick_best(await candidates(respx_mock, "geoapify_search_hyderabad.json"))
    assert (best.name, best.state, best.country_code) == ("Hyderabad", "Telangana", "in")
    assert not ambiguous


async def test_pick_best_hyderabad_without_country_preference_is_ambiguous(respx_mock) -> None:
    best, ambiguous = pick_best(await candidates(respx_mock, "geoapify_search_hyderabad.json"), preferred_country="fr")
    assert best.country_code == "in"
    assert ambiguous


async def test_pick_best_bandra_is_mumbai_suburb(respx_mock) -> None:
    best, ambiguous = pick_best(await candidates(respx_mock, "geoapify_search_bandra.json"))
    assert best.result_type == "suburb"
    assert (best.name, best.city, best.state) == ("Bandra West", "Mumbai", "Maharashtra")
    assert best.formatted.startswith("Bandra West")
    assert not ambiguous


async def test_pick_best_paris_is_france(respx_mock) -> None:
    respx_mock.get(SEARCH_URL).mock(return_value=ok("geoapify_search_paris_world.json"))
    best, ambiguous = pick_best(await geocode("Paris", country="fr"))
    assert (best.country_code, best.result_type, best.timezone) == ("fr", "city", "Europe/Paris")
    assert not ambiguous


async def test_pick_best_ignores_near_duplicates(respx_mock) -> None:
    paris_city, paris_suburb, _texas = await candidates(respx_mock, "geoapify_search_paris_world.json")
    moved = paris_suburb.model_copy(update={"state": "Elsewhere"})  # different state, but < 5 km away
    assert pick_best([paris_city, moved]) == (paris_city, False)


def test_pick_best_requires_candidates() -> None:
    with pytest.raises(LocationNotFoundError):
        pick_best([])
