from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.agent.resolution import SUPPORTED_DATES_HINT, option_label, resolve_query
from backend.agent.schemas import QueryUnderstanding, UserContext
from backend.services import date_resolver, geocoding
from backend.services.errors import LocationNotFoundError, UnrecognizedDateError, UpstreamAPIError
from backend.services.models import Location, TimeWindow
from backend.tests.agent_fakes import candidates

pytestmark = pytest.mark.anyio

IST = ZoneInfo("Asia/Kolkata")
NOW = datetime(2026, 10, 18, 13, tzinfo=IST)
HERE = UserContext(lat=26.91, lon=75.79, now=NOW)  # device in Jaipur
NOWHERE = UserContext(now=NOW)  # no device location

SEARCHES = {
    "jaipur": candidates("geoapify_search_jaipur.json"),
    "aurangabad": candidates("geoapify_search_aurangabad.json"),
    "hyderabad": candidates("geoapify_search_hyderabad.json"),
    "bandra": candidates("geoapify_search_bandra.json"),
    "paris": candidates("geoapify_search_paris_world.json"),
    "delhi": candidates("geoapify_search_jaipur.json"),  # any unambiguous Indian city will do
    "mumbai": candidates("geoapify_search_bandra.json")[:1],
}
CURRENT = Location(name="Jaipur", city="Jaipur", lat=26.91, lon=75.79, country="India", country_code="in",
                   state="Rajasthan", formatted="Jaipur, RJ, India", timezone="Asia/Kolkata")


def q(**slots) -> QueryUnderstanding:
    return QueryUnderstanding(**({"intent": "weather"} | slots))


class FakeServices:
    """Records calls to the geocoding and date-resolver functions resolution uses."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.geocode_calls: list[tuple[str, tuple | None]] = []
        self.reverse_calls: list[tuple[float, float]] = []
        self.resolve_calls: list[tuple[str, str, str]] = []
        self.date_error: Exception | None = None
        monkeypatch.setattr(geocoding, "geocode", self.geocode)
        monkeypatch.setattr(geocoding, "reverse_geocode", self.reverse_geocode)
        monkeypatch.setattr(date_resolver, "resolve", self.resolve)

    async def geocode(self, query: str, limit: int = 5, near=None, country=None) -> list[Location]:
        self.geocode_calls.append((query, near))
        if query.lower() not in SEARCHES:
            raise LocationNotFoundError(f"No place found matching {query!r}.")
        return SEARCHES[query.lower()]

    async def reverse_geocode(self, lat: float, lon: float) -> Location:
        self.reverse_calls.append((lat, lon))
        return CURRENT

    def resolve(self, date_expr: str, time_expr: str, tz: str, now=None) -> list[TimeWindow]:
        self.resolve_calls.append((date_expr, time_expr, tz))
        if self.date_error:
            raise self.date_error
        zone = ZoneInfo(tz)
        return [TimeWindow(start=datetime(2026, 10, 19, 6, tzinfo=zone), end=datetime(2026, 10, 19, 12, tzinfo=zone))]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> FakeServices:
    return FakeServices(monkeypatch)


# --- locations -------------------------------------------------------------


async def test_no_location_named_uses_device_location(services: FakeServices) -> None:
    result = await resolve_query(q(date_expr="tomorrow", time_expr="morning"), HERE)

    assert result.status == "ready"
    assert services.reverse_calls == [(26.91, 75.79)]
    [target] = result.targets
    assert (target.query, target.location) == ("", CURRENT)
    assert services.resolve_calls == [("tomorrow", "morning", "Asia/Kolkata")]


async def test_no_location_at_all_asks_which_city(services: FakeServices) -> None:
    result = await resolve_query(q(date_expr="tomorrow"), NOWHERE)
    assert result.status == "clarify"
    assert (result.clarification.reason, result.clarification.question) == ("no_location", "Which city?")
    assert services.reverse_calls == [] and services.geocode_calls == []


async def test_named_city_is_searched_without_proximity_bias(services: FakeServices) -> None:
    # Geoapify's proximity bias returns only the nearest same-name place, hiding ambiguity.
    result = await resolve_query(q(locations=["Jaipur"]), HERE)
    assert result.status == "ready"
    assert services.geocode_calls == [("Jaipur", None)]
    assert result.targets[0].location.state == "Rajasthan"


async def test_ambiguous_city_far_from_user_still_asks(services: FakeServices) -> None:
    result = await resolve_query(q(locations=["Aurangabad"]), HERE)  # nearest one (Haryana) is ~380 km away
    assert result.clarification.reason == "ambiguous_location"


async def test_local_match_wins_when_user_is_nearby(services: FakeServices) -> None:
    near_bihar = UserContext(lat=24.70, lon=84.40, now=NOW)  # a few km from Aurangabad, Bihar
    result = await resolve_query(q(locations=["Aurangabad"]), near_bihar)
    assert result.status == "ready"
    assert result.targets[0].location.state == "Bihar"


async def test_bandra_from_jaipur_is_still_mumbai(services: FakeServices) -> None:
    # Bandra, Rajasthan is ~150 km from the user, so it is not "local".
    result = await resolve_query(q(locations=["Bandra"]), HERE)
    assert (result.targets[0].location.name, result.targets[0].location.city) == ("Bandra West", "Mumbai")


async def test_ambiguous_city_lists_options_with_state(services: FakeServices) -> None:
    result = await resolve_query(q(locations=["Aurangabad"], date_expr="tomorrow"), HERE)

    assert result.status == "clarify"
    c = result.clarification
    assert (c.reason, c.location_query) == ("ambiguous_location", "Aurangabad")
    assert c.question == (
        "Did you mean Chhatrapati Sambhajinagar (Aurangabad), Maharashtra, Aurangabad, West Bengal "
        "or Aurangabad, Bihar?"
    )
    assert [o.state for o in c.options] == ["Maharashtra", "West Bengal", "Bihar"]
    assert services.resolve_calls == []


async def test_preferred_country_settles_hyderabad(services: FakeServices) -> None:
    result = await resolve_query(q(locations=["Hyderabad"]), HERE)
    assert result.status == "ready"
    assert (result.targets[0].location.state, result.targets[0].location.country_code) == ("Telangana", "in")


async def test_unknown_city_asks_politely(services: FakeServices) -> None:
    result = await resolve_query(q(locations=["Atlantis"]), HERE)
    assert result.status == "clarify"
    assert result.clarification.reason == "location_not_found"
    assert result.clarification.question.startswith("I couldn't find 'Atlantis'.")


async def test_pinned_choice_skips_geocoding(services: FakeServices) -> None:
    bihar = SEARCHES["aurangabad"][2]
    result = await resolve_query(q(locations=["Aurangabad"]), HERE, pinned={"aurangabad": bihar})
    assert result.status == "ready" and result.targets[0].location == bihar
    assert services.geocode_calls == []


async def test_upstream_failures_still_raise(services: FakeServices, monkeypatch: pytest.MonkeyPatch) -> None:
    async def down(*args, **kwargs):
        raise UpstreamAPIError("Geoapify request failed (HTTP 500).", 500)

    monkeypatch.setattr(geocoding, "geocode", down)
    with pytest.raises(UpstreamAPIError):
        await resolve_query(q(locations=["Jaipur"]), HERE)


def test_option_label_adds_foreign_country() -> None:
    texas = SEARCHES["paris"][2]
    assert option_label(texas, "Paris") == "Paris, Texas, United States"
    assert option_label(SEARCHES["aurangabad"][0], "Aurangabad") == "Chhatrapati Sambhajinagar (Aurangabad), Maharashtra"


# --- dates and times -------------------------------------------------------


async def test_defaults_are_today_and_full_day(services: FakeServices) -> None:
    result = await resolve_query(q(locations=["Jaipur"]), HERE)
    assert (result.date_expr, result.time_expr) == ("today", "full_day")
    assert services.resolve_calls == [("today", "full_day", "Asia/Kolkata")]
    assert (result.understanding.date_expr, result.understanding.time_expr) == (None, None)  # slots stay null


async def test_dates_resolve_in_each_locations_timezone(services: FakeServices) -> None:
    result = await resolve_query(q(intent="comparison", locations=["Jaipur", "Paris"], date_expr="tomorrow"), HERE)
    assert [c[2] for c in services.resolve_calls] == ["Asia/Kolkata", "Europe/Paris"]
    assert [t.windows[0].start.tzinfo.key for t in result.targets] == ["Asia/Kolkata", "Europe/Paris"]


async def test_llm_flagged_unsupported_date_asks(services: FakeServices) -> None:
    understanding = q(locations=["Goa"], needs_clarification=True,
                      clarification_question="I can only look about a week ahead. Which day did you mean?")
    result = await resolve_query(understanding, HERE)
    assert result.status == "clarify"
    assert result.clarification.reason == "llm_clarification"
    assert result.clarification.question == understanding.clarification_question
    assert services.geocode_calls == []


async def test_resolver_rejecting_date_explains_what_is_supported(services: FakeServices) -> None:
    services.date_error = UnrecognizedDateError("Unrecognized date expression 'someday'.")
    result = await resolve_query(q(locations=["Jaipur"], date_expr="tomorrow"), HERE)
    assert result.status == "clarify"
    assert result.clarification.reason == "unsupported_date"
    assert result.clarification.question == SUPPORTED_DATES_HINT
    assert "next Monday" in SUPPORTED_DATES_HINT and "this weekend" in SUPPORTED_DATES_HINT


async def test_weekend_already_over_suggests_another_day(services: FakeServices) -> None:
    services.date_error = UnrecognizedDateError("No part of this weekend's requested time is left.")
    result = await resolve_query(q(locations=["Jaipur"], date_expr="this_weekend", time_expr="morning"), HERE)
    assert result.clarification.reason == "unsupported_date"
    assert "no morning left this weekend" in result.clarification.question


async def test_unsupported_time_asks_before_geocoding(services: FakeServices) -> None:
    result = await resolve_query(q(locations=["Jaipur"], time_expr="midday-ish"), HERE)
    assert result.status == "clarify"
    assert result.clarification.reason == "unsupported_time"
    assert services.geocode_calls == []


# --- comparison, scope -----------------------------------------------------


async def test_comparison_with_two_locations(services: FakeServices) -> None:
    result = await resolve_query(q(intent="comparison", locations=["Jaipur", "Mumbai"], date_expr="tomorrow"), HERE)
    assert result.status == "ready"
    assert [t.query for t in result.targets] == ["Jaipur", "Mumbai"]
    assert len(services.resolve_calls) == 2


async def test_comparison_with_four_locations_asks_to_narrow(services: FakeServices) -> None:
    result = await resolve_query(q(intent="comparison", locations=["Jaipur", "Mumbai", "Delhi", "Paris"]), HERE)
    assert result.status == "clarify"
    assert result.clarification.reason == "too_many_locations"
    assert "up to 3 places" in result.clarification.question
    assert services.geocode_calls == []


async def test_out_of_scope_is_not_resolved(services: FakeServices) -> None:
    result = await resolve_query(QueryUnderstanding(intent="out_of_scope"), HERE)
    assert (result.status, result.targets, result.clarification) == ("out_of_scope", [], None)
    assert services.geocode_calls == services.reverse_calls == services.resolve_calls == []


async def test_unclear_asks_to_rephrase(services: FakeServices) -> None:
    result = await resolve_query(QueryUnderstanding(intent="unclear"), HERE)
    assert result.status == "clarify" and result.clarification.reason == "unclear"
    assert "rephrase" in result.clarification.question
