"""Test doubles shared by the agent tests: a fake structured-output LLM and real-fixture candidates."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field

from backend.agent.extraction import Extraction
from backend.agent.schemas import QueryUnderstanding
from backend.services import geocoding
from backend.services.models import Location
from backend.services.weather import ONECALL_URL

FIXTURES = Path(__file__).parent / "fixtures"


def candidates(fixture: str) -> list[Location]:
    """Locations parsed from a real recorded Geoapify response."""
    data = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    return [geocoding._to_location(r) for r in data["results"]]


def ok(understanding: QueryUnderstanding) -> dict[str, Any]:
    """What with_structured_output(include_raw=True) returns on success."""
    return {"raw": AIMessage(content=understanding.model_dump_json()), "parsed": understanding, "parsing_error": None}


def invalid(text: str = '{"intent": "weather", "date_expr": "next_month"}') -> dict[str, Any]:
    """What it returns when the model's output doesn't fit the schema."""
    return {"raw": AIMessage(content=text), "parsed": None, "parsing_error": ValueError("schema mismatch")}


class FakeStructuredLLM:
    """Stands in for a chat model: replays queued structured-output results (or raises them)."""

    def __init__(self, *responses: dict[str, Any] | Exception) -> None:
        self.responses = list(responses)
        self.calls: list[list] = []
        self.schema = None

    def with_structured_output(self, schema: type, include_raw: bool = False) -> RunnableLambda:
        assert include_raw, "extraction relies on include_raw=True"
        self.schema = schema

        async def run(messages: list) -> dict[str, Any]:
            self.calls.append(messages)
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

        return RunnableLambda(run)


# --- answering-layer fakes ------------------------------------------------------

IST = ZoneInfo("Asia/Kolkata")


def onecall(name: str) -> dict:
    return json.loads((FIXTURES / f"openweather_onecall_{name}.json").read_text(encoding="utf-8"))


def fixture_now(name: str) -> datetime:
    """The moment a onecall fixture was recorded, as the agent's `now`."""
    return datetime.fromtimestamp(onecall(name)["current"]["dt"], IST)


ONECALL_BY_LAT = {"26.92": "jaipur", "19.06": "mumbai", "28.61": "delhi"}  # rounded lat -> fixture
# Recorded back to back with the air quality fixtures (Thu 22 Oct 2026, ~12:00 IST).
ONECALL_BY_LAT_AQ = {"28.61": "delhi_aq", "48.85": "paris_aq"}
AIR_BY_LAT = {"28.61": "delhi", "48.85": "paris"}


def mock_onecall(respx_mock, default: str = "jaipur", by_lat: dict[str, str] | None = None):
    """Serve real recorded onecall responses, picked by the request's rounded latitude."""
    table = ONECALL_BY_LAT | (by_lat or {})

    def respond(request: httpx.Request) -> httpx.Response:
        name = table.get(request.url.params["lat"], default)
        return httpx.Response(200, json=onecall(name))

    return respx_mock.get(ONECALL_URL).mock(side_effect=respond)


def air(kind: str, name: str) -> dict:
    return json.loads((FIXTURES / f"openweather_air_{kind}_{name}.json").read_text(encoding="utf-8"))


def mock_air_quality(respx_mock) -> None:
    """Serve recorded air quality for Delhi and Paris. Other places have no recording: HTTP 503."""
    from backend.services.air_quality import CURRENT_URL, FORECAST_URL, HISTORY_URL

    def responder(kind: str):
        def respond(request: httpx.Request) -> httpx.Response:
            name = AIR_BY_LAT.get(request.url.params["lat"])
            return httpx.Response(200, json=air(kind, name)) if name else httpx.Response(503)
        return respond

    for url, kind in ((CURRENT_URL, "current"), (FORECAST_URL, "forecast"), (HISTORY_URL, "history")):
        respx_mock.get(url).mock(side_effect=responder(kind))


def aq_fixture_now(name: str = "delhi_aq") -> datetime:
    """When the air quality fixtures were recorded, in IST."""
    return fixture_now(name)


SEARCH_FIXTURES = {
    "jaipur": "geoapify_search_jaipur.json",
    "aurangabad": "geoapify_search_aurangabad.json",
    "bandra": "geoapify_search_bandra.json",
    "mumbai": "geoapify_search_bandra.json",  # first result is Bandra West, Mumbai
    "delhi": "geoapify_search_delhi_city.json",
    "paris": "geoapify_search_paris_city.json",
}


def install_fake_geocoding(monkeypatch) -> None:
    """geocode/reverse_geocode answered from real recorded Geoapify responses, no network."""
    from backend.services.errors import LocationNotFoundError

    async def geocode(query: str, limit: int = 5, near=None, country=None) -> list[Location]:
        name = query.strip().lower()
        if name not in SEARCH_FIXTURES:
            raise LocationNotFoundError(f"No place found matching {query!r}.")
        return candidates(SEARCH_FIXTURES[name])

    async def reverse_geocode(lat: float, lon: float) -> Location:
        data = json.loads((FIXTURES / "geoapify_reverse_jaipur.json").read_text(encoding="utf-8"))
        place = geocoding._to_location(data["results"][0], area_name=False)
        return place.model_copy(update={"lat": lat, "lon": lon})

    monkeypatch.setattr(geocoding, "geocode", geocode)
    monkeypatch.setattr(geocoding, "reverse_geocode", reverse_geocode)


class RecordingChatModel(BaseChatModel):
    """A fake chat model: replies with queued text (or raises queued errors) and records prompts."""

    replies: list = Field(default_factory=list)
    calls: list = Field(default_factory=list)

    @property
    def _llm_type(self) -> str:
        return "recording-fake"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls.append(messages)
        reply = self.replies.pop(0) if self.replies else "OK."
        if isinstance(reply, Exception):
            raise reply
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=reply))])


class QueuedExtractor:
    """Stands in for extract(): returns queued understandings and records what it was given."""

    def __init__(self, *items: QueryUnderstanding | Exception) -> None:
        self.queue = list(items)
        self.calls: list[dict] = []

    async def __call__(self, message, ctx, previous=None, pending_question=None) -> Extraction:
        self.calls.append({"message": message, "previous": previous, "pending_question": pending_question})
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return Extraction(item, raw=item.model_dump_json(), attempts=1, seconds=0.0)
