"""Incremental Tengrinews RSS collector.

Only feed metadata and a bounded extract are retained.  Tengrinews is never
used as a price source.
"""

from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import html
import re
from xml.etree import ElementTree

import httpx

from app.collectors.news import NewsSourceItem, NewsSourceProvider


class TengrinewsCollector(NewsSourceProvider):
    name = "tengrinews"
    DEFAULT_URL = "https://tengrinews.kz/news.rss"

    def __init__(self, client: httpx.AsyncClient | None = None, *, feed_url: str = DEFAULT_URL, max_extract_chars: int = 700):
        self.client = client
        self.feed_url = feed_url
        self.max_extract_chars = max_extract_chars

    @staticmethod
    def _clean(value: str | None) -> str:
        text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
        return " ".join(text.split())

    def parse(self, payload: str) -> list[NewsSourceItem]:
        root = ElementTree.fromstring(payload)
        output: list[NewsSourceItem] = []
        for node in root.findall(".//item"):
            title = self._clean(node.findtext("title"))
            url = (node.findtext("link") or "").strip()
            raw_date = node.findtext("pubDate")
            if not title or not url or not raw_date:
                continue
            try:
                published = parsedate_to_datetime(raw_date)
            except (TypeError, ValueError):
                published = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            category = self._clean(node.findtext("category")) or None
            extract = self._clean(node.findtext("description"))[: self.max_extract_chars] or None
            output.append(NewsSourceItem(self.name, url, title, published, category, extract, "ru", 0.75))
        return output

    async def fetch_new(self, *, since: datetime | None = None) -> list[NewsSourceItem]:
        if self.client is not None:
            response = await self.client.get(self.feed_url)
        else:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True, headers={"User-Agent": "Nexora-News/1.0"}) as client:
                response = await client.get(self.feed_url)
        response.raise_for_status()
        items = self.parse(response.text)
        return [item for item in items if since is None or item.published_at > since]


__all__ = ["TengrinewsCollector"]
