"""KASE issuer disclosures as a first-tier news source.

The incremental collector already stores every disclosure KASE publishes for a
covered issuer in ``kase_news_items``: title, publication date, the original
kase.kz URL and the ticker it was found under. Those rows were never reaching
the news feed, because the feed reads the classified ``market_events`` stream.

Rather than a second, parallel news path, this provider republishes the stored
disclosures through the ordinary ``NewsIntelligencePipeline`` - so KASE items
get the same deduplication, clustering, event classification, market-reaction
measurement and impact scoring as every other source, and the "no article
without an original URL" rule holds because a stored row without a URL is
skipped rather than invented.

It reads the database instead of the network: the disclosure fetch already
happened in the documents/catalogue pass, and repeating it would hit KASE twice
for the same bytes.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.collectors.news import NewsSourceItem, NewsSourceProvider
from app.models.incremental import KaseNewsItem

#: KASE publishes the disclosure itself, so it is the primary record rather
#: than a report about one. Matches the tier-1 confidence in `source_tier`.
KASE_SOURCE_CONFIDENCE = 0.98


class KaseDisclosureCollector(NewsSourceProvider):
    """Replays stored KASE disclosures into the news pipeline."""

    name = "kase"

    def __init__(self, session: Session, *, limit: int = 500):
        self.session = session
        self.limit = limit

    @staticmethod
    def _aware(value: datetime) -> datetime:
        """Stored timestamps are naive UTC; the pipeline compares tz-aware."""
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _rows(self, since: datetime | None) -> list[KaseNewsItem]:
        query = select(KaseNewsItem).where(KaseNewsItem.url.is_not(None))
        if since is not None:
            # `since` arrives tz-aware; the column is naive UTC.
            cutoff = since.astimezone(timezone.utc).replace(tzinfo=None)
            query = query.where(KaseNewsItem.publication_date > cutoff)
        query = query.order_by(
            KaseNewsItem.publication_date.desc(), KaseNewsItem.id.desc()
        ).limit(self.limit)
        return list(self.session.execute(query).scalars())

    async def fetch_new(self, *, since: datetime | None = None) -> list[NewsSourceItem]:
        items: list[NewsSourceItem] = []
        for row in self._rows(since):
            title = (row.title or "").strip()
            url = (row.url or "").strip()
            if not title or not url or row.publication_date is None:
                # No fabricated links and no untitled entries in the feed.
                continue
            items.append(
                NewsSourceItem(
                    source=self.name,
                    url=url,
                    title=title,
                    published_at=self._aware(row.publication_date),
                    section=row.ticker or row.issuer_code,
                    # KASE gives a headline, not a body. Inventing a summary
                    # here is exactly the hallucination the product forbids.
                    short_text=None,
                    language="en",
                    source_confidence=KASE_SOURCE_CONFIDENCE,
                )
            )
        return items


__all__ = ["KaseDisclosureCollector", "KASE_SOURCE_CONFIDENCE"]
