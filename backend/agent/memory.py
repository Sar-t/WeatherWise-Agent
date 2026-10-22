"""Conversation state across turns: follow-up merging and answers to clarification questions."""

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, Field

from backend.agent.extraction import Extraction, extract
from backend.agent.resolution import option_label, place_key, resolve_query
from backend.agent.schemas import Clarification, QueryUnderstanding, ResolvedQuery, UserContext
from backend.services.models import Location

Extractor = Callable[..., Awaitable[Extraction]]

_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2}


def merge(previous: QueryUnderstanding, new: QueryUnderstanding) -> QueryUnderstanding:
    """New non-null (and non-empty) slots override the previous ones."""
    merged = previous.model_copy()
    for field in ("intent", "activity", "date_expr", "time_expr", "recommendation_type"):
        if (value := getattr(new, field)) is not None:
            setattr(merged, field, value)
    if new.locations:
        merged.locations = list(new.locations)
    merged.is_follow_up = True
    merged.needs_clarification = new.needs_clarification
    merged.clarification_question = new.clarification_question
    return merged


def match_option(reply: str, options: list[Location], typed: str = "", preferred_country: str = "in") -> Location | None:
    """Match a reply like "2", "second", "Bihar" or "Aurangabad, Bihar" to exactly one option."""
    text = reply.strip().lower()
    index = int(text) - 1 if text.isdigit() else _ORDINALS.get(text)
    if index is not None:
        return options[index] if 0 <= index < len(options) else None

    words = set(re.findall(r"\w+", text))
    hits = []
    for option in options:
        # Words that tell options apart: state, country and name, not the shared typed name.
        distinctive = {w for w in re.findall(r"\w+", option_label(option, "", preferred_country).lower())}
        distinctive -= set(re.findall(r"\w+", typed.lower()))
        if distinctive & words:
            hits.append(option)
    return hits[0] if len(hits) == 1 else None


class MemoryState(BaseModel):
    """What carries over between turns (JSON-serializable, so a checkpointer can store it)."""

    previous: QueryUnderstanding | None = None
    pending: Clarification | None = None  # the question we last asked, if any
    pinned: dict[str, Location] = Field(default_factory=dict)  # typed place name -> chosen Location


@dataclass
class Turn:
    """This turn's understanding and how to record its resolution."""

    understanding: QueryUnderstanding
    extraction: Extraction | None  # None when a clarification was answered without the LLM
    remember: bool = True  # False for a garbled message: keep the earlier query and question


async def understand(memory: MemoryState, message: str, ctx: UserContext, extractor: Extractor = extract) -> tuple[Turn, MemoryState]:
    """Work out what this message asks, given the conversation so far."""
    memory = memory.model_copy(deep=True)
    clarification = memory.pending

    if clarification and clarification.reason == "ambiguous_location" and memory.previous is not None:
        choice = match_option(message, clarification.options, clarification.location_query or "",
                              ctx.preferred_country)
        if choice is not None:  # answered without needing the LLM
            memory.pinned[place_key(clarification.location_query or "")] = choice
            return Turn(memory.previous, None), memory

    extraction = await extractor(
        message, ctx, previous=memory.previous, pending_question=clarification.question if clarification else None,
    )
    new = extraction.understanding
    if new.intent == "unclear" and memory.previous is not None and not new.is_follow_up:
        return Turn(new, extraction, remember=False), memory  # don't lose the earlier query over one garbled message

    if new.is_follow_up and memory.previous is not None:
        understanding = merge(memory.previous, new)
    else:
        understanding = new
        memory.pinned.clear()
    memory.previous = understanding
    return Turn(understanding, extraction), memory


def record(memory: MemoryState, turn: Turn, result: ResolvedQuery) -> MemoryState:
    """Remember the question we're asking (if any) for the next turn."""
    if not turn.remember:
        return memory
    return memory.model_copy(update={"pending": result.clarification if result.status == "clarify" else None})


class Conversation:
    """One user's chat session without the graph (understand + resolve only)."""

    def __init__(self, ctx: UserContext, extractor: Extractor = extract) -> None:
        self.ctx = ctx
        self._extract = extractor
        self.memory = MemoryState()
        self.last_extraction: Extraction | None = None  # for --debug

    async def handle(self, message: str) -> ResolvedQuery:
        """Understand and resolve one user message in the context of the conversation."""
        turn, self.memory = await understand(self.memory, message, self.ctx, self._extract)
        self.last_extraction = turn.extraction
        result = await resolve_query(turn.understanding, self.ctx, self.memory.pinned)
        self.memory = record(self.memory, turn, result)
        return result
