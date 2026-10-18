"""Small cache interface for service results, swappable for Redis later."""

from typing import Any, Protocol


class Cache(Protocol):
    """Key/value cache. Values must be JSON-serializable so a Redis backend can store them."""

    def get(self, key: str) -> Any | None: ...

    def set(self, key: str, value: Any) -> None: ...


class InMemoryCache:
    """Process-local dict cache with no expiry."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str) -> Any | None:
        return self._data.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def clear(self) -> None:
        self._data.clear()
