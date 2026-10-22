import os

import pytest
from langchain_core.exceptions import ModelRateLimitError
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import GoogleRateLimitError

from backend.agent import llm
from backend.agent.llm import (
    LLMConfigError,
    LLMRateLimitError,
    call_with_backoff,
    get_llm,
    is_rate_limit,
    suggest_flash_model,
)
from backend.core.config import Settings

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def flaky(*outcomes):
    """An async call that raises or returns each outcome in turn."""
    queue = list(outcomes)
    calls = []

    async def call():
        calls.append(1)
        outcome = queue.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    call.calls = calls
    return call


def settings(**overrides) -> Settings:
    base = {"llm_model": "gemini-x-flash", "google_api_key": "test-google-key", "_env_file": None}
    return Settings(**(base | overrides))


# --- backoff ---------------------------------------------------------------


async def test_backoff_retries_rate_limits_then_succeeds() -> None:
    sleeps = Sleeps()
    call = flaky(ModelRateLimitError("429"), GoogleRateLimitError("429 RESOURCE_EXHAUSTED"), "ok")
    assert await call_with_backoff(call, sleep=sleeps) == "ok"
    assert sleeps.delays == [2.0, 4.0]


async def test_backoff_gives_up_after_three_retries_with_clear_error() -> None:
    sleeps = Sleeps()
    call = flaky(*[ModelRateLimitError("429 RESOURCE_EXHAUSTED")] * 4)
    with pytest.raises(LLMRateLimitError, match="free-tier rate limit") as exc_info:
        await call_with_backoff(call, sleep=sleeps)
    assert len(call.calls) == 4
    assert sleeps.delays == [2.0, 4.0, 8.0]
    assert exc_info.value.__suppress_context__


async def test_daily_quota_fails_fast_with_clear_error() -> None:
    sleeps = Sleeps()
    daily = GoogleRateLimitError("429 RESOURCE_EXHAUSTED. {'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier'}")
    call = flaky(daily, "never reached")
    with pytest.raises(LLMRateLimitError, match="daily quota"):
        await call_with_backoff(call, sleep=sleeps)
    assert (len(call.calls), sleeps.delays) == (1, [])


async def test_other_errors_are_not_retried() -> None:
    sleeps = Sleeps()
    call = flaky(ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        await call_with_backoff(call, sleep=sleeps)
    assert sleeps.delays == []


class CodedError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


@pytest.mark.parametrize(
    "exc, expected",
    [
        (ModelRateLimitError("x"), True),
        (GoogleRateLimitError("x"), True),
        (RuntimeError("429 RESOURCE_EXHAUSTED. Quota exceeded"), True),
        (CodedError(429), True),
        (CodedError(500), False),
        (RuntimeError("503 UNAVAILABLE"), False),
    ],
)
def test_is_rate_limit(exc: Exception, expected: bool) -> None:
    assert is_rate_limit(exc) is expected


# --- model setup -----------------------------------------------------------


def test_get_llm_builds_gemini_with_settings() -> None:
    model = get_llm(settings(llm_temperature=0.0))
    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.model.endswith("gemini-x-flash")
    assert model.temperature == 0.0
    assert model.max_retries == 1  # our backoff does the retrying


def test_missing_model_lists_flash_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "list_flash_models", lambda key: ["gemini-2.5-flash", "gemini-3.7-flash", "gemini-3.7-flash-lite"])
    with pytest.raises(LLMConfigError) as exc_info:
        get_llm(settings(llm_model=""))
    message = str(exc_info.value)
    assert "LLM_MODEL is not set" in message
    assert "gemini-2.5-flash" in message and "Suggested: LLM_MODEL=gemini-3.7-flash" in message
    assert "test-google-key" not in message


def test_missing_model_survives_listing_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(key: str) -> list[str]:
        raise ConnectionError("offline")

    monkeypatch.setattr(llm, "list_flash_models", fail)
    with pytest.raises(LLMConfigError, match="Could not list models"):
        get_llm(settings(llm_model=""))


def test_missing_google_key() -> None:
    with pytest.raises(LLMConfigError, match="GOOGLE_API_KEY"):
        get_llm(settings(google_api_key=""))


@pytest.mark.parametrize(
    "names, expected",
    [
        (["gemini-2.5-flash", "gemini-3.8-flash", "gemini-3.10-flash-lite", "gemini-flash-latest"], "gemini-3.8-flash"),
        (["gemini-2.5-flash", "gemini-10.0-flash"], "gemini-10.0-flash"),
        (["gemini-3-flash-preview", "gemini-flash-latest"], "gemini-flash-latest"),
        ([], None),
    ],
)
def test_suggest_flash_model(names: list[str], expected: str | None) -> None:
    assert suggest_flash_model(names) == expected


def test_tracing_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LANGSMITH_TRACING", raising=False)
    get_llm(settings())
    assert "LANGSMITH_TRACING" not in os.environ


def test_tracing_enabled_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"):
        monkeypatch.setenv(var, "")  # registers the original value so get_llm's writes are undone
        monkeypatch.delenv(var)
    get_llm(settings(langsmith_tracing=True, langsmith_api_key="ls-key", langsmith_project="ww-test"))
    assert (os.environ["LANGSMITH_TRACING"], os.environ["LANGSMITH_PROJECT"]) == ("true", "ww-test")
    assert os.environ["LANGSMITH_API_KEY"] == "ls-key"
