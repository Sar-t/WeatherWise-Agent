"""Small cache interface for service results, swappable for Redis later."""

import time
from collections.abc import Callable
from typing import Any, Protocol


class Cache(Protocol):
    """Key/value cache. Values must be JSON-serializable so a Redis backend can store them."""

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        """Store `value`; it expires after `ttl_s` seconds, or never if None."""
        ...


class InMemoryCache:
    """Process-local dict cache with optional per-entry expiry."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._data: dict[str, tuple[Any, float | None]] = {}

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expires_at = entry
        if expires_at is not None and self._clock() >= expires_at:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any, ttl_s: float | None = None) -> None:
        expires_at = None if ttl_s is None else self._clock() + ttl_s
        self._data[key] = (value, expires_at)

    def clear(self) -> None:
        self._data.clear()
