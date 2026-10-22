"""Test doubles shared by the agent tests: a fake structured-output LLM and real-fixture candidates."""

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from backend.agent.schemas import QueryUnderstanding
from backend.services import geocoding
from backend.services.models import Location

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
