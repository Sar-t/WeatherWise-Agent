from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from langchain_core.exceptions import ModelRateLimitError

from backend.agent import llm
from backend.agent.extraction import build_system_prompt, build_user_message, extract
from backend.agent.llm import LLMRateLimitError
from backend.agent.schemas import QueryUnderstanding, UserContext
from backend.services import date_resolver
from backend.tests.agent_fakes import FakeStructuredLLM, invalid, ok

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 18, 13, tzinfo=ZoneInfo("Asia/Kolkata"))  # Sunday
CTX = UserContext(now=NOW)
JAIPUR_TOMORROW = QueryUnderstanding(intent="weather", locations=["Jaipur"], date_expr="tomorrow", time_expr="morning")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    slept: list[float] = []

    async def fake_sleep(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(llm, "_sleep", fake_sleep)
    return slept


# --- prompt ----------------------------------------------------------------


def test_prompt_has_today_in_user_timezone() -> None:
    prompt = build_system_prompt(UserContext(timezone="America/New_York", now=NOW))  # 03:30 in New York
    assert "Today is Sunday, 18 October 2026 (timezone America/New_York)" in prompt
    early = datetime(2026, 10, 18, 2, tzinfo=ZoneInfo("Asia/Kolkata"))  # still Saturday in New York
    assert "Saturday, 17 October 2026" in build_system_prompt(UserContext(timezone="America/New_York", now=early))


def test_prompt_vocabulary_comes_from_resolver(monkeypatch: pytest.MonkeyPatch) -> None:
    prompt = build_system_prompt(CTX)
    for value in date_resolver.DATE_EXPRESSIONS + date_resolver.TIME_EXPRESSIONS:
        assert value in prompt
    # A new resolver value shows up in the prompt without touching the prompt file.
    monkeypatch.setattr("backend.agent.extraction.TIME_EXPRESSIONS", [*date_resolver.TIME_EXPRESSIONS, "golden_hour"])
    assert "golden_hour" in build_system_prompt(CTX)


def test_schema_date_values_match_resolver() -> None:
    allowed = QueryUnderstanding.model_json_schema()["properties"]["date_expr"]["anyOf"][0]["enum"]
    assert allowed == date_resolver.DATE_EXPRESSIONS


def test_prompt_has_hinglish_example() -> None:
    assert "kal subah Jaipur mein baarish hogi?" in build_system_prompt(CTX)


def test_user_message_includes_previous_slots_and_pending_question() -> None:
    text = build_user_message("and Delhi?", previous=JAIPUR_TOMORROW, pending_question="Which city?")
    assert 'Previous query: {"intent": "weather", "locations": ["Jaipur"], "date_expr": "tomorrow"' in text
    assert "You asked the user: Which city?" in text
    assert text.endswith("User: and Delhi?")


# --- extraction ------------------------------------------------------------


async def test_one_llm_call_in_the_normal_case() -> None:
    fake = FakeStructuredLLM(ok(JAIPUR_TOMORROW))
    result = await extract("kal subah Jaipur mein baarish hogi?", CTX, llm=fake)

    assert result.understanding == JAIPUR_TOMORROW
    assert (result.attempts, len(fake.calls)) == (1, 1)
    assert fake.schema is QueryUnderstanding
    system, human = fake.calls[0]
    assert "Today is Sunday, 18 October 2026" in system.content
    assert human.content == "User: kal subah Jaipur mein baarish hogi?"
    assert '"Jaipur"' in result.raw


async def test_invalid_output_is_retried_once() -> None:
    fake = FakeStructuredLLM(invalid(), ok(JAIPUR_TOMORROW))
    result = await extract("weather in jaipur tmrw morning", CTX, llm=fake)
    assert result.understanding == JAIPUR_TOMORROW
    assert (result.attempts, len(fake.calls)) == (2, 2)


async def test_two_invalid_outputs_fall_back_to_unclear() -> None:
    fake = FakeStructuredLLM(invalid(), invalid("not json at all"))
    result = await extract("???", CTX, llm=fake)
    assert result.understanding == QueryUnderstanding(intent="unclear")
    assert result.attempts == 2
    assert result.raw == "not json at all"


async def test_rate_limit_is_backed_off_inside_extraction(_no_real_sleep: list[float]) -> None:
    fake = FakeStructuredLLM(ModelRateLimitError("429 RESOURCE_EXHAUSTED"), ok(JAIPUR_TOMORROW))
    result = await extract("weather in Jaipur tomorrow morning", CTX, llm=fake)
    assert result.understanding == JAIPUR_TOMORROW
    assert _no_real_sleep == [2.0]
    assert result.attempts == 1  # a rate-limit retry is not an invalid-output retry


async def test_persistent_rate_limit_raises_clear_error(_no_real_sleep: list[float]) -> None:
    fake = FakeStructuredLLM(*[ModelRateLimitError("429 RESOURCE_EXHAUSTED")] * 4)
    with pytest.raises(LLMRateLimitError, match="free-tier"):
        await extract("weather in Jaipur", CTX, llm=fake)
    assert _no_real_sleep == [2.0, 4.0, 8.0]


async def test_follow_up_sends_previous_slots() -> None:
    fake = FakeStructuredLLM(ok(QueryUnderstanding(intent="weather", locations=["Delhi"], is_follow_up=True)))
    await extract("and Delhi?", CTX, previous=JAIPUR_TOMORROW, llm=fake)
    assert "Previous query:" in fake.calls[0][1].content
