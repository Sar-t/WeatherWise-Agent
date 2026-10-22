"""Extract a QueryUnderstanding from one user message with a single structured-output LLM call."""

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Template
from typing import Any
from zoneinfo import ZoneInfo

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from backend.agent.llm import call_with_backoff, get_llm, message_text
from backend.agent.schemas import QueryUnderstanding, UserContext
from backend.services.date_resolver import DATE_EXPRESSIONS, TIME_EXPRESSIONS

PROMPT_PATH = Path(__file__).parent / "prompts" / "extraction.md"
ATTEMPTS = 2  # first try + one retry on invalid structured output


@dataclass
class Extraction:
    """The extracted slots plus what the CLI's --debug shows."""

    understanding: QueryUnderstanding
    raw: str | None  # raw LLM output of the last attempt
    attempts: int
    seconds: float


def build_system_prompt(ctx: UserContext) -> str:
    """The extraction prompt with today's date and the resolver's vocabulary filled in."""
    now = (ctx.now or datetime.now(ZoneInfo(ctx.timezone))).astimezone(ZoneInfo(ctx.timezone))
    return Template(PROMPT_PATH.read_text(encoding="utf-8")).substitute(
        today=now.strftime("%d %B %Y"),
        weekday=now.strftime("%A"),
        timezone=ctx.timezone,
        date_values=", ".join(DATE_EXPRESSIONS),
        time_values=", ".join(TIME_EXPRESSIONS),
    )


def build_user_message(
    message: str, previous: QueryUnderstanding | None = None, pending_question: str | None = None
) -> str:
    parts = []
    if previous is not None:
        parts.append(f"Previous query: {_slots_json(previous)}")
    if pending_question:
        parts.append(f"You asked the user: {pending_question}")
    parts.append(f"User: {message}")
    return "\n".join(parts)


async def extract(
    message: str,
    ctx: UserContext,
    previous: QueryUnderstanding | None = None,
    pending_question: str | None = None,
    llm: BaseChatModel | None = None,
) -> Extraction:
    """One LLM call normally; one retry on invalid output, then intent 'unclear'."""
    structured = (llm or get_llm("extraction")).with_structured_output(QueryUnderstanding, include_raw=True)
    messages = [
        SystemMessage(build_system_prompt(ctx)),
        HumanMessage(build_user_message(message, previous, pending_question)),
    ]
    started = time.perf_counter()
    raw = None
    for attempt in range(1, ATTEMPTS + 1):
        result: dict[str, Any] = await call_with_backoff(lambda: structured.ainvoke(messages))
        raw = _raw_text(result.get("raw"))
        parsed = result.get("parsed")
        if isinstance(parsed, QueryUnderstanding) and result.get("parsing_error") is None:
            return Extraction(parsed, raw, attempt, time.perf_counter() - started)
    return Extraction(QueryUnderstanding(intent="unclear"), raw, ATTEMPTS, time.perf_counter() - started)


def _slots_json(u: QueryUnderstanding) -> str:
    """Only the filled slots, to keep the prompt short."""
    slots = u.model_dump(exclude_defaults=True, exclude={"is_follow_up", "needs_clarification", "clarification_question"})
    return json.dumps({"intent": u.intent, **slots}, ensure_ascii=False)


def _raw_text(raw: Any) -> str | None:
    if raw is None:
        return None
    tool_calls = getattr(raw, "tool_calls", None)
    if tool_calls:
        return json.dumps([c.get("args") for c in tool_calls], ensure_ascii=False)
    return message_text(raw)
