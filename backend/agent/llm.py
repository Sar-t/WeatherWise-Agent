"""Chat model setup (provider-swappable via init_chat_model) and free-tier rate-limit backoff."""

import asyncio
import logging
import os
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypeVar

from langchain.chat_models import init_chat_model
from langchain_core.exceptions import ModelRateLimitError
from langchain_core.language_models import BaseChatModel

from backend.core.config import Settings, get_settings
from backend.services.errors import WeatherWiseError

log = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF_BASE_S = 2.0  # waits 2 s, 4 s, 8 s, unless the provider says how long to wait
MAX_RETRY_DELAY_S = 60.0  # longest provider-suggested wait honoured within one request
EXHAUSTED_TTL_S = 60 * 60  # skip a model whose daily quota ran out for this long

T = TypeVar("T")
_sleep = asyncio.sleep  # module-level so tests can replace it
_clock = time.monotonic  # module-level so tests can replace it
_models: dict[tuple, BaseChatModel] = {}  # built chat models, one per configuration
_exhausted: dict[str, float] = {}  # model -> when its daily quota was found used up


class LLMConfigError(WeatherWiseError):
    """The LLM is not configured (missing model name or API key)."""


class LLMRateLimitError(WeatherWiseError):
    """The provider kept rejecting requests for rate/quota reasons after all retries."""


class LLMDailyQuotaError(LLMRateLimitError):
    """The model's daily quota is used up; retrying today won't help, another model might."""


class LLMUnavailableError(WeatherWiseError):
    """The provider kept reporting it is overloaded (503) after all retries."""


Role = Literal["extraction", "answer"]


def get_llm(role: Role = "extraction", settings: Settings | None = None, model: str | None = None) -> BaseChatModel:
    """Build the chat model for `role` from settings. Raises LLMConfigError if it can't be configured.

    EXTRACTION_MODEL / ANSWER_MODEL override LLM_MODEL for their role; `model` overrides both.
    """
    settings = settings or get_settings()
    _configure_tracing(settings)
    model = model or getattr(settings, f"{role}_model") or settings.llm_model
    if not model:
        raise LLMConfigError(_missing_model_message(settings))

    temperature = getattr(settings, f"{role}_temperature")
    kwargs: dict[str, Any] = {"temperature": temperature, "max_retries": 1}  # we retry ourselves
    kwargs |= thinking_kwargs(model, getattr(settings, f"{role}_thinking"), settings.llm_provider)
    if settings.llm_provider == "google_genai":
        key = settings.google_api_key.get_secret_value()
        if not key:
            raise LLMConfigError("GOOGLE_API_KEY is not set. Get a free key at https://aistudio.google.com/apikey.")
        kwargs["google_api_key"] = key

    # Building a client costs ~0.9 s, and this runs twice per message: reuse one per configuration.
    cache_key = (settings.llm_provider, model, tuple(sorted((k, str(v)) for k, v in kwargs.items())))
    if cache_key not in _models:
        _models[cache_key] = init_chat_model(model, model_provider=settings.llm_provider, **kwargs)
    return _models[cache_key]


THINKING_LEVELS = ("minimal", "low", "medium", "high")


def thinking_kwargs(model: str, thinking: str, provider: str = "google_genai") -> dict[str, Any]:
    """Map a THINKING setting to model kwargs: a level, a token budget, or nothing (model default).

    Gemini 3+ takes `thinking_level`; Gemini 2.5 only takes `thinking_budget`, where "minimal"
    becomes 0 (thinking off). Levels a model's profile doesn't list are rejected up front.
    """
    value = thinking.strip().lower()
    if not value or provider != "google_genai":
        return {}
    if value.lstrip("-").isdigit():
        return {"thinking_budget": int(value)}
    if value not in THINKING_LEVELS:
        raise LLMConfigError(f"Unknown thinking setting {thinking!r}: use {', '.join(THINKING_LEVELS)} or a token budget.")
    if model.removeprefix("models/").startswith("gemini-2."):
        if value == "minimal":
            return {"thinking_budget": 0}
        raise LLMConfigError(f"{model} has no thinking levels; use 'minimal' (off) or a token budget like 1024.")
    levels = _profile_levels(model)
    if levels is not None and value not in levels:
        raise LLMConfigError(f"{model} supports thinking levels {', '.join(levels)}, not {value!r}.")
    return {"thinking_level": value}


def _profile_levels(model: str) -> list[str] | None:
    """Thinking levels from LangChain's bundled model profile, or None if it doesn't say.

    Reads the profile data directly: building a ChatGoogleGenerativeAI just to look costs ~0.9 s.
    """
    try:
        from langchain_google_genai.chat_models import _get_default_model_profile

        return _get_default_model_profile(model.removeprefix("models/")).get("reasoning_effort_levels")
    except Exception:  # private helper; if it moves, skip the up-front check
        return None


def model_chain(role: Role, settings: Settings) -> list[str]:
    """Models to try for `role`, in order: its own, then LLM_MODEL, then the other role's."""
    other: Role = "answer" if role == "extraction" else "extraction"
    names = [getattr(settings, f"{role}_model"), settings.llm_model, getattr(settings, f"{other}_model")]
    return list(dict.fromkeys(n for n in names if n))


async def invoke(role: Role, call: Callable[[BaseChatModel], Awaitable[T]], settings: Settings | None = None) -> T:
    """Run `call(model)` with backoff on the role's model; on a used-up daily quota, switch to the
    next configured model. Models found exhausted are skipped for EXHAUSTED_TTL_S."""
    settings = settings or get_settings()
    chain = model_chain(role, settings)
    if not chain:
        raise LLMConfigError(_missing_model_message(settings))
    fresh = [m for m in chain if _clock() - _exhausted.get(m, float("-inf")) >= EXHAUSTED_TTL_S]
    if not fresh:
        raise LLMDailyQuotaError(_daily_quota_message(chain))
    for i, name in enumerate(fresh):
        try:
            model = get_llm(role, settings, model=name)
        except LLMConfigError:
            if name == chain[0]:
                raise
            log.warning("fallback model unusable, skipping", extra={"model": name, "role": role})
            continue
        try:
            return await call_with_backoff(lambda: call(model))
        except LLMDailyQuotaError:
            _exhausted[name] = _clock()
            log.warning("daily quota used up", extra={"model": name, "role": role,
                                                      "next": fresh[i + 1] if i + 1 < len(fresh) else None})
    raise LLMDailyQuotaError(_daily_quota_message(chain))


def _daily_quota_message(models: list[str]) -> str:
    return (f"The LLM's free-tier daily quota is used up for {', '.join(models)} (429 RESOURCE_EXHAUSTED). "
            "It resets within 24 hours; until then set LLM_MODEL to another model, which has its own quota.")


def retry_delay(exc: BaseException) -> float | None:
    """Seconds the provider asks us to wait, e.g. Gemini's "'retryDelay': '23s'", if it says."""
    text = str(exc)
    match = (re.search(r"""retryDelay['"]?\s*[:=]\s*['"]?(\d+(?:\.\d+)?)s""", text)
             or re.search(r"retry in (\d+(?:\.\d+)?)\s*s", text, re.IGNORECASE))
    return float(match.group(1)) if match else None


def is_daily_quota(exc: BaseException) -> bool:
    """True when a 429 is about the per-day quota (Gemini quotaId ...PerDay...), not per-minute."""
    return "PerDay" in str(exc)


async def call_with_backoff(
    call: Callable[[], Awaitable[T]], sleep: Callable[[float], Awaitable[None]] | None = None
) -> T:
    """Run `call`, retrying rate limits (429) and overloads (503). Waits as long as the provider
    asks when it says, else backs off exponentially. A used-up daily quota is not retried."""
    sleep = sleep or _sleep
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await call()
        except Exception as exc:
            overloaded = is_overloaded(exc)
            if not overloaded and not is_rate_limit(exc):
                raise
            if overloaded and attempt == MAX_RETRIES:
                raise LLMUnavailableError(
                    f"The LLM provider is overloaded (503 UNAVAILABLE) and {MAX_RETRIES} retries did not help. "
                    "Try again in a minute."
                ) from None
            if not overloaded and is_daily_quota(exc):  # a daily quota won't reset during a backoff
                raise LLMDailyQuotaError(
                    "The LLM's free-tier daily quota for this model is used up (429 RESOURCE_EXHAUSTED). "
                    "It resets within 24 hours; until then set LLM_MODEL to another model, which has its own quota."
                ) from None
            if attempt == MAX_RETRIES:
                raise LLMRateLimitError(
                    f"The LLM's free-tier rate limit was hit (429 RESOURCE_EXHAUSTED) and {MAX_RETRIES} retries "
                    "did not help. Wait a minute and try again, or use a key with a higher quota."
                ) from None
            hinted = None if overloaded else retry_delay(exc)
            if hinted is not None and hinted > MAX_RETRY_DELAY_S:
                raise LLMRateLimitError(
                    f"The LLM's free-tier rate limit was hit (429 RESOURCE_EXHAUSTED) and the provider asks to "
                    f"wait {hinted:.0f} s. Try again then."
                ) from None
            delay = hinted if hinted is not None else BACKOFF_BASE_S * 2**attempt
            log.warning(
                "llm busy, backing off",
                extra={"attempt": attempt + 1, "delay_s": delay, "reason": "503" if overloaded else "429"},
            )
            await sleep(delay)
    raise AssertionError("unreachable")


def is_overloaded(exc: BaseException) -> bool:
    """True for 503 / UNAVAILABLE ("high demand") errors."""
    return getattr(exc, "code", None) == 503 or "503 UNAVAILABLE" in str(exc)


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


def message_text(message: Any) -> str:
    """Plain text of a chat message; newer Gemini models return a list of content blocks."""
    content = getattr(message, "content", message)
    if isinstance(content, list):
        return "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in content)
    return str(content)
