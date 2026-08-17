from __future__ import annotations

from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.news import EventMarketReaction, MarketEvent, NewsArticle
from app.collectors.news import normalize_title
from app.core.config import settings


class HistoricalEventMatcher:
    def __init__(self, session: Session, *, minimum_sample_size: int | None = None):
        self.session = session; self.minimum_sample_size = minimum_sample_size or settings.NEWS_MINIMUM_ANALOG_SAMPLE

    def match(self, event: MarketEvent, limit: int = 50) -> dict:
        query = select(MarketEvent, EventMarketReaction, NewsArticle).join(EventMarketReaction, EventMarketReaction.event_id == MarketEvent.id).join(NewsArticle, NewsArticle.id == MarketEvent.news_id).where(
            MarketEvent.id != event.id, MarketEvent.event_timestamp < event.event_timestamp, MarketEvent.event_type == event.event_type
        )
        rows = self.session.execute(query.order_by(MarketEvent.event_timestamp.desc()).limit(limit)).all()
        target_article=self.session.get(NewsArticle,event.news_id); target_tokens=set(normalize_title(target_article.title).split()) if target_article else set()
        def similarity(article: NewsArticle) -> float:
            tokens=set(normalize_title(article.title).split())
            return len(tokens & target_tokens)/max(len(tokens | target_tokens),1)
        ranked = sorted(rows, key=lambda row: ((row[0].issuer_id == event.issuer_id) * 3 + (row[0].sector == event.sector) * 2 + (row[0].market_regime == event.market_regime and event.market_regime is not None) + similarity(row[2]) * 2 + (1 - abs(row[0].importance - event.importance))), reverse=True)
        reactions = [row[1] for row in ranked]
        one = [r.return_1d for r in reactions if r.return_1d is not None]
        five = [r.return_5d for r in reactions if r.return_5d is not None]
        abnormal = [r.abnormal_return_1d for r in reactions if r.abnormal_return_1d is not None]
        enough = len(reactions) >= self.minimum_sample_size
        return {"count": len(reactions), "minimum_sample_size": self.minimum_sample_size, "sufficient_sample": enough,
                "message": None if enough else "Недостаточно исторических аналогов",
                "positive_reaction_rate": (sum(v > 0 for v in five) / len(five)) if enough and five else None,
                "negative_reaction_rate": (sum(v < 0 for v in five) / len(five)) if enough and five else None,
                "median_return_1d": median(one) if enough and one else None, "median_return_5d": median(five) if enough and five else None,
                "median_abnormal_return": median(abnormal) if enough and abnormal else None,
                "distribution": {"return_1d": one, "return_5d": five, "abnormal_return_1d": abnormal} if enough else None,
                "event_ids": [row[0].id for row in ranked]}


__all__ = ["HistoricalEventMatcher"]
