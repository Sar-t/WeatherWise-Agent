import asyncio
import copy
from datetime import timedelta

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
from backend.agent.schemas import AgentDebug, QueryUnderstanding, UserContext
from backend.core.config import get_settings
from backend.services import air_quality, weather
from backend.services import guidelines as guidelines_service
from backend.services.cache import InMemoryCache
from backend.services.weather import ONECALL_URL
from backend.tests.agent_fakes import (
    ONECALL_BY_LAT_AQ,
    QueuedExtractor,
    RecordingChatModel,
    fixture_now,
    install_fake_geocoding,
    mock_air_quality,
    mock_onecall,
    onecall,
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
def _offline(monkeypatch: pytest.MonkeyPatch, respx_mock) -> None:
    """Fake geocoding and key; recorded air quality (Delhi/Paris; elsewhere 503) for every test."""
    install_fake_geocoding(monkeypatch)
    monkeypatch.setenv("OPENWEATHER_API_KEY", "test-ow-key")
    get_settings.cache_clear()
    monkeypatch.setattr(weather, "cache", InMemoryCache())
    monkeypatch.setattr(air_quality, "cache", InMemoryCache())
    mock_air_quality(respx_mock)

    async def no_sleep(delay: float) -> None:
        return None

    monkeypatch.setattr(llm, "_sleep", no_sleep)

    real_calls = []  # the guidelines node swallows errors, so record and check after the test

    def real_retriever_called(**kwargs):
        real_calls.append(kwargs)
        return []

    monkeypatch.setattr(guidelines_service, "retrieve_guidelines", real_retriever_called)
    yield
    get_settings.cache_clear()
    assert not real_calls, "pytest must not use the real embedding model; pass a FakeRetriever"


class FakeRetriever:
    """Stands in for retrieve_guidelines(): records calls, returns queued results (or raises)."""

    def __init__(self, results=None, error: Exception | None = None) -> None:
        self.results, self.error, self.calls = list(results or []), error, []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return list(self.results)


def make_agent(*understandings, answer: RecordingChatModel | None = None, now=JAIPUR_NOW,
               retriever: FakeRetriever | None = None, **ctx) -> tuple:
    extractor = QueuedExtractor(*understandings)
    answer = answer or RecordingChatModel(replies=["Rain is unlikely; expect about 27°C."])
    graph = build_graph(extractor=extractor, answer_llm=answer, retriever=retriever or FakeRetriever())
    agent = Agent(UserContext(now=now, **ctx), graph=graph)
    return agent, extractor, answer


# --- happy paths -----------------------------------------------------------


async def test_simple_weather_query_end_to_end(respx_mock) -> None:
    route = mock_onecall(respx_mock)
    agent, extractor, answer = make_agent(q(locations=["Jaipur"], date_expr="tomorrow", time_expr="evening"))

    response = await agent.ask("Will it rain in Jaipur tomorrow evening?")

    assert (response.kind, response.text) == ("answer", "Rain is unlikely; expect about 27°C.")
    assert response.debug.nodes == ["understand", "resolve", "fetch", "guidelines", "answer"]
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
        assert raw_key not in prompt.split("Observations:")[1]
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


# --- air quality -------------------------------------------------------------------


AQ_NOW = fixture_now("delhi_aq")  # Thu 22 Oct 2026 12:00 IST, recorded with the air quality fixtures


@pytest.fixture
def aq_world(respx_mock):
    """Weather recorded alongside the air quality fixtures for Delhi and Paris."""
    return mock_onecall(respx_mock, by_lat=ONECALL_BY_LAT_AQ)


def run_q(**slots) -> QueryUnderstanding:
    return q(**({"intent": "activity_recommendation", "activity": "running", "locations": ["Delhi"],
                 "date_expr": "tomorrow", "time_expr": "morning"} | slots))


async def test_activity_query_gets_weather_and_air_quality(aq_world) -> None:
    agent, extractor, answer = make_agent(run_q(), now=AQ_NOW)
    response = await agent.ask("Is it ok to run in Delhi tomorrow morning?")

    assert response.kind == "answer"
    assert sorted(t.name for t in response.debug.tool_calls) == ["get_air_quality_for_windows",
                                                                 "get_weather_for_windows"]
    [card], [air_card] = response.cards, response.air_quality
    assert (air_card.granularity, air_card.location.name, len(air_card.points)) == ("hourly", "New Delhi", 6)
    assert air_card.window == card.window
    prompt = answer.calls[0][-1].content
    assert "New Delhi | FORECAST, hourly" in prompt
    assert "New Delhi | AIR QUALITY FORECAST, hourly (India NAQI, CPCB)" in prompt
    assert f"worst NAQI {air_card.summary.naqi_max}, {air_card.summary.naqi_category}" in prompt
    assert "µg/m³" in prompt and "not the index" in prompt
    assert response.debug.llm_calls == 2  # air quality adds no LLM call


async def test_weather_and_air_quality_are_fetched_concurrently(aq_world, monkeypatch: pytest.MonkeyPatch) -> None:
    started = {"weather": asyncio.Event(), "air": asyncio.Event()}
    real_weather, real_air = weather.get_weather_multi, air_quality.get_air_quality_multi

    async def slow_weather(*args, **kwargs):
        started["weather"].set()
        await asyncio.wait_for(started["air"].wait(), timeout=2)  # only returns if air quality started too
        return await real_weather(*args, **kwargs)

    async def slow_air(*args, **kwargs):
        started["air"].set()
        await asyncio.wait_for(started["weather"].wait(), timeout=2)
        return await real_air(*args, **kwargs)

    monkeypatch.setattr(weather, "get_weather_multi", slow_weather)
    monkeypatch.setattr(air_quality, "get_air_quality_multi", slow_air)
    agent, _, _ = make_agent(run_q(), now=AQ_NOW)
    response = await agent.ask("Is it ok to run in Delhi tomorrow morning?")
    assert response.kind == "answer" and response.air_quality  # sequential calls would have timed out


async def test_air_quality_failure_becomes_a_note_not_an_error(aq_world) -> None:
    agent, _, answer = make_agent(run_q(locations=["Jaipur"]), now=JAIPUR_NOW)  # no recording for Jaipur: 503
    response = await agent.ask("Is it ok to run in Jaipur tomorrow morning?")

    assert response.kind == "answer" and response.cards and response.air_quality == []
    assert "Jaipur | AIR QUALITY UNAVAILABLE: OpenWeather request failed (HTTP 503)." in answer.calls[0][-1].content
    assert response.debug.error is None


async def test_air_quality_only_question_skips_the_weather(aq_world) -> None:
    agent, _, answer = make_agent(q(intent="air_quality", locations=["Delhi"], date_expr="tomorrow"), now=AQ_NOW)
    response = await agent.ask("Delhi mein kal pollution kaisa rahega?")

    assert [t.name for t in response.debug.tool_calls] == ["get_air_quality_for_windows"]
    assert aq_world.call_count == 0 and response.cards == []
    [air_card] = response.air_quality
    assert len(air_card.points) == 24
    assert "FORECAST, hourly |" not in answer.calls[0][-1].content  # no weather observation


async def test_air_quality_only_beyond_the_forecast_says_so(aq_world) -> None:
    agent, _, _ = make_agent(q(intent="air_quality", locations=["Delhi"], date_expr="next_tuesday"), now=AQ_NOW)
    response = await agent.ask("How will Delhi's air be next Tuesday?")
    assert (response.kind, response.text) == ("error", graph_module.AIR_OUT_OF_RANGE_TEXT)
    assert "OutOfRangeError" in response.debug.error


async def test_air_quality_beyond_its_forecast_does_not_block_the_weather(aq_world) -> None:
    agent, _, answer = make_agent(run_q(date_expr="next_monday", time_expr="morning"), now=AQ_NOW)  # 26 Oct
    response = await agent.ask("Can I run in Delhi on Monday morning?")
    assert response.kind == "answer" and response.cards and not response.air_quality
    assert "AIR QUALITY UNAVAILABLE: Air quality forecasts are available up to 4 days ahead." in (
        answer.calls[0][-1].content)


async def test_weather_question_mentioning_smog_includes_air_quality(aq_world) -> None:
    agent, _, _ = make_agent(q(locations=["Delhi"], date_expr="tomorrow", include_air_quality=True), now=AQ_NOW)
    response = await agent.ask("Will it be hot in Delhi tomorrow, and how bad is the smog?")
    assert response.cards and response.air_quality


async def test_plain_weather_and_clothing_questions_skip_air_quality(aq_world) -> None:
    for understanding in (q(locations=["Delhi"], date_expr="tomorrow"),
                          q(intent="clothing_recommendation", locations=["Delhi"], date_expr="tomorrow")):
        agent, _, _ = make_agent(understanding, now=AQ_NOW)
        response = await agent.ask("Delhi tomorrow")
        assert [t.name for t in response.debug.tool_calls] == ["get_weather_for_windows"]
        assert response.air_quality == []


async def test_follow_up_keeps_air_quality_and_uses_owm_scale_abroad(aq_world) -> None:
    agent, _, answer = make_agent(run_q(), q(locations=["Paris"], is_follow_up=True),
                                  answer=RecordingChatModel(replies=["Delhi reply.", "Paris reply."]), now=AQ_NOW)
    await agent.ask("Is it ok to run in Delhi tomorrow morning?")
    response = await agent.ask("and Paris?")

    [air_card] = response.air_quality
    assert air_card.location.country_code == "fr"
    prompt = answer.calls[1][-1].content
    assert "Paris, France | AIR QUALITY FORECAST, hourly (OpenWeather 1-5 scale, not NAQI)" in prompt
    assert "NAQI" not in prompt.split("AIR QUALITY FORECAST")[1].split("|", 1)[1]  # no NAQI shown for Paris


async def test_answer_failure_template_includes_air_quality(aq_world) -> None:
    failing = RecordingChatModel(replies=[ModelRateLimitError("429 RESOURCE_EXHAUSTED")] * 4)
    agent, _, _ = make_agent(run_q(), answer=failing, now=AQ_NOW)
    response = await agent.ask("Is it ok to run in Delhi tomorrow morning?")

    s = response.air_quality[0].summary
    assert response.debug.answer_fallback
    assert (f"New Delhi air quality, Fri 23 Oct, 06:00-12:00 IST: NAQI up to {s.naqi_max} ({s.naqi_category}, "
            f"mainly {s.dominant_pollutant}).") in response.text


# --- activity assessments (scoring engine) ---------------------------------------------


def prompt_of(answer: RecordingChatModel, turn: int = 0) -> str:
    return answer.calls[turn][-1].content


def assessment_text(prompt: str) -> str:
    """The first ACTIVITY ASSESSMENT block in the answer prompt."""
    start = prompt.index("| ACTIVITY ASSESSMENT")
    start = prompt.rindex("\n", 0, start) + 1
    return prompt[start:].split("\n\n")[0]


async def test_suitability_runs_the_engine_and_passes_its_verdict_unchanged(aq_world) -> None:
    agent, _, answer = make_agent(run_q(recommendation_type="suitability"), now=AQ_NOW)
    response = await agent.ask("Is it ok to run in Delhi tomorrow morning?")

    assert response.debug.nodes == ["understand", "resolve", "fetch", "assess", "guidelines", "answer"]
    [a] = response.assessments
    assert (a.profile, a.location.name, a.windows) == ("running", "New Delhi", [response.cards[0].window])
    text = assessment_text(prompt_of(answer))
    lines = text.splitlines()
    assert lines[0] == "New Delhi | ACTIVITY ASSESSMENT (running) | Fri 23 Oct, 06:00-12:00 IST"
    assert lines[1] == (f"VERDICT (from the scoring engine; explain it, don't change it): "
                        f"{a.verdict.replace('_', ' ')}, score {a.score}/100")
    assert lines[2].startswith("Main reasons:")  # suitability: reasons right after the verdict
    assert prompt_of(answer).index("ACTIVITY ASSESSMENT") < prompt_of(answer).index("| FORECAST, hourly")
    assert response.air_quality and not any("Air quality" in n for n in a.data_quality)  # air quality scored
    assert response.debug.llm_calls == 2  # scoring adds no LLM call


async def test_best_time_lists_best_blocks_first_in_local_time(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, answer = make_agent(q(intent="activity_recommendation", activity="cycling", locations=["Jaipur"],
                                    date_expr="tomorrow", recommendation_type="best_time"))
    response = await agent.ask("When should I cycle in Jaipur tomorrow?")

    [a] = response.assessments
    lines = assessment_text(prompt_of(answer)).splitlines()
    best = a.best_blocks[0]
    assert lines[2].startswith(f"- Best time: {best.start:%a %H:%M}-{best.end:%H:%M}, score {best.average_score}")
    assert best.end - best.start == timedelta(hours=2)  # cycling: 2 h blocks
    assert best.start.tzinfo.key == "Asia/Kolkata"


async def test_avoid_lists_avoid_blocks_first(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, answer = make_agent(q(intent="activity_recommendation", locations=["Mumbai"], date_expr="tomorrow",
                                    recommendation_type="avoid"), now=CITIES_NOW)
    response = await agent.ask("What times should I avoid being outside in Mumbai tomorrow?")

    [a] = response.assessments
    assert a.profile == "general_outdoor" and a.avoid_blocks  # no activity named; Mumbai feels like 42°C
    lines = assessment_text(prompt_of(answer)).splitlines()
    worst = a.avoid_blocks[0]
    assert lines[2].startswith(f"- Avoid: {worst.start:%a %H:%M}-{worst.end:%H:%M}")
    assert "too hot" in lines[2]


async def test_clothing_uses_general_outdoor_and_leads_with_hints(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, answer = make_agent(q(intent="clothing_recommendation", locations=["Mumbai"], date_expr="tomorrow",
                                    recommendation_type="packing"), now=CITIES_NOW)
    response = await agent.ask("What should I wear in Mumbai tomorrow?")

    [a] = response.assessments
    assert a.profile == "general_outdoor" and not any("No profile" in n for n in a.data_quality)
    lines = assessment_text(prompt_of(answer)).splitlines()
    assert lines[2] == f"Hints: {' '.join(a.hints)}"
    assert "breathable" in lines[2]
    assert response.air_quality == []  # clothing questions don't fetch air quality


async def test_travel_uses_the_travel_profile_and_trip_duration(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, answer = make_agent(q(intent="travel_recommendation", locations=["Mumbai"], date_expr="tomorrow",
                                    recommendation_type="travel_timing", duration_hours=3), now=CITIES_NOW)
    response = await agent.ask("kal 3 ghante ki drive hai Mumbai tak, kab nikalun?")

    [a] = response.assessments
    assert a.profile == "travel"
    assert all(b.end - b.start == timedelta(hours=3) for b in a.best_blocks)
    lines = assessment_text(prompt_of(answer)).splitlines()
    assert lines[2].startswith("- Best time:")  # travel_timing: best, then avoid
    assert "AIR QUALITY UNAVAILABLE" in prompt_of(answer)  # travel asks for air quality; no Mumbai recording


async def test_comparison_with_an_activity_assesses_each_city(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, answer = make_agent(q(intent="comparison", activity="running", locations=["Delhi", "Mumbai"],
                                    date_expr="tomorrow", time_expr="morning"), now=CITIES_NOW)
    response = await agent.ask("Delhi or Mumbai for a run tomorrow morning?")

    assert [a.location.name for a in response.assessments] == ["New Delhi", "Bandra West"]
    assert prompt_of(answer).count("| ACTIVITY ASSESSMENT (running)") == 2
    delhi, mumbai = response.assessments
    assert delhi.score > mumbai.score  # Mumbai feels hotter


async def test_comparison_without_an_activity_is_not_assessed(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, _ = make_agent(q(intent="comparison", locations=["Delhi", "Mumbai"], date_expr="tomorrow"),
                             now=CITIES_NOW)
    response = await agent.ask("Delhi or Mumbai tomorrow?")
    assert response.assessments == [] and "assess" not in response.debug.nodes


async def test_five_days_ahead_is_a_daily_estimate(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, answer = make_agent(q(intent="activity_recommendation", activity="picnic", locations=["Jaipur"],
                                    date_expr="next_friday"))  # Sun 18 Oct -> Fri 23 Oct
    response = await agent.ask("Picnic in Jaipur on Friday?")

    [a] = response.assessments
    assert a.is_daily_estimate and a.confidence == "low"
    assert [h.part_of_day for h in a.hourly] == ["morning", "afternoon", "evening", "night"]
    text = assessment_text(prompt_of(answer))
    assert text.splitlines()[1] == "DAILY ESTIMATE: rough outlook from daily forecasts, lower confidence"
    assert "(morning)" in text or "(afternoon)" in text  # best times are parts of the day


async def test_missing_air_quality_still_assesses_with_a_note(aq_world) -> None:
    agent, _, answer = make_agent(run_q(locations=["Jaipur"]), now=JAIPUR_NOW)  # Jaipur air quality: 503
    response = await agent.ask("Can I run in Jaipur tomorrow morning?")

    [a] = response.assessments
    assert "Air quality unavailable; not scored." in a.data_quality
    assert "Data note: Air quality unavailable; not scored." in assessment_text(prompt_of(answer))


async def test_assessment_failure_does_not_block_the_answer(aq_world, monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.agent import assess

    def broken(*args, **kwargs):
        raise RuntimeError("scoring bug")

    monkeypatch.setattr(assess, "assess_activity", broken)
    agent, _, _ = make_agent(run_q(), now=AQ_NOW)
    response = await agent.ask("Is it ok to run in Delhi tomorrow morning?")
    assert response.kind == "answer" and response.assessments == [] and response.cards


async def test_template_fallback_leads_with_verdict_and_best_time(aq_world) -> None:
    failing = RecordingChatModel(replies=[ModelRateLimitError("429 RESOURCE_EXHAUSTED")] * 4)
    agent, _, _ = make_agent(run_q(), answer=failing, now=AQ_NOW)
    response = await agent.ask("Is it ok to run in Delhi tomorrow morning?")

    [a] = response.assessments
    best = a.best_blocks[0]
    assert response.text.splitlines()[0] == (
        f"Running in New Delhi, Fri 23 Oct, 06:00-12:00 IST: {a.verdict.replace('_', ' ')} ({a.score}/100); "
        f"best time {best.start:%a %H:%M}-{best.end:%H:%M}.")


async def test_guidelines_step_stores_topics(respx_mock) -> None:
    mock_onecall(respx_mock)
    graph = build_graph(extractor=QueuedExtractor(q(intent="activity_recommendation", locations=["Mumbai"],
                                                    date_expr="tomorrow")),
                        answer_llm=RecordingChatModel(replies=["ok"]), retriever=FakeRetriever())
    config = {"configurable": {"thread_id": "t", "ctx": UserContext(now=CITIES_NOW)}}
    state = await graph.ainvoke({"message": "Mumbai tomorrow outdoors?", "response": None, "debug": AgentDebug(),
                                 "cards": [], "air_cards": [], "places": [], "assessments": [],
                                 "guideline_topics": [], "observations": []}, config)
    assert "heat" in state["guideline_topics"]  # Mumbai feels like 42°C
    assert set(state["guideline_topics"]) <= {"heat", "cold", "rain", "thunderstorm", "air_quality", "uv", "wind"}
    assert "guidelines" in state["debug"].nodes


# --- official guidance (fake retriever; never the real embedding model) ---------------------


def heat_docs() -> list:
    from backend.tests.test_guidance import guideline

    return [guideline("heat-stay-cool"), guideline("heat-exertion-and-vulnerable-groups"), guideline("uv-sun-protection")]


def outdoors_mumbai(**slots) -> QueryUnderstanding:
    return q(**({"intent": "activity_recommendation", "locations": ["Mumbai"], "date_expr": "tomorrow"} | slots))


async def test_assessment_topics_retrieve_guidance_for_the_answer(respx_mock) -> None:
    mock_onecall(respx_mock)
    retriever = FakeRetriever(heat_docs())
    agent, _, answer = make_agent(outdoors_mumbai(), now=CITIES_NOW, retriever=retriever)
    response = await agent.ask("Can I be outdoors in Mumbai tomorrow?")

    [call] = retriever.calls
    assert call["topics"][0] == "heat" and call["activity"] == "general_outdoor"
    assert (call["audience"], call["k"], call["severity"]) == ("general", 3, None)
    assert [s.title for s in response.sources] == [d.title for d in heat_docs()[:2]]  # at most 2 documents
    assert response.sources[0].source_name.startswith("WHO") and response.sources[0].source_url.startswith("https://")
    prompt = answer.calls[0][-1].content
    section = prompt[prompt.index("GUIDANCE (official sources"):]
    assert section.count("\n- ") == 2 and "Sun protection" not in section
    assert prompt.index("ACTIVITY ASSESSMENT") < prompt.index("GUIDANCE")
    assert "guidelines" in response.debug.timings


async def test_perfect_best_block_with_warm_hours_after_it_retrieves_no_guidance(aq_world) -> None:
    """The live Delhi case: 'run tomorrow morning?' scored 100/100 at 06:30-07:30, but the later
    morning hours are warm. Those hours are outside the best block, so no heat guidance."""
    retriever = FakeRetriever(heat_docs())
    agent, _, answer = make_agent(run_q(recommendation_type="suitability"), now=AQ_NOW, retriever=retriever)
    response = await agent.ask("Can I go for a run in Delhi tomorrow morning?")

    [a] = response.assessments
    assert a.best_blocks[0].average_score == 100
    warm_later = [h for h in a.hourly if h.time >= a.best_blocks[0].end
                  and any(f.name == "feels_like_c" and f.reason for f in h.factors)]
    assert warm_later  # the hours that used to raise the heat topic
    assert retriever.calls == [] and response.sources == []
    assert "GUIDANCE" not in answer.calls[0][-1].content


async def test_audience_is_passed_through(respx_mock) -> None:
    mock_onecall(respx_mock)
    retriever = FakeRetriever(heat_docs())
    agent, _, _ = make_agent(outdoors_mumbai(activity="walking", audience="older_adults"), now=CITIES_NOW,
                             retriever=retriever)
    await agent.ask("Can my parents go for a walk in Mumbai tomorrow?")
    assert retriever.calls[0]["audience"] == "older_adults" and retriever.calls[0]["activity"] == "walking"


async def test_plain_weather_does_not_retrieve(respx_mock) -> None:
    mock_onecall(respx_mock)
    retriever = FakeRetriever(heat_docs())
    agent, _, answer = make_agent(q(locations=["Jaipur"], date_expr="tomorrow", time_expr="evening"),
                                  retriever=retriever)
    response = await agent.ask("Will it rain in Jaipur tomorrow evening?")
    assert retriever.calls == [] and response.sources == []
    assert "GUIDANCE" not in answer.calls[0][-1].content


async def test_retrieval_failure_still_answers_without_guidance(respx_mock) -> None:
    mock_onecall(respx_mock)
    retriever = FakeRetriever(error=RuntimeError("index missing"))
    agent, _, answer = make_agent(outdoors_mumbai(), now=CITIES_NOW, retriever=retriever)
    response = await agent.ask("Can I be outdoors in Mumbai tomorrow?")
    assert response.kind == "answer" and response.sources == [] and response.debug.error is None
    assert "GUIDANCE" not in answer.calls[0][-1].content


async def test_no_documents_found_means_no_section_or_sources(respx_mock) -> None:
    mock_onecall(respx_mock)
    agent, _, answer = make_agent(outdoors_mumbai(), now=CITIES_NOW, retriever=FakeRetriever([]))
    response = await agent.ask("Can I be outdoors in Mumbai tomorrow?")
    assert response.sources == [] and "GUIDANCE" not in answer.calls[0][-1].content


async def test_template_fallback_includes_one_guidance_line(respx_mock) -> None:
    mock_onecall(respx_mock)
    failing = RecordingChatModel(replies=[ModelRateLimitError("429 RESOURCE_EXHAUSTED")] * 4)
    agent, _, _ = make_agent(outdoors_mumbai(), now=CITIES_NOW, answer=failing, retriever=FakeRetriever(heat_docs()))
    response = await agent.ask("Can I be outdoors in Mumbai tomorrow?")
    assert response.debug.answer_fallback
    [line] = [x for x in response.text.splitlines() if x.startswith("Guidance (")]
    assert line.startswith("Guidance (WHO")
    assert response.sources  # the fallback still cites what it used


async def test_air_quality_precautions_use_the_naqi_category_as_severity(aq_world) -> None:
    retriever = FakeRetriever([])
    agent, _, _ = make_agent(q(intent="air_quality", locations=["Delhi"], date_expr="tomorrow", include_air_quality=True,
                               audience="children", asks_precautions=True), now=AQ_NOW, retriever=retriever)
    await agent.ask("Delhi mein pollution hai, bacchon ke liye kya precautions?")
    [call] = retriever.calls
    assert call["topics"] == ["air_quality"] and call["audience"] == "children"
    assert call["severity"] == "NAQI Moderately Polluted"  # Delhi's recorded worst: 126


async def test_severe_alert_triggers_guidance_with_alert_severity(respx_mock) -> None:
    """SYNTHETIC: the recorded Jaipur forecast has no alerts, so one IMD alert is added for this test."""
    data = copy.deepcopy(onecall("jaipur"))
    data["alerts"] = [{"sender_name": "IMD", "event": "Thunderstorm & Lightning", "description": "Synthetic test alert",
                       "start": int(JAIPUR_NOW.timestamp()), "end": int((JAIPUR_NOW + timedelta(days=1)).timestamp()),
                       "tags": []}]
    respx_mock.get(ONECALL_URL).mock(return_value=httpx.Response(200, json=data))
    retriever = FakeRetriever([])
    agent, _, _ = make_agent(q(locations=["Jaipur"], date_expr="tomorrow", time_expr="morning"), retriever=retriever)
    await agent.ask("Jaipur tomorrow morning?")
    [call] = retriever.calls
    assert call["topics"] == ["thunderstorm"] and call["severity"] == "thunderstorm"
