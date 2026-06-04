"""In-memory TTL cache for fast /get payloads."""

from __future__ import annotations

import time
from typing import Generic, TypeVar

V = TypeVar("V")


class TtlCache(Generic[V]):
    def __init__(self, ttl: float, *, maxsize: int = 512) -> None:
        self._ttl = ttl
        self._maxsize = maxsize
        self._store: dict[str, tuple[float, V]] = {}

    def get(self, key: str) -> V | None:
        hit = self._store.get(key)
        if not hit:
            return None
        if time.time() - hit[0] > self._ttl:
            self._store.pop(key, None)
            return None
        return hit[1]

    def delete(self, key: str) -> None:
        self._store.pop(key, None)

    def set(self, key: str, value: V) -> None:
        if len(self._store) >= self._maxsize:
            oldest = min(self._store.items(), key=lambda x: x[1][0])[0]
            self._store.pop(oldest, None)
        self._store[key] = (time.time(), value)
