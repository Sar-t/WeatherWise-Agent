from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from backend.agent.extraction import Extraction
from backend.agent.memory import Conversation, match_option, merge
from backend.agent.schemas import QueryUnderstanding, UserContext
from backend.tests.agent_fakes import candidates
from backend.tests.test_resolution import SEARCHES, FakeServices

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 18, 13, tzinfo=ZoneInfo("Asia/Kolkata"))
CTX = UserContext(lat=26.91, lon=75.79, now=NOW)
AURANGABAD = candidates("geoapify_search_aurangabad.json")


class FakeExtractor:
    """Replays queued understandings and records what the conversation passed in."""

    def __init__(self, *understandings: QueryUnderstanding) -> None:
        self.queue = list(understandings)
        self.calls: list[dict] = []

    async def __call__(self, message, ctx, previous=None, pending_question=None) -> Extraction:
        self.calls.append({"message": message, "previous": previous, "pending_question": pending_question})
        return Extraction(self.queue.pop(0), raw=None, attempts=1, seconds=0.0)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch) -> FakeServices:
    return FakeServices(monkeypatch)


def weather(**slots) -> QueryUnderstanding:
    return QueryUnderstanding(**({"intent": "weather"} | slots))


# --- merge -----------------------------------------------------------------


def test_merge_overrides_only_new_non_null_slots() -> None:
    old = weather(locations=["Jaipur"], date_expr="today", time_expr="evening", activity="running")
    new = weather(date_expr="tomorrow", is_follow_up=True)
    merged = merge(old, new)
    assert (merged.locations, merged.date_expr, merged.time_expr, merged.activity) == (
        ["Jaipur"], "tomorrow", "evening", "running",
    )
    assert merged.is_follow_up


def test_merge_keeps_and_overrides_duration() -> None:
    old = QueryUnderstanding(intent="travel_recommendation", locations=["Pune"], duration_hours=3)
    assert merge(old, weather(date_expr="tomorrow", is_follow_up=True)).duration_hours == 3
    assert merge(old, weather(duration_hours=5, is_follow_up=True)).duration_hours == 5


def test_merge_replaces_locations() -> None:
    merged = merge(weather(locations=["Jaipur"], date_expr="tomorrow"), weather(locations=["Delhi"], is_follow_up=True))
    assert (merged.locations, merged.date_expr) == (["Delhi"], "tomorrow")


def test_merge_takes_new_clarification_flags() -> None:
    merged = merge(weather(locations=["Goa"]), weather(needs_clarification=True, clarification_question="Which day?"))
    assert merged.needs_clarification and merged.clarification_question == "Which day?"


# --- follow-ups ------------------------------------------------------------


async def test_what_about_tomorrow(services: FakeServices) -> None:
    extractor = FakeExtractor(
        weather(locations=["Jaipur"], date_expr="today", time_expr="evening"),
        weather(date_expr="tomorrow", is_follow_up=True),
    )
    conversation = Conversation(CTX, extractor)
    await conversation.handle("Jaipur this evening?")
    result = await conversation.handle("what about tomorrow?")

    assert extractor.calls[1]["previous"].locations == ["Jaipur"]
    assert (result.understanding.locations, result.understanding.date_expr, result.understanding.time_expr) == (
        ["Jaipur"], "tomorrow", "evening",
    )
    assert services.resolve_calls[-1] == ("tomorrow", "evening", "Asia/Kolkata")


async def test_and_delhi(services: FakeServices) -> None:
    extractor = FakeExtractor(
        weather(locations=["Jaipur"], date_expr="tomorrow", time_expr="morning"),
        weather(locations=["Delhi"], is_follow_up=True),
    )
    conversation = Conversation(CTX, extractor)
    await conversation.handle("Jaipur tomorrow morning")
    result = await conversation.handle("and Delhi?")
    assert result.understanding.locations == ["Delhi"]
    assert (result.date_expr, result.time_expr) == ("tomorrow", "morning")


async def test_new_question_does_not_inherit_old_slots(services: FakeServices) -> None:
    extractor = FakeExtractor(
        QueryUnderstanding(intent="activity_recommendation", activity="running", locations=["Jaipur"]),
        weather(locations=["Mumbai"]),
    )
    conversation = Conversation(CTX, extractor)
    await conversation.handle("good time to run in Jaipur?")
    result = await conversation.handle("weather in Mumbai")
    assert (result.understanding.intent, result.understanding.activity) == ("weather", None)


@pytest.mark.parametrize("intent, flagged", [
    ("air_quality", True), ("activity_recommendation", True), ("travel_recommendation", True),
    ("weather", None), ("clothing_recommendation", None), ("comparison", None),
])
async def test_code_turns_on_air_quality_for_some_intents(services: FakeServices, intent: str,
                                                          flagged: bool | None) -> None:
    conversation = Conversation(CTX, FakeExtractor(QueryUnderstanding(intent=intent, locations=["Jaipur"])))
    result = await conversation.handle("q")
    assert result.understanding.include_air_quality is flagged


async def test_llm_air_quality_flag_is_kept_and_carries_into_follow_ups(services: FakeServices) -> None:
    extractor = FakeExtractor(weather(locations=["Jaipur"], include_air_quality=True),
                              weather(locations=["Delhi"], is_follow_up=True),
                              weather(locations=["Mumbai"]))
    conversation = Conversation(CTX, extractor)
    assert (await conversation.handle("Jaipur weather and smog?")).understanding.include_air_quality
    assert (await conversation.handle("and Delhi?")).understanding.include_air_quality  # follow-up keeps it
    assert (await conversation.handle("Mumbai weather")).understanding.include_air_quality is None  # new query


async def test_garbled_message_keeps_previous_query(services: FakeServices) -> None:
    extractor = FakeExtractor(weather(locations=["Jaipur"]), QueryUnderstanding(intent="unclear"),
                              weather(date_expr="tomorrow", is_follow_up=True))
    conversation = Conversation(CTX, extractor)
    await conversation.handle("Jaipur weather")
    assert (await conversation.handle("asdf qwerty")).clarification.reason == "unclear"
    result = await conversation.handle("tomorrow?")
    assert result.understanding.locations == ["Jaipur"]


# --- answering clarifications ----------------------------------------------


async def test_picking_a_city_by_state_applies_to_pending_query(services: FakeServices) -> None:
    extractor = FakeExtractor(weather(locations=["Aurangabad"], date_expr="tomorrow", time_expr="evening"))
    conversation = Conversation(CTX, extractor)
    first = await conversation.handle("Aurangabad tomorrow evening")
    assert first.clarification.reason == "ambiguous_location"

    result = await conversation.handle("Bihar")
    assert len(extractor.calls) == 1  # answered without another LLM call
    assert result.status == "ready"
    assert result.targets[0].location.state == "Bihar"
    assert (result.date_expr, result.time_expr) == ("tomorrow", "evening")


async def test_picking_a_city_by_number(services: FakeServices) -> None:
    conversation = Conversation(CTX, FakeExtractor(weather(locations=["Aurangabad"])))
    await conversation.handle("Aurangabad weather")
    result = await conversation.handle("1")
    assert result.targets[0].location.state == "Maharashtra"


async def test_choice_is_kept_for_follow_ups_and_dropped_for_new_queries(services: FakeServices) -> None:
    extractor = FakeExtractor(
        weather(locations=["Aurangabad"]),
        weather(date_expr="tomorrow", is_follow_up=True),
        weather(locations=["Aurangabad"]),
    )
    conversation = Conversation(CTX, extractor)
    await conversation.handle("Aurangabad weather")
    await conversation.handle("West Bengal")
    follow_up = await conversation.handle("and tomorrow?")
    assert follow_up.targets[0].location.state == "West Bengal"

    fresh = await conversation.handle("Aurangabad weather")
    assert fresh.status == "clarify"


async def test_unmatched_reply_goes_to_the_llm_with_the_question(services: FakeServices) -> None:
    extractor = FakeExtractor(
        weather(locations=["Aurangabad"]),
        weather(locations=["Jaipur"], is_follow_up=True),
    )
    conversation = Conversation(CTX, extractor)
    asked = await conversation.handle("Aurangabad weather")
    result = await conversation.handle("actually, Jaipur")
    assert extractor.calls[1]["pending_question"] == asked.clarification.question
    assert result.targets[0].location.name == "Jaipur"


async def test_answering_which_city(services: FakeServices) -> None:
    extractor = FakeExtractor(
        weather(date_expr="tomorrow", time_expr="morning"),
        weather(locations=["Jaipur"], is_follow_up=True),
    )
    conversation = Conversation(UserContext(now=NOW), extractor)  # no device location
    asked = await conversation.handle("will it rain tomorrow morning?")
    assert asked.clarification.question == "Which city?"

    result = await conversation.handle("Jaipur")
    assert extractor.calls[1]["pending_question"] == "Which city?"
    assert result.status == "ready"
    assert (result.targets[0].location.name, result.date_expr, result.time_expr) == ("Jaipur", "tomorrow", "morning")


@pytest.mark.parametrize(
    "reply, state",
    [
        ("Bihar", "Bihar"),
        ("Aurangabad, Bihar", "Bihar"),
        ("the one in west bengal", "West Bengal"),
        ("Sambhajinagar", "Maharashtra"),
        ("2", "West Bengal"),
        ("third", "Bihar"),
        ("Aurangabad", None),  # shared by every option
        ("5", None),
        ("Delhi", None),
    ],
)
def test_match_option(reply: str, state: str | None) -> None:
    options = AURANGABAD[:3]  # Maharashtra, West Bengal, Bihar
    match = match_option(reply, options, typed="Aurangabad")
    assert (match.state if match else None) == state
