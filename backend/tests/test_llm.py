import os

import pytest
from langchain_core.exceptions import ModelRateLimitError
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_google_genai.chat_models import GoogleRateLimitError

from backend.agent import llm
from backend.agent.llm import (
    LLMConfigError,
    LLMDailyQuotaError,
    LLMRateLimitError,
    LLMUnavailableError,
    call_with_backoff,
    get_llm,
    is_overloaded,
    is_rate_limit,
    retry_delay,
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


class CodedError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


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
    with pytest.raises(LLMDailyQuotaError, match="daily quota"):
        await call_with_backoff(call, sleep=sleeps)
    assert (len(call.calls), sleeps.delays) == (1, [])


# Shapes of Gemini's per-minute 429, as langchain-google-genai stringifies it.
PER_MINUTE = ("429 RESOURCE_EXHAUSTED. {'error': {'details': [{'violations': [{'quotaId': "
              "'GenerateRequestsPerMinutePerProjectPerModel-FreeTier', 'quotaValue': '5'}]}, "
              "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '%ss'}]}}")
DAILY = ("429 RESOURCE_EXHAUSTED. {'error': {'details': [{'violations': [{'quotaId': "
         "'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaValue': '20'}]}, "
         "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '77s'}]}}")


@pytest.mark.parametrize("text, expected", [
    (PER_MINUTE % "23", 23.0), (PER_MINUTE % "7.5", 7.5), ("Please retry in 12.3s.", 12.3), ("429 RESOURCE_EXHAUSTED", None),
])
def test_retry_delay_is_read_from_the_error(text: str, expected: float | None) -> None:
    assert retry_delay(GoogleRateLimitError(text)) == expected


async def test_provider_retry_delay_replaces_the_default_backoff() -> None:
    sleeps = Sleeps()
    call = flaky(GoogleRateLimitError(PER_MINUTE % "23"), ModelRateLimitError("429"), "ok")
    assert await call_with_backoff(call, sleep=sleeps) == "ok"
    assert sleeps.delays == [23.0, 4.0]  # the hint, then the default for a hint-less error


async def test_retry_delay_longer_than_a_minute_fails_instead_of_waiting() -> None:
    sleeps = Sleeps()
    call = flaky(GoogleRateLimitError(PER_MINUTE % "90"), "never reached")
    with pytest.raises(LLMRateLimitError, match="asks to wait 90 s"):
        await call_with_backoff(call, sleep=sleeps)
    assert sleeps.delays == []


async def test_daily_quota_ignores_its_retry_delay() -> None:
    sleeps = Sleeps()
    with pytest.raises(LLMDailyQuotaError):
        await call_with_backoff(flaky(GoogleRateLimitError(DAILY)), sleep=sleeps)
    assert sleeps.delays == []


# --- model fallback on a used-up daily quota -----------------------------------


class FakeModel:
    def __init__(self, name: str, outcomes: list) -> None:
        self.name, self.outcomes, self.calls = name, list(outcomes), 0

    async def ainvoke(self, _messages):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


@pytest.fixture
def fake_models(monkeypatch: pytest.MonkeyPatch):
    """get_llm returns a scripted FakeModel per model name; the exhausted-model memory starts empty."""
    models: dict[str, FakeModel] = {}
    clock = {"t": 1000.0}

    def fake_get_llm(role, settings=None, model=None):
        if model not in models:
            raise LLMConfigError(f"{model} not scripted")
        return models[model]

    monkeypatch.setattr(llm, "get_llm", fake_get_llm)
    monkeypatch.setattr(llm, "_exhausted", {})
    monkeypatch.setattr(llm, "_clock", lambda: clock["t"])
    monkeypatch.setattr(llm, "_sleep", Sleeps())
    return models, clock


def test_model_chain_order() -> None:
    s = settings(llm_model="base", extraction_model="ex", answer_model="ans")
    assert llm.model_chain("answer", s) == ["ans", "base", "ex"]
    assert llm.model_chain("extraction", s) == ["ex", "base", "ans"]
    assert llm.model_chain("answer", settings(llm_model="base")) == ["base"]
    assert llm.model_chain("answer", settings(llm_model="same", answer_model="same")) == ["same"]


async def test_daily_quota_switches_to_the_next_configured_model(fake_models) -> None:
    models, _ = fake_models
    models["ans"] = FakeModel("ans", [GoogleRateLimitError(DAILY)])
    models["base"] = FakeModel("base", ["from base"])
    s = settings(llm_model="base", answer_model="ans")

    assert await llm.invoke("answer", lambda m: m.ainvoke([]), settings=s) == "from base"
    assert (models["ans"].calls, models["base"].calls) == (1, 1)  # no retries on the exhausted model

    models["base"].outcomes.append("again")
    assert await llm.invoke("answer", lambda m: m.ainvoke([]), settings=s) == "again"
    assert models["ans"].calls == 1  # remembered as exhausted: skipped, not called again


async def test_exhausted_model_is_tried_again_after_the_ttl(fake_models) -> None:
    models, clock = fake_models
    models["ans"] = FakeModel("ans", [GoogleRateLimitError(DAILY), "fresh again"])
    models["base"] = FakeModel("base", ["from base"])
    s = settings(llm_model="base", answer_model="ans")
    await llm.invoke("answer", lambda m: m.ainvoke([]), settings=s)
    clock["t"] += llm.EXHAUSTED_TTL_S
    assert await llm.invoke("answer", lambda m: m.ainvoke([]), settings=s) == "fresh again"


async def test_daily_quota_on_every_model_fails_fast_as_busy(fake_models) -> None:
    models, _ = fake_models
    models["ans"] = FakeModel("ans", [GoogleRateLimitError(DAILY)])
    models["base"] = FakeModel("base", [GoogleRateLimitError(DAILY)])
    s = settings(llm_model="base", answer_model="ans")
    with pytest.raises(LLMDailyQuotaError, match="ans, base"):
        await llm.invoke("answer", lambda m: m.ainvoke([]), settings=s)
    with pytest.raises(LLMDailyQuotaError):  # both remembered: no call at all
        await llm.invoke("answer", lambda m: m.ainvoke([]), settings=s)
    assert (models["ans"].calls, models["base"].calls) == (1, 1)
    assert issubclass(LLMDailyQuotaError, LLMRateLimitError)  # the graph maps it to "assistant is busy"


async def test_no_switch_without_another_model_or_for_per_minute_limits(fake_models) -> None:
    models, _ = fake_models
    models["base"] = FakeModel("base", [GoogleRateLimitError(DAILY)])
    with pytest.raises(LLMDailyQuotaError):
        await llm.invoke("answer", lambda m: m.ainvoke([]), settings=settings(llm_model="base"))

    models["ans"] = FakeModel("ans", [ModelRateLimitError("429")] * 4)
    models["base"].outcomes.append("not used")
    with pytest.raises(LLMRateLimitError, match="retries did not help"):
        await llm.invoke("answer", lambda m: m.ainvoke([]), settings=settings(llm_model="base", answer_model="ans"))
    assert models["base"].calls == 1  # a per-minute limit doesn't switch models


async def test_unusable_fallback_is_skipped_but_primary_config_errors_raise(fake_models) -> None:
    models, _ = fake_models
    models["ans"] = FakeModel("ans", [GoogleRateLimitError(DAILY)])
    models["ex"] = FakeModel("ex", ["from ex"])  # "base" is not scripted: get_llm raises for it
    s = settings(llm_model="base", answer_model="ans", extraction_model="ex")
    assert await llm.invoke("answer", lambda m: m.ainvoke([]), settings=s) == "from ex"
    with pytest.raises(LLMConfigError):
        await llm.invoke("extraction", lambda m: m.ainvoke([]), settings=settings(llm_model="nope"))


async def test_graph_turns_daily_quota_into_the_busy_message() -> None:
    from backend.agent.graph import BUSY_TEXT, _error_response
    from backend.agent.schemas import AgentDebug

    response = _error_response(LLMDailyQuotaError("daily quota used up"), AgentDebug())
    assert response.text == BUSY_TEXT and response.kind == "error"


async def test_overload_503_is_retried_then_succeeds() -> None:
    sleeps = Sleeps()
    call = flaky(CodedError(503), RuntimeError("503 UNAVAILABLE. high demand"), "ok")
    assert await call_with_backoff(call, sleep=sleeps) == "ok"
    assert sleeps.delays == [2.0, 4.0]


async def test_persistent_overload_raises_unavailable() -> None:
    sleeps = Sleeps()
    call = flaky(*[CodedError(503)] * 4)
    with pytest.raises(LLMUnavailableError, match="overloaded"):
        await call_with_backoff(call, sleep=sleeps)
    assert sleeps.delays == [2.0, 4.0, 8.0]


@pytest.mark.parametrize("exc, expected", [(CodedError(503), True), (RuntimeError("503 UNAVAILABLE"), True),
                                           (CodedError(500), False), (ModelRateLimitError("429"), False)])
def test_is_overloaded(exc: Exception, expected: bool) -> None:
    assert is_overloaded(exc) is expected


async def test_other_errors_are_not_retried() -> None:
    sleeps = Sleeps()
    call = flaky(ValueError("boom"))
    with pytest.raises(ValueError, match="boom"):
        await call_with_backoff(call, sleep=sleeps)
    assert sleeps.delays == []


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
    model = get_llm(settings=settings())
    assert isinstance(model, ChatGoogleGenerativeAI)
    assert model.model.endswith("gemini-x-flash")
    assert model.temperature == 0.0
    assert model.max_retries == 1  # our backoff does the retrying


def test_roles_default_to_llm_model_with_their_own_temperature() -> None:
    extraction = get_llm("extraction", settings=settings())
    answer = get_llm("answer", settings=settings())
    assert extraction.model.endswith("gemini-x-flash") and answer.model.endswith("gemini-x-flash")
    assert (extraction.temperature, answer.temperature) == (0.0, 0.3)


def test_role_models_override_llm_model() -> None:
    s = settings(extraction_model="gemini-a-flash", answer_model="gemini-b-flash", answer_temperature=0.5)
    assert get_llm("extraction", settings=s).model.endswith("gemini-a-flash")
    answer = get_llm("answer", settings=s)
    assert answer.model.endswith("gemini-b-flash") and answer.temperature == 0.5


def test_missing_model_lists_flash_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm, "list_flash_models", lambda key: ["gemini-2.5-flash", "gemini-3.7-flash", "gemini-3.7-flash-lite"])
    with pytest.raises(LLMConfigError) as exc_info:
        get_llm(settings=settings(llm_model=""))
    message = str(exc_info.value)
    assert "LLM_MODEL is not set" in message
    assert "gemini-2.5-flash" in message and "Suggested: LLM_MODEL=gemini-3.7-flash" in message
    assert "test-google-key" not in message


def test_missing_model_survives_listing_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(key: str) -> list[str]:
        raise ConnectionError("offline")

    monkeypatch.setattr(llm, "list_flash_models", fail)
    with pytest.raises(LLMConfigError, match="Could not list models"):
        get_llm(settings=settings(llm_model=""))


def test_missing_google_key() -> None:
    with pytest.raises(LLMConfigError, match="GOOGLE_API_KEY"):
        get_llm(settings=settings(google_api_key=""))


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
    get_llm(settings=settings())
    assert "LANGSMITH_TRACING" not in os.environ


def test_tracing_enabled_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("LANGSMITH_TRACING", "LANGSMITH_API_KEY", "LANGSMITH_PROJECT"):
        monkeypatch.setenv(var, "")  # registers the original value so get_llm's writes are undone
        monkeypatch.delenv(var)
    get_llm(settings=settings(langsmith_tracing=True, langsmith_api_key="ls-key", langsmith_project="ww-test"))
    assert (os.environ["LANGSMITH_TRACING"], os.environ["LANGSMITH_PROJECT"]) == ("true", "ww-test")
    assert os.environ["LANGSMITH_API_KEY"] == "ls-key"


# --- thinking --------------------------------------------------------------


@pytest.mark.parametrize(
    "model, setting, expected",
    [
        ("gemini-3.6-flash", "minimal", {"thinking_level": "minimal"}),
        ("gemini-3.6-flash", "HIGH", {"thinking_level": "high"}),
        ("gemini-2.5-flash", "minimal", {"thinking_budget": 0}),  # 2.5 has no levels: minimal = off
        ("gemini-3.6-flash", "512", {"thinking_budget": 512}),
        ("gemini-2.5-flash", "-1", {"thinking_budget": -1}),  # dynamic
        ("gemini-3.6-flash", "", {}),  # model default
        ("gemini-9.9-flash", "minimal", {"thinking_level": "minimal"}),  # unknown to the profile: passed through
    ],
)
def test_thinking_kwargs(model: str, setting: str, expected: dict) -> None:
    assert llm.thinking_kwargs(model, setting) == expected


@pytest.mark.parametrize(
    "model, setting, message",
    [
        ("gemini-3.6-flash", "fast", "Unknown thinking setting"),
        ("gemini-2.5-flash", "low", "has no thinking levels"),
    ],
)
def test_thinking_kwargs_rejects_bad_settings(model: str, setting: str, message: str) -> None:
    with pytest.raises(LLMConfigError, match=message):
        llm.thinking_kwargs(model, setting)


def test_thinking_ignored_for_other_providers() -> None:
    assert llm.thinking_kwargs("gpt-x", "minimal", provider="openai") == {}


def test_roles_get_their_own_thinking() -> None:
    s = settings(llm_model="gemini-3.6-flash", extraction_thinking="minimal", answer_thinking="low")
    assert get_llm("extraction", settings=s).thinking_level == "minimal"
    assert get_llm("answer", settings=s).thinking_level == "low"
    assert get_llm("answer", settings=settings(llm_model="gemini-2.5-flash")).thinking_budget == 0  # default minimal


def test_built_models_are_reused_per_configuration() -> None:
    s = settings(llm_model="gemini-3.6-flash")
    assert get_llm("answer", settings=s) is get_llm("answer", settings=s)
    assert get_llm("answer", settings=s) is not get_llm("extraction", settings=s)  # different temperature
    assert get_llm("answer", settings=s) is not get_llm("answer", settings=settings(llm_model="gemini-3.7-flash"))
