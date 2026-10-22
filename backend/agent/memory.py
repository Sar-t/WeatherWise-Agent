"""Conversation state across turns: follow-up merging and answers to clarification questions."""

import re
from collections.abc import Awaitable, Callable

from backend.agent.extraction import Extraction, extract
from backend.agent.resolution import option_label, place_key, resolve_query
from backend.agent.schemas import QueryUnderstanding, ResolvedQuery, UserContext
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


class Conversation:
    """One user's chat session. Keeps the previous slots and any pending clarification."""

    def __init__(self, ctx: UserContext, extractor: Extractor = extract) -> None:
        self.ctx = ctx
        self._extract = extractor
        self.previous: QueryUnderstanding | None = None
        self.pending: ResolvedQuery | None = None  # last result, if it asked a question
        self.pinned: dict[str, Location] = {}  # typed place name -> chosen Location
        self.last_extraction: Extraction | None = None  # for --debug

    async def handle(self, message: str) -> ResolvedQuery:
        """Understand and resolve one user message in the context of the conversation."""
        self.last_extraction = None
        clarification = self.pending.clarification if self.pending else None

        if clarification and clarification.reason == "ambiguous_location" and self.previous is not None:
            choice = match_option(message, clarification.options, clarification.location_query or "",
                                  self.ctx.preferred_country)
            if choice is not None:  # answered without needing the LLM
                self.pinned[place_key(clarification.location_query or "")] = choice
                return await self._resolve(self.previous)

        extraction = await self._extract(
            message, self.ctx, previous=self.previous,
            pending_question=clarification.question if clarification else None,
        )
        self.last_extraction = extraction
        new = extraction.understanding
        if new.intent == "unclear" and self.previous is not None and not new.is_follow_up:
            return await resolve_query(new, self.ctx)  # don't lose the earlier query over one garbled message

        if new.is_follow_up and self.previous is not None:
            understanding = merge(self.previous, new)
        else:
            understanding = new
            self.pinned.clear()
        self.previous = understanding
        return await self._resolve(understanding)

    async def _resolve(self, understanding: QueryUnderstanding) -> ResolvedQuery:
        result = await resolve_query(understanding, self.ctx, self.pinned)
        self.pending = result if result.status == "clarify" else None
        return result

