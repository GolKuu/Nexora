"""Extensible, copyright-conscious news source contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class NewsSourceItem:
    source: str
    url: str
    title: str
    published_at: datetime
    section: str | None = None
    short_text: str | None = None
    language: str | None = None
    source_confidence: float = 0.7


class NewsSourceProvider(ABC):
    """Providers return metadata and a short extract, never bulk article copies."""

    name: str

    @abstractmethod
    async def fetch_new(self, *, since: datetime | None = None) -> list[NewsSourceItem]: ...


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith("utm_") and k.lower() not in {"ref", "source", "fbclid"}]
    path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def normalize_title(value: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", value.casefold(), flags=re.UNICODE).split())


def article_fingerprint(source: str, canonical_url: str, title: str, published_at: datetime) -> str:
    stable = f"{source.casefold()}|{canonical_url or normalize_title(title) + '|' + published_at.isoformat()}"
    return sha256(stable.encode("utf-8")).hexdigest()


__all__ = ["NewsSourceItem", "NewsSourceProvider", "article_fingerprint", "canonicalize_url", "normalize_title"]

