"""The agent as a LangGraph flow: understand -> resolve -> route -> fetch -> answer | ask | decline.

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
from backend.agent.schemas import (
    AgentDebug,
    AgentResponse,
    QueryUnderstanding,
    ResolvedQuery,
    Target,
    ToolCallInfo,
    UserContext,
)
from backend.agent.tools import describe_args, get_current_weather, get_weather_for_windows
from backend.services import models
from backend.services.errors import OutOfRangeError, UpstreamAPIError
from backend.services.models import WeatherResult

log = logging.getLogger(__name__)

OUT_OF_RANGE_TEXT = "I can only forecast up to 8 days ahead. Could you pick a day within the next week?"
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
                 "ToolCallInfo")
] + [(models.__name__, name) for name in ("Location", "TimeWindow", "WeatherResult", "WeatherPoint",
                                          "DailyWeather", "WeatherAlert")] + [("backend.agent.memory", "MemoryState")]


class AgentState(TypedDict, total=False):
    message: str
    memory: MemoryState  # carries over between turns
    understanding: QueryUnderstanding
    remember: bool
    resolved: ResolvedQuery
    cards: list[WeatherResult]
    observations: list[str]
    response: AgentResponse | None
    debug: AgentDebug


def build_graph(extractor: Extractor = extract, answer_llm: Any = None, checkpointer: Any = None):
    """Compile the agent graph. `extractor` and `answer_llm` are injectable for tests and evals."""

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
        with_current = resolved.date_expr == "today" and resolved.understanding.time_expr is None

        async def fetch_target(target: Target) -> list[ToolMessage]:
            # Sequential within a place so the current-conditions call reuses the cached onecall.
            messages = [await _call_tool(get_weather_for_windows, debug, location=target.location,
                                         windows=target.windows, time_expr=resolved.time_expr, now=ctx.now)]
            if with_current:
                messages.insert(0, await _call_tool(get_current_weather, debug, location=target.location))
            return messages

        per_target = await asyncio.gather(*(fetch_target(t) for t in resolved.targets))  # places in parallel
        observations, cards = [], []
        for message in (m for messages in per_target for m in messages):
            observations.append(message.content)
            cards += message.artifact if isinstance(message.artifact, list) else [message.artifact]
        return {"observations": observations, "cards": cards, "debug": debug}

    async def answer_node(state: AgentState, config: RunnableConfig) -> dict:
        debug = state["debug"]
        ctx = _ctx(config)
        now = (ctx.now or datetime.now(ZoneInfo(ctx.timezone))).astimezone(ZoneInfo(ctx.timezone))
        answer = await generate_answer(state["message"], state["understanding"], state["observations"],
                                       state["cards"], llm=answer_llm, now=now)
        debug.llm_calls += 1
        debug.answer_fallback = answer.fallback
        if answer.error:
            debug.error = answer.error
        return {"response": AgentResponse(text=answer.text, kind="answer", cards=state["cards"], debug=debug)}

    async def ask_node(state: AgentState, config: RunnableConfig) -> dict:
        return {"response": AgentResponse(text=state["resolved"].clarification.question, kind="clarification",
                                          debug=state["debug"])}

    async def decline_node(state: AgentState, config: RunnableConfig) -> dict:
        return {"response": AgentResponse(text=DECLINE_TEXT, kind="decline", debug=state["debug"])}

    graph = StateGraph(AgentState)
    for name, node in [("understand", understand_node), ("resolve", resolve_node), ("fetch", fetch_node),
                       ("answer", answer_node), ("ask", ask_node), ("decline", decline_node)]:
        graph.add_node(name, _tracked(name, node))
    graph.add_edge(START, "understand")
    graph.add_conditional_edges("understand", _stop_on_error("resolve"))
    graph.add_conditional_edges("resolve", _route)
    graph.add_conditional_edges("fetch", _stop_on_error("answer"))
    for name in ("answer", "ask", "decline"):
        graph.add_edge(name, END)
    checkpointer = checkpointer or MemorySaver(serde=JsonPlusSerializer(allowed_msgpack_modules=_STATE_TYPES))
    return graph.compile(checkpointer=checkpointer)


class Agent:
    """One conversation: a thread on the graph plus the user's context."""

    def __init__(self, ctx: UserContext, graph: Any = None, thread_id: str | None = None) -> None:
        self.ctx = ctx
        self.graph = graph or build_graph()
        self.thread_id = thread_id or uuid.uuid4().hex

    async def ask(self, message: str) -> AgentResponse:
        started = time.perf_counter()
        turn_input = {"message": message, "response": None, "debug": AgentDebug(), "cards": [], "observations": []}
        config = {"configurable": {"thread_id": self.thread_id, "ctx": self.ctx}}
        state = await self.graph.ainvoke(turn_input, config)
        response = state["response"]
        response.debug.timings["total"] = round(time.perf_counter() - started, 3)
        return response


# --- helpers ---------------------------------------------------------------


def _ctx(config: RunnableConfig) -> UserContext:
    return config["configurable"]["ctx"]


def _route(state: AgentState) -> str:
    if state.get("response") is not None:
        return END
    return {"clarify": "ask", "out_of_scope": "decline", "ready": "fetch"}[state["resolved"].status]


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
