"""Shared HTTP handling for external APIs (Geoapify, OpenWeather, ...).

Query parameters often carry API keys, so they are never logged or put in error messages.
"""

import logging
import ssl
import time
from functools import lru_cache
from typing import Any

import certifi
import httpx

from backend.services.errors import UpstreamAPIError

log = logging.getLogger(__name__)
# httpx logs every request URL at INFO, query string (and so API keys) included.
for _noisy in ("httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

TIMEOUT_S = 10.0
RETRIES = 1  # extra attempts after a timeout or 5xx


async def get_json(provider: str, url: str, params: dict[str, Any]) -> Any:
    """GET `url` with `params` and return the decoded JSON body.

    Retries once on timeout or 5xx. Every failure becomes an UpstreamAPIError naming
    `provider`; bad keys (401/403) and rate limits (429) get their own messages.
    """
    path = httpx.URL(url).path
    async with httpx.AsyncClient(timeout=TIMEOUT_S, verify=_ssl_context()) as client:
        for attempt in range(1, RETRIES + 2):
            last_attempt = attempt == RETRIES + 1
            started = time.perf_counter()
            try:
                response = await client.get(url, params=params)
            except httpx.TimeoutException:
                log.warning("upstream timeout", extra={"provider": provider, "path": path, "attempt": attempt})
                if last_attempt:
                    raise UpstreamAPIError(f"{provider} request timed out after {attempt} attempts.") from None
                continue
            except httpx.HTTPError as exc:
                # `from None`: httpx exceptions can include the full URL, API key and all.
                raise UpstreamAPIError(f"{provider} request failed: {type(exc).__name__}.") from None

            log.info(
                "upstream response",
                extra={
                    "provider": provider,
                    "path": path,
                    "status": response.status_code,
                    "attempt": attempt,
                    "ms": round((time.perf_counter() - started) * 1000),
                },
            )
            if response.status_code >= 500 and not last_attempt:
                continue
            return _decode(provider, response)
    raise AssertionError("unreachable")


@lru_cache(maxsize=1)
def _ssl_context() -> ssl.SSLContext:
    """Build the TLS context once; loading the CA bundle per request costs ~0.3 s."""
    return ssl.create_default_context(cafile=certifi.where())


def _decode(provider: str, response: httpx.Response) -> Any:
    status = response.status_code
    if status in (401, 403):
        raise UpstreamAPIError(f"{provider} rejected the API key (HTTP {status}). Check the key in .env.", status)
    if status == 429:
        raise UpstreamAPIError(f"{provider} rate limit exceeded (HTTP 429). Try again later.", status)
    if status >= 400:
        raise UpstreamAPIError(f"{provider} request failed (HTTP {status}).", status)
    try:
        return response.json()
    except ValueError:
        raise UpstreamAPIError(f"{provider} returned a response that is not valid JSON.", status) from None
