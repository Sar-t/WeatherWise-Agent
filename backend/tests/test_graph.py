import asyncio

import httpx
import pytest
from langchain_core.exceptions import ModelRateLimitError

from backend.agent import graph as graph_module
from backend.agent import llm
from backend.agent.graph import (
    BUSY_TEXT,
    DECLINE_TEXT,
    OUT_OF_RANGE_TEXT,
    UPSTREAM_TEXT,
    Agent,
    build_graph,
)
from backend.agent.llm import LLMRateLimitError
from backend.agent.schemas import QueryUnderstanding, UserContext
from backend.services import weather
from backend.services.cache import InMemoryCache
from backend.services.weather import ONECALL_URL
from backend.tests.agent_fakes import (
    QueuedExtractor,
    RecordingChatModel,
    fixture_now,
    install_fake_geocoding,
    mock_onecall,
)

pytestmark = pytest.mark.anyio

JAIPUR_NOW = fixture_now("jaipur")  # Sun 18 Oct 2026 20:17 IST
CITIES_NOW = fixture_now("mumbai")  # Thu 22 Oct 2026 11:06 IST (Delhi recorded the same second)


def q(**slots) -> QueryUnderstanding:
    return QueryUnderstanding(**({"intent": "weather"} | slots))


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    install_fake_geocoding(monkeypatch)
    monkeypatch.setattr(weather, "cache", InMemoryCache())

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(llm, "_sleep", no_sleep)


def make_agent(*understandings, answer: RecordingChatModel | None = None, now=JAIPUR_NOW, **ctx) -> tuple:
    extractor = QueuedExtractor(*understandings)
    answer = answer or RecordingChatModel(replies=["Rain is unlikely; expect about 27°C."])
    graph = build_graph(extractor=extractor, answer_llm=answer)
    agent = Agent(UserContext(now=now, **ctx), graph=graph)
    return agent, extractor, answer


# --- happy paths -----------------------------------------------------------


async def test_simple_weather_query_end_to_end(respx_mock) -> None:
    route = mock_onecall(respx_mock)
    agent, extractor, answer = make_agent(q(locations=["Jaipur"], date_expr="tomorrow", time_expr="evening"))

    response = await agent.ask("Will it rain in Jaipur tomorrow evening?")

    assert (response.kind, response.text) == ("answer", "Rain is unlikely; expect about 27°C.")
    assert response.debug.nodes == ["understand", "resolve", "fetch", "answer"]
    assert [t.name for t in response.debug.tool_calls] == ["get_weather_for_windows"]
    assert response.debug.tool_calls[0].args["location"] == "Jaipur"
    assert response.debug.llm_calls == 2
    assert route.call_count == 1
    [card] = response.cards
    assert (card.granularity, len(card.hourly)) == ("hourly", 4)
    assert set(response.debug.timings) >= {"understand", "resolve", "fetch", "answer", "total"}


async def test_answer_prompt_gets_observations_not_raw_json(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, answer = make_agent(q(locations=["Jaipur"], date_expr="tomorrow", time_expr="evening"))
    await agent.ask("Will it rain in Jaipur tomorrow evening?")

    [messages] = answer.calls
    prompt = messages[-1].content
    assert prompt.startswith("Now (user's local time): Sun 18 Oct 2026, 20:17 IST")
    assert "Jaipur | FORECAST, hourly | Mon 19 Oct, 17:00-21:00 IST" in prompt
    assert "Summary of 4 hours" in prompt
    for raw_key in ('"dt"', "wind_speed", "feels_like", '"pop"', "{'", '{"'):
        assert raw_key not in prompt.split("Weather observations:")[1]
    assert "Use only numbers that appear in the observations" in messages[0].content


async def test_today_without_time_adds_current_conditions(respx_mock) -> None:
    route = mock_onecall(respx_mock)
    agent, _, answer = make_agent(q(locations=["Jaipur"]))  # no date, no time -> today, full day

    response = await agent.ask("Jaipur weather")

    assert sorted(t.name for t in response.debug.tool_calls) == ["get_current_weather", "get_weather_for_windows"]
    assert [c.granularity for c in response.cards] == ["current", "hourly"]
    assert route.call_count == 1  # current conditions reuse the same onecall
    assert "CURRENT CONDITIONS" in answer.calls[0][-1].content


async def test_today_with_time_has_no_current_conditions(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, _ = make_agent(q(locations=["Jaipur"], time_expr="night"))
    response = await agent.ask("Jaipur tonight")
    assert [t.name for t in response.debug.tool_calls] == ["get_weather_for_windows"]


async def test_current_location_fallback(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, _ = make_agent(q(date_expr="tomorrow"), lat=26.91, lon=75.79)
    response = await agent.ask("Will it rain tomorrow?")
    assert response.kind == "answer"
    assert response.cards[0].location.name == "Jaipur"


# --- clarification / decline -----------------------------------------------


async def test_ambiguous_city_asks_and_skips_fetch(respx_mock) -> None:
    route = mock_onecall(respx_mock)
    agent, _, answer = make_agent(q(locations=["Aurangabad"], date_expr="tomorrow"))

    response = await agent.ask("Aurangabad tomorrow?")

    assert response.kind == "clarification"
    assert response.text.startswith("Did you mean Chhatrapati Sambhajinagar (Aurangabad), Maharashtra")
    assert response.debug.nodes == ["understand", "resolve", "ask"]
    assert (response.debug.tool_calls, response.debug.llm_calls, response.cards) == ([], 1, [])
    assert route.call_count == 0 and answer.calls == []


async def test_out_of_scope_is_declined(respx_mock) -> None:
    agent, _, answer = make_agent(QueryUnderstanding(intent="out_of_scope"))
    response = await agent.ask("Write me a poem")
    assert (response.kind, response.text) == ("decline", DECLINE_TEXT)
    assert response.debug.nodes == ["understand", "resolve", "decline"]
    assert answer.calls == [] and not respx_mock.calls


# --- comparison ------------------------------------------------------------


async def test_comparison_fetches_cities_in_parallel_one_onecall_each(respx_mock) -> None:
    both_started = asyncio.Event()
    started: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        started.append(request.url.params["lat"])
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=2)  # sequential fetching would time out here
        from backend.tests.agent_fakes import ONECALL_BY_LAT, onecall
        return httpx.Response(200, json=onecall(ONECALL_BY_LAT[request.url.params["lat"]]))

    route = respx_mock.get(ONECALL_URL).mock(side_effect=respond)
    agent, _, answer = make_agent(
        q(intent="comparison", locations=["Delhi", "Mumbai"], date_expr="tomorrow"),
        answer=RecordingChatModel(replies=["Mumbai looks wetter than Delhi."]),
        now=CITIES_NOW,
    )

    response = await agent.ask("Compare Delhi and Mumbai tomorrow")

    assert response.kind == "answer", response.debug.error
    assert route.call_count == 2 and sorted(started) == ["19.06", "28.61"]
    assert [c.location.name for c in response.cards] == ["New Delhi", "Bandra West"]
    prompt = answer.calls[0][-1].content
    assert "New Delhi | FORECAST" in prompt and "Bandra West, Mumbai | FORECAST" in prompt


# --- follow-ups --------------------------------------------------------------


async def test_follow_up_across_turns_uses_thread_memory(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, extractor, _ = make_agent(
        q(locations=["Jaipur"], date_expr="tomorrow", time_expr="evening"),
        q(date_expr="day_after_tomorrow", is_follow_up=True),
    )
    await agent.ask("Jaipur tomorrow evening?")
    response = await agent.ask("what about the day after?")

    assert extractor.calls[1]["previous"].locations == ["Jaipur"]
    assert response.kind == "answer"
    [card] = response.cards
    assert card.location.name == "Jaipur"
    assert card.window.start.day == 20 and card.window.start.hour == 17  # day after tomorrow, evening kept


async def test_memory_is_per_thread(respx_mock) -> None:
    mock_onecall(respx_mock)
    extractor = QueuedExtractor(q(locations=["Jaipur"], date_expr="tomorrow"), q(date_expr="tomorrow"))
    graph = build_graph(extractor=extractor, answer_llm=RecordingChatModel())
    ctx = UserContext(now=JAIPUR_NOW)
    await Agent(ctx, graph=graph, thread_id="a").ask("Jaipur tomorrow")
    response = await Agent(ctx, graph=graph, thread_id="b").ask("tomorrow?")
    assert extractor.calls[1]["previous"] is None
    assert response.text == "Which city?"


async def test_same_thread_id_resumes_conversation(respx_mock) -> None:
    mock_onecall(respx_mock)
    extractor = QueuedExtractor(q(locations=["Aurangabad"]))
    graph = build_graph(extractor=extractor, answer_llm=RecordingChatModel())
    ctx = UserContext(now=JAIPUR_NOW)
    await Agent(ctx, graph=graph, thread_id="t1").ask("Aurangabad weather")
    response = await Agent(ctx, graph=graph, thread_id="t1").ask("Bihar")  # a new Agent object, same thread

    assert response.kind == "answer"
    assert response.cards[-1].location.state == "Bihar"
    assert len(extractor.calls) == 1  # the choice was matched without the LLM
    assert response.debug.llm_calls == 1  # just the answer


# --- errors ----------------------------------------------------------------


async def test_out_of_range_window_is_a_friendly_error(respx_mock) -> None:
    agent, _, answer = make_agent(q(locations=["Jaipur"], date_expr="next_sunday", time_expr="night"))
    response = await agent.ask("Jaipur next Sunday night")  # ends Mon 26 Oct 06:00, past the 8-day limit
    assert (response.kind, response.text) == ("error", OUT_OF_RANGE_TEXT)
    assert response.debug.error.startswith("OutOfRangeError")
    assert response.debug.nodes == ["understand", "resolve", "fetch"]
    assert answer.calls == []


async def test_upstream_failure_is_a_friendly_error(respx_mock) -> None:
    respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(500))
    agent, _, _ = make_agent(q(locations=["Jaipur"], date_expr="tomorrow"))
    response = await agent.ask("Jaipur tomorrow")
    assert (response.kind, response.text) == ("error", UPSTREAM_TEXT)
    assert "HTTP 500" in response.debug.error
    assert "Traceback" not in response.text


async def test_rate_limit_during_understanding_says_busy() -> None:
    agent, _, _ = make_agent(LLMRateLimitError("free-tier rate limit"))
    response = await agent.ask("Jaipur tomorrow")
    assert (response.kind, response.text) == ("error", BUSY_TEXT)
    assert response.debug.nodes == ["understand"]


async def test_unexpected_error_is_generic_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    agent, _, _ = make_agent(RuntimeError("boom"))
    response = await agent.ask("Jaipur tomorrow")
    assert (response.kind, response.text) == ("error", graph_module.GENERIC_TEXT)
    assert "RuntimeError: boom" in response.debug.error
    assert any("agent turn failed" in r.getMessage() for r in caplog.records)


async def test_answer_llm_failure_falls_back_to_template(respx_mock) -> None:
    mock_onecall(respx_mock)
    failing = RecordingChatModel(replies=[ModelRateLimitError("429 RESOURCE_EXHAUSTED")] * 4)
    agent, _, _ = make_agent(q(locations=["Jaipur"], date_expr="tomorrow", time_expr="evening"), answer=failing)

    response = await agent.ask("Jaipur tomorrow evening")

    assert response.kind == "answer"
    assert response.debug.answer_fallback is True
    assert "LLMRateLimitError" in response.debug.error
    stats_points = response.cards[0].hourly
    low, high = min(p.temp_c for p in stats_points), max(p.temp_c for p in stats_points)
    assert response.text.startswith("Jaipur, Mon 19 Oct, 17:00-21:00 IST: expected")
    assert f"{low:.0f}-{high:.0f}°C" in response.text
