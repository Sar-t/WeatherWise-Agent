"""Run one agent turn for the API, reporting progress (status) and answer text (tokens) as it goes."""

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from backend.agent.graph import turn_input
from backend.agent.llm import message_text
from backend.agent.schemas import AgentResponse, UserContext, wants_air_quality

log = logging.getLogger(__name__)

# What the user sees while each graph step runs.
NODE_STATUS = {
    "understand": "understanding",
    "resolve": "finding location",
    "fetch": "fetching weather",
    "assess": "assessing",
    "guidelines": "looking up guidance",
    "answer": "writing answer",
}
AIR_STATUS = "checking air quality"

Emit = Callable[[str, str], Awaitable[None]]  # (event, text): ("status", "fetching weather") / ("token", "Rain ")


class AgentService(Protocol):
    async def run(self, message: str, conversation_id: str, ctx: UserContext, emit: Emit | None = None
                  ) -> AgentResponse: ...

    async def delete_thread(self, conversation_id: str) -> None: ...


class GraphAgentService:
    """The compiled LangGraph agent; `thread_id` = conversation id, so follow-ups use its memory."""

    def __init__(self, graph: Any, checkpointer: Any = None) -> None:
        self.graph = graph
        self.checkpointer = checkpointer

    async def run(self, message: str, conversation_id: str, ctx: UserContext, emit: Emit | None = None
                  ) -> AgentResponse:
        started = time.perf_counter()
        config = {"configurable": {"thread_id": conversation_id, "ctx": ctx}}
        final: dict | None = None
        understanding = None
        async for event in self.graph.astream_events(turn_input(message), config, version="v2"):
            kind, name = event["event"], event.get("name")
            node = (event.get("metadata") or {}).get("langgraph_node")
            if kind == "on_chain_end" and not event.get("parent_ids"):
                final = event["data"].get("output")  # the whole graph's final state
            if emit is None:
                continue
            if kind == "on_chain_start" and name in NODE_STATUS and node == name:
                await emit("status", NODE_STATUS[name])
                if name == "fetch" and understanding is not None and wants_air_quality(understanding):
                    await emit("status", AIR_STATUS)
            elif kind == "on_chain_end" and name == "understand" and node == name:
                understanding = (event["data"].get("output") or {}).get("understanding")
            elif kind == "on_chat_model_stream" and node == "answer":
                text = message_text(event["data"]["chunk"])
                if text:
                    await emit("token", text)
        if not final or final.get("response") is None:
            raise RuntimeError("the agent finished without a response")
        response: AgentResponse = final["response"]
        response.debug.timings["total"] = round(time.perf_counter() - started, 3)
        return response

    async def delete_thread(self, conversation_id: str) -> None:
        if self.checkpointer is not None and hasattr(self.checkpointer, "adelete_thread"):
            await self.checkpointer.adelete_thread(conversation_id)
