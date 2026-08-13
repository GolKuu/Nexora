"""Page cache for the browser agent (§24).

A page that was read successfully a moment ago is not read again. The cache
keeps the URL, the content hash, when it was fetched and the parsed result, and
expires per kind of page (a catalogue changes slower than a bond's trades).

This is deliberately in-process: it protects kase.kz from us, it is not a
distributed data store. Anything worth keeping goes to the database.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Page kind -> setting holding its TTL.
_TTL_BY_KIND = {
    "catalog": "BROWSER_CACHE_TTL_CATALOG_S",
    "bond": "BROWSER_CACHE_TTL_BOND_S",
    "issuer": "BROWSER_CACHE_TTL_ISSUER_S",
}


def ttl_for(kind: str) -> float:
    attr = _TTL_BY_KIND.get(kind)
    if attr is None:
        return settings.BROWSER_CACHE_TTL_DEFAULT_S
    return float(getattr(settings, attr))


@dataclass(slots=True)
class CacheEntry:
    url: str
    kind: str
    content_hash: str | None
    fetched_at: float
    expires_at: float
    value: Any

    @property
    def age_seconds(self) -> float:
        return time.time() - self.fetched_at

    @property
    def expired(self) -> bool:
        return time.time() >= self.expires_at


class BrowserPageCache:
    def __init__(self) -> None:
        self._entries: dict[str, CacheEntry] = {}
        self.hits = 0
        self.misses = 0

    @staticmethod
    def _key(url: str, kind: str, variant: str | None = None) -> str:
        return f"{kind}::{url}::{variant or ''}"

    def get(
        self, url: str, kind: str = "default", variant: str | None = None
    ) -> CacheEntry | None:
        entry = self._entries.get(self._key(url, kind, variant))
        if entry is None:
            self.misses += 1
            return None
        if entry.expired:
            self._entries.pop(self._key(url, kind, variant), None)
            self.misses += 1
            return None
        self.hits += 1
        logger.debug("browser cache hit url=%s kind=%s age=%.0fs", url, kind, entry.age_seconds)
        return entry

    def put(
        self,
        url: str,
        value: Any,
        *,
        kind: str = "default",
        content_hash: str | None = None,
        variant: str | None = None,
        ttl: float | None = None,
    ) -> CacheEntry:
        now = time.time()
        entry = CacheEntry(
            url=url,
            kind=kind,
            content_hash=content_hash,
            fetched_at=now,
            expires_at=now + (ttl if ttl is not None else ttl_for(kind)),
            value=value,
        )
        self._entries[self._key(url, kind, variant)] = entry
        return entry

    def invalidate(self, url: str | None = None) -> int:
        """Drop one URL's entries, or everything when ``url`` is None."""
        if url is None:
            count = len(self._entries)
            self._entries.clear()
            return count
        keys = [k for k in self._entries if f"::{url}::" in k]
        for key in keys:
            self._entries.pop(key, None)
        return len(keys)

    def stats(self) -> dict:
        return {
            "entries": len(self._entries),
            "hits": self.hits,
            "misses": self.misses,
        }


page_cache = BrowserPageCache()
