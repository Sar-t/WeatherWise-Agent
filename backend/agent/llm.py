"""Chat model setup (provider-swappable via init_chat_model) and free-tier rate-limit backoff."""

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.exceptions import ModelRateLimitError
from langchain_core.language_models import BaseChatModel

from backend.core.config import Settings, get_settings
from backend.services.errors import WeatherWiseError

log = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0  # waits 2 s, 4 s, 8 s

T = TypeVar("T")
_sleep = asyncio.sleep  # module-level so tests can replace it


class LLMConfigError(WeatherWiseError):
    """The LLM is not configured (missing model name or API key)."""


class LLMRateLimitError(WeatherWiseError):
    """The provider kept rejecting requests for rate/quota reasons after all retries."""


def get_llm(settings: Settings | None = None) -> BaseChatModel:
    """Build the chat model from settings. Raises LLMConfigError if it can't be configured."""
    settings = settings or get_settings()
    _configure_tracing(settings)
    if not settings.llm_model:
        raise LLMConfigError(_missing_model_message(settings))

    kwargs: dict[str, Any] = {"temperature": settings.llm_temperature, "max_retries": 1}  # we retry ourselves
    if settings.llm_provider == "google_genai":
        key = settings.google_api_key.get_secret_value()
        if not key:
            raise LLMConfigError("GOOGLE_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey.")
        kwargs["google_api_key"] = key
    return init_chat_model(settings.llm_model, model_provider=settings.llm_provider, **kwargs)


async def call_with_backoff(
    call: Callable[[], Awaitable[T]], sleep: Callable[[float], Awaitable[None]] | None = None
) -> T:
    """Run `call`, retrying rate-limit errors with exponential backoff (MAX_RETRIES times)."""
    sleep = sleep or _sleep
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await call()
        except Exception as exc:
            if not is_rate_limit(exc):
                raise
            if "PerDay" in str(exc):  # a daily quota won't reset during a backoff
                raise LLMRateLimitError(
                    "The LLM's free-tier daily quota for this model is used up (429 RESOURCE_EXHAUSTED). "
                    "It resets within 24 hours; until then set LLM_MODEL to another model, which has its own quota."
                ) from None
            if attempt == MAX_RETRIES:
                raise LLMRateLimitError(
                    f"The LLM's free-tier rate limit was hit (429 RESOURCE_EXHAUSTED) and {MAX_RETRIES} retries "
                    "did not help. Wait a minute and try again, or use a key with a higher quota."
                ) from None
            delay = BACKOFF_BASE_S * 2**attempt
            log.warning("llm rate limited, backing off", extra={"attempt": attempt + 1, "delay_s": delay})
            await sleep(delay)
    raise AssertionError("unreachable")


def is_rate_limit(exc: BaseException) -> bool:
    """True for 429 / RESOURCE_EXHAUSTED errors from any provider."""
    return (
        isinstance(exc, ModelRateLimitError)
        or getattr(exc, "code", None) == 429
        or "RESOURCE_EXHAUSTED" in str(exc)
    )


def list_flash_models(api_key: str) -> list[str]:
    """Gemini Flash models this key can call for text generation."""
    from google import genai

    client = genai.Client(api_key=api_key)
    names = [
        m.name.removeprefix("models/")
        for m in client.models.list()
        if "generateContent" in (m.supported_actions or []) and "flash" in m.name
    ]
    return sorted(names)


def suggest_flash_model(names: list[str]) -> str | None:
    """Prefer the newest stable 'gemini-X.Y-flash'; else any non-preview Flash; else the first."""
    stable = [n for n in names if re.fullmatch(r"gemini-\d+(\.\d+)?-flash", n)]
    if stable:
        return max(stable, key=lambda n: [int(p) for p in re.findall(r"\d+", n)])
    plain = [n for n in names if "preview" not in n and "exp" not in n]
    return (plain or names or [None])[0]


def _missing_model_message(settings: Settings) -> str:
    base = "LLM_MODEL is not set in .env."
    key = settings.google_api_key.get_secret_value()
    if settings.llm_provider != "google_genai" or not key:
        return f"{base} Set it to a model name for provider {settings.llm_provider!r}."
    try:
        names = list_flash_models(key)
    except Exception as exc:  # listing is best-effort help, never the real error
        return f"{base} (Could not list models for your key: {type(exc).__name__}.)"
    suggestion = suggest_flash_model(names)
    listing = "\n".join(f"  - {n}" for n in names) or "  (none found)"
    return f"{base} Flash models your key can use:\n{listing}\nSuggested: LLM_MODEL={suggestion}"


def _configure_tracing(settings: Settings) -> None:
    """LangChain reads LangSmith config from the environment; mirror .env there when enabled."""
    if not settings.langsmith_tracing:
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
    if key := settings.langsmith_api_key.get_secret_value():
        os.environ["LANGSMITH_API_KEY"] = key
