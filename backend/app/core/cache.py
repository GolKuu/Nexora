"""Tiny cache abstraction.

Redis is optional. When REDIS_URL is not configured we use an in-process TTL
cache, which is entirely sufficient for a single-worker deployment.
"""

from __future__ import annotations

import time
from typing import Any


class TTLCache:
    def __init__(self) -> None:
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at < time.time():
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: float = 60.0) -> None:
        self._data[key] = (time.time() + ttl_seconds, value)

    def invalidate(self, prefix: str = "") -> None:
        for key in [k for k in self._data if k.startswith(prefix)]:
            self._data.pop(key, None)


cache = TTLCache()
