"""The agent as a LangGraph flow:
understand -> resolve -> route -> fetch -> [assess -> guidelines] -> answer | ask | decline.

Two LLM calls per answered message (understand, answer); everything else is deterministic.
Conversation memory lives in graph state, saved per thread_id by the checkpointer.
"""

import asyncio
import logging
import time
import uuid
from datetime import datetime
from collections.abc import Awaitable, Callable
from typing import Any, TypedDict
from zoneinfo import ZoneInfo

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

from backend.agent import schemas
from backend.agent.answer import generate_answer
from backend.agent.extraction import extract
from backend.agent.llm import LLMConfigError, LLMRateLimitError, LLMUnavailableError
from backend.agent.memory import Extractor, MemoryState, Turn, record, understand
from backend.agent.resolution import resolve_query
from backend.agent.assess import assess_place, guideline_topics, should_assess
from backend.agent.guidance import MAX_DOCS, guidance_line, keep_relevant, plan_guidance, summarize_guidance
from backend.agent.observations import air_unavailable, summarize_assessment
from backend.agent.schemas import (
    GuidelineSource,
    AgentDebug,
    AgentResponse,
    QueryUnderstanding,
    ResolvedQuery,
    Target,
    ToolCallInfo,
    UserContext,
    wants_air_quality,
)
from backend.agent.tools import (
    describe_args,
    get_air_quality_for_windows,
    get_current_weather,
    get_weather_for_windows,
)
from backend.services import guidelines, models
from backend.services.errors import OutOfRangeError, UpstreamAPIError, WeatherWiseError
from backend.services.models import AirQualityResult, WeatherResult
from backend.services.scoring import models as scoring_models
from backend.services.scoring.models import ActivityAssessment

log = logging.getLogger(__name__)

OUT_OF_RANGE_TEXT = "I can only forecast up to 8 days ahead. Could you pick a day within the next week?"
AIR_OUT_OF_RANGE_TEXT = ("I can only forecast air quality up to 4 days ahead. "
                         "Could you pick a day within the next 4 days?")
UPSTREAM_TEXT = "Weather data is temporarily unavailable, please try again."
BUSY_TEXT = "The assistant is busy right now. Please try again in a minute."
CONFIG_TEXT = "The assistant isn't set up yet (missing model name or API key)."
GENERIC_TEXT = "Sorry, something went wrong on my side. Please try again."
DECLINE_TEXT = (
    "Sorry, I can only help with weather, and with planning activities, travel or clothing around it. "
    "Ask me about the weather anywhere!"
)

# Types the checkpointer may restore from saved state (explicit allowlist instead of a warning).
_STATE_TYPES = [
    (schemas.__name__, name)
    for name in ("QueryUnderstanding", "ResolvedQuery", "Clarification", "Target", "AgentResponse", "AgentDebug",
                 "ToolCallInfo", "GuidelineSource")
] + [(models.__name__, name) for name in ("Location", "TimeWindow", "WeatherResult", "WeatherPoint",
                                          "DailyWeather", "WeatherAlert", "SunTimes", "AirQualityResult",
                                          "AirQualityPoint", "AirComponents", "AirQualitySummary")
       ] + [(scoring_models.__name__, name) for name in ("ActivityAssessment", "HourAssessment", "FactorScore",
                                                         "TimeBlock")
       ] + [("backend.agent.memory", "MemoryState")]


class AgentState(TypedDict, total=False):
    message: str
    memory: MemoryState  # carries over between turns
    understanding: QueryUnderstanding
    remember: bool
    resolved: ResolvedQuery
    cards: list[WeatherResult]
    air_cards: list[AirQualityResult]
    # Per place, for scoring: its weather results, and its air quality (None: not fetched, []: failed).
    places: list[tuple[list[WeatherResult], list[AirQualityResult] | None]]
    assessments: list[ActivityAssessment]
    guideline_topics: list[str]  # what the guidelines step may look up
    sources: list[GuidelineSource]  # guidelines given to the answer model
    guidance_note: str | None  # one guidance line for the template fallback
    observations: list[str]
    response: AgentResponse | None
    debug: AgentDebug


def build_graph(extractor: Extractor = extract, answer_llm: Any = None, checkpointer: Any = None,
                retriever: Callable[..., list] | None = None):
    """Compile the agent graph. `extractor`, `answer_llm` and `retriever` (default:
    guidelines.retrieve_guidelines) are injectable for tests and evals."""

    async def understand_node(state: AgentState, config: RunnableConfig) -> dict:
        debug = state["debug"]
        turn, memory = await understand(state.get("memory") or MemoryState(), state["message"], _ctx(config), extractor)
        if turn.extraction is not None:
            debug.llm_calls += turn.extraction.attempts
        return {"memory": memory, "understanding": turn.understanding, "remember": turn.remember, "debug": debug}

    async def resolve_node(state: AgentState, config: RunnableConfig) -> dict:
        memory, understanding = state["memory"], state["understanding"]
        resolved = await resolve_query(understanding, _ctx(config), memory.pinned)
        memory = record(memory, Turn(understanding, None, state.get("remember", True)), resolved)
        return {"resolved": resolved, "memory": memory}

    async def fetch_node(state: AgentState, config: RunnableConfig) -> dict:
        ctx, resolved, debug = _ctx(config), state["resolved"], state["debug"]
        air_only = resolved.understanding.intent == "air_quality"
        with_air = wants_air_quality(resolved.understanding)
        with_current = not air_only and resolved.date_expr == "today" and resolved.understanding.time_expr is None

        async def weather_for(target: Target) -> list[ToolMessage]:
            # Sequential within a place so the current-conditions call reuses the cached onecall.
            messages = [await _call_tool(get_weather_for_windows, debug, location=target.location,
                                         windows=target.windows, time_expr=resolved.time_expr, now=ctx.now)]
            if with_current:
                messages.insert(0, await _call_tool(get_current_weather, debug, location=target.location))
            return messages

        async def air_for(target: Target) -> ToolMessage | str:
            """The air quality tool message, or a note saying why it's missing (never blocks the weather)."""
            try:
                return await _call_tool(get_air_quality_for_windows, debug, location=target.location,
                                        windows=target.windows, now=ctx.now)
            except WeatherWiseError as exc:
                if air_only:
                    raise
                log.warning("air quality unavailable", extra={"error": f"{type(exc).__name__}: {exc}"})
                return air_unavailable(target.location, str(exc))

        async def fetch_target(target: Target) -> tuple[list[ToolMessage], ToolMessage | str | None]:
            # Weather and air quality for a place in parallel; an air-quality-only question skips the weather.
            return await asyncio.gather(_value([]) if air_only else weather_for(target),
                                        air_for(target) if with_air else _value(None))

        try:
            per_target = await asyncio.gather(*(fetch_target(t) for t in resolved.targets))  # places in parallel
        except OutOfRangeError as exc:
            if not air_only:
                raise
            response = _error_response(exc, debug)
            return {"response": response.model_copy(update={"text": AIR_OUT_OF_RANGE_TEXT})}

        observations, cards, air_cards, places = [], [], [], []
        for weather_messages, air in per_target:
            place_weather, place_air = [], None
            for message in weather_messages:
                observations.append(message.content)
                place_weather += message.artifact if isinstance(message.artifact, list) else [message.artifact]
            if isinstance(air, str):
                observations.append(air)
                place_air = []  # asked for, but unavailable
            elif air is not None:
                observations.append(air.content)
                place_air = air.artifact
            cards += place_weather
            air_cards += place_air or []
            places.append((place_weather, place_air))
        return {"observations": observations, "cards": cards, "air_cards": air_cards, "places": places,
                "debug": debug}

    async def assess_node(state: AgentState, config: RunnableConfig) -> dict:
        """Score the activity for each place with the scoring engine; its verdict leads the observations."""
        u, now = state["resolved"].understanding, _ctx(config).now
        assessments = []
        for weather_results, air_results in state.get("places") or []:
            try:
                assessment = assess_place(u, weather_results, air_results, now)
            except Exception:  # never lose the weather answer over a scoring problem
                log.exception("assessment failed")
                continue
            if assessment is not None:
                assessments.append(assessment)
        summaries = [summarize_assessment(a, u.recommendation_type, u.intent) for a in assessments]
        return {"assessments": assessments, "observations": summaries + state["observations"]}

    async def guidelines_node(state: AgentState, config: RunnableConfig) -> dict:
        """Official guidance, only when there's a reason (see guidance.plan_guidance). Non-blocking:
        a missing index or failed retrieval means an answer without guidance."""
        assessments, air_cards = state.get("assessments") or [], state.get("air_cards") or []
        alerts = [a for card in state.get("cards") or [] for a in card.alerts]
        u = state["resolved"].understanding
        update: dict = {"guideline_topics": guideline_topics(assessments, alerts, u.recommendation_type), "sources": [],
                        "guidance_note": None}
        request = plan_guidance(state["resolved"].understanding, assessments, air_cards, alerts)
        if request is None:
            return update
        try:
            found = await asyncio.to_thread(retriever or guidelines.retrieve_guidelines, **request.kwargs())
        except Exception:
            log.exception("guideline retrieval failed; answering without guidance")
            return update
        docs = keep_relevant(found, request.severity)[:MAX_DOCS]
        log.info("guidelines", extra={"reasons": request.reasons, "topics": request.topics,
                                      "severity": request.severity, "docs": [d.doc_id for d in docs]})
        if not docs:
            return update
        words = request.words()
        return update | {
            "observations": state["observations"] + [summarize_guidance(docs, words)],
            "sources": [GuidelineSource(title=d.title, source_name=d.source_name, source_url=d.source_url)
                        for d in docs],
            "guidance_note": guidance_line(docs, words),
        }

    async def answer_node(state: AgentState, config: RunnableConfig) -> dict:
        debug = state["debug"]
        ctx = _ctx(config)
        now = (ctx.now or datetime.now(ZoneInfo(ctx.timezone))).astimezone(ZoneInfo(ctx.timezone))
        air_cards, assessments = state.get("air_cards") or [], state.get("assessments") or []
        answer = await generate_answer(state["message"], state["understanding"], state["observations"],
                                       state["cards"], llm=answer_llm, now=now, air_cards=air_cards,
                                       assessments=assessments, guidance_note=state.get("guidance_note"))
        debug.llm_calls += 1
        debug.answer_fallback = answer.fallback
        if answer.error:
            debug.error = answer.error
        return {"response": AgentResponse(text=answer.text, kind="answer", cards=state["cards"],
                                          air_quality=air_cards, assessments=assessments,
                                          sources=state.get("sources") or [], debug=debug)}

    async def ask_node(state: AgentState, config: RunnableConfig) -> dict:
        return {"response": AgentResponse(text=state["resolved"].clarification.question, kind="clarification",
                                          debug=state["debug"])}

    async def decline_node(state: AgentState, config: RunnableConfig) -> dict:
        return {"response": AgentResponse(text=DECLINE_TEXT, kind="decline", debug=state["debug"])}

    graph = StateGraph(AgentState)
    for name, node in [("understand", understand_node), ("resolve", resolve_node), ("fetch", fetch_node),
                       ("assess", assess_node), ("guidelines", guidelines_node), ("answer", answer_node),
                       ("ask", ask_node), ("decline", decline_node)]:
        graph.add_node(name, _tracked(name, node))
    graph.add_edge(START, "understand")
    graph.add_conditional_edges("understand", _stop_on_error("resolve"))
    graph.add_conditional_edges("resolve", _route)
    graph.add_conditional_edges("fetch", _after_fetch)
    graph.add_conditional_edges("assess", _stop_on_error("guidelines"))
    graph.add_conditional_edges("guidelines", _stop_on_error("answer"))
    for name in ("answer", "ask", "decline"):
        graph.add_edge(name, END)
    checkpointer = checkpointer or MemorySaver(serde=state_serde())
    return graph.compile(checkpointer=checkpointer)


def state_serde() -> JsonPlusSerializer:
    """The serializer any checkpointer should use: restores only the agent's own state types."""
    return JsonPlusSerializer(allowed_msgpack_modules=_STATE_TYPES)


def turn_input(message: str) -> dict:
    """Fresh per-turn state (memory carries over through the checkpointer)."""
    return {"message": message, "response": None, "debug": AgentDebug(), "cards": [], "air_cards": [],
            "places": [], "assessments": [], "guideline_topics": [], "sources": [], "guidance_note": None,
            "observations": []}


class Agent:
    """One conversation: a thread on the graph plus the user's context."""

    def __init__(self, ctx: UserContext, graph: Any = None, thread_id: str | None = None) -> None:
        self.ctx = ctx
        self.graph = graph or build_graph()
        self.thread_id = thread_id or uuid.uuid4().hex

    async def ask(self, message: str) -> AgentResponse:
        started = time.perf_counter()
        config = {"configurable": {"thread_id": self.thread_id, "ctx": self.ctx}}
        state = await self.graph.ainvoke(turn_input(message), config)
        response = state["response"]
        response.debug.timings["total"] = round(time.perf_counter() - started, 3)
        return response


# --- helpers ---------------------------------------------------------------


def _ctx(config: RunnableConfig) -> UserContext:
    return config["configurable"]["ctx"]


async def _value(value: Any) -> Any:
    """An awaitable that just returns `value`, to keep asyncio.gather slots uniform."""
    return value


def _route(state: AgentState) -> str:
    if state.get("response") is not None:
        return END
    return {"clarify": "ask", "out_of_scope": "decline", "ready": "fetch"}[state["resolved"].status]


def _after_fetch(state: AgentState) -> str:
    if state.get("response") is not None:
        return END
    return "assess" if should_assess(state["resolved"].understanding) else "guidelines"


def _stop_on_error(next_node: str) -> Callable[[AgentState], str]:
    return lambda state: END if state.get("response") is not None else next_node


def _tracked(name: str, node: Callable[..., Awaitable[dict]]) -> Callable[..., Awaitable[dict]]:
    """Record the node in debug, time it, and turn failures into a user-facing error reply."""

    async def run(state: AgentState, config: RunnableConfig) -> dict:
        debug = state["debug"]
        debug.nodes.append(name)
        started = time.perf_counter()
        try:
            update = await node(state, config)
        except Exception as exc:
            update = {"response": _error_response(exc, debug)}
        debug.timings[name] = round(time.perf_counter() - started, 3)
        return update | {"debug": debug}

    return run


def _error_response(exc: Exception, debug: AgentDebug) -> AgentResponse:
    """Map a failure to a friendly message; the real error goes to the log and debug."""
    if isinstance(exc, OutOfRangeError):
        text = OUT_OF_RANGE_TEXT
    elif isinstance(exc, UpstreamAPIError):
        text = UPSTREAM_TEXT
    elif isinstance(exc, (LLMRateLimitError, LLMUnavailableError)):
        text = BUSY_TEXT
    elif isinstance(exc, LLMConfigError):
        text = CONFIG_TEXT
    else:
        text = GENERIC_TEXT
    debug.error = f"{type(exc).__name__}: {exc}"
    log.error("agent turn failed", extra={"error": debug.error, "node": debug.nodes[-1] if debug.nodes else None},
              exc_info=not isinstance(exc, (OutOfRangeError, UpstreamAPIError, LLMRateLimitError,
                                            LLMUnavailableError, LLMConfigError)))
    return AgentResponse(text=text, kind="error", debug=debug)


async def _call_tool(tool: BaseTool, debug: AgentDebug, **args: Any) -> ToolMessage:
    """Invoke a tool as a tool call (so its artifact comes back) and record it for debug."""
    started = time.perf_counter()
    message = await tool.ainvoke({"type": "tool_call", "id": uuid.uuid4().hex, "name": tool.name, "args": args})
    debug.tool_calls.append(ToolCallInfo(name=tool.name, args=describe_args(args),
                                         seconds=round(time.perf_counter() - started, 3)))
    return message
