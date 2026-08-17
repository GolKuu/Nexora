from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.news import EventCluster, EventMarketReaction, MarketEvent, NewsArticle, NewsImpactScore
from app.services.historical_events import HistoricalEventMatcher
from app.services.stock_service import StockService
from app.core.errors import NotFoundError
from app.models.stock import CorporateAction, Stock


def _reaction(row: EventMarketReaction | None) -> dict | None:
    if row is None: return None
    return {key: getattr(row, key) for key in ("price_before", "return_5m", "return_30m", "return_1h", "return_same_day", "return_1d", "return_5d", "return_20d", "volume_ratio", "volatility_change", "market_return", "sector_return", "abnormal_return_1d", "abnormal_return_5d", "benchmark_id", "formula_version")}


def factual_explanation(event: MarketEvent, reaction: EventMarketReaction | None) -> str:
    if reaction is None or reaction.return_1d is None: return "Для этого события пока недостаточно рыночных данных, поэтому фактическая реакция не рассчитана."
    direction = "выросла" if reaction.return_1d >= 0 else "снизилась"
    text = f"После публикации акция {direction} на {abs(reaction.return_1d) * 100:.1f}% за торговый день"
    if reaction.abnormal_return_1d is not None: text += f", что на {reaction.abnormal_return_1d * 100:+.1f} п.п. отличается от выбранного benchmark"
    return text + ". Это наблюдаемое совпадение во времени, а не доказательство причинности."


class NewsQueryService:
    def __init__(self, session: Session): self.session = session

    def _stock(self, identifier: str): return StockService(self.session).require(identifier)

    def _rows(self, identifier: str, limit: int = 50):
        stock = self._stock(identifier)
        return stock, self.session.execute(select(MarketEvent, NewsArticle, EventMarketReaction, NewsImpactScore).join(NewsArticle, NewsArticle.id == MarketEvent.news_id)
            .outerjoin(EventMarketReaction, EventMarketReaction.event_id == MarketEvent.id).outerjoin(NewsImpactScore, NewsImpactScore.event_id == MarketEvent.id)
            .where(MarketEvent.instrument_id == stock.instrument_id).order_by(MarketEvent.event_timestamp.desc()).limit(limit)).all()

    def events(self, identifier: str, limit: int = 50) -> dict:
        stock, rows = self._rows(identifier, limit)
        items=[]
        for event, article, reaction, impact in rows:
            analogs = HistoricalEventMatcher(self.session).match(event)
            items.append({"id": event.id, "news_id": article.id, "title": article.title, "source": article.source, "source_url": article.source_url,
                "event_type": event.event_type, "event_timestamp": event.event_timestamp, "importance": event.importance, "sentiment": event.sentiment,
                "surprise": event.surprise, "source_confidence": event.source_confidence, "analysis_confidence": event.analysis_confidence,
                "impact_score": impact.value if impact else None, "reaction": _reaction(reaction), "historical_analogs": analogs,
                "explanation": factual_explanation(event, reaction), "marker": marker_type(event.event_type)})
        return {"ticker": stock.instrument.ticker, "items": items, "total": len(items)}

    def news(self, identifier: str, limit: int = 50) -> dict:
        payload=self.events(identifier,limit)
        payload["items"]=[{"id": x["news_id"], "event_id": x["id"], "title":x["title"], "source":x["source"], "source_url":x["source_url"], "published_at":x["event_timestamp"], "event_type":x["event_type"], "importance":x["importance"]} for x in payload["items"]]
        return payload

    def event(self, event_id: int) -> dict:
        row=self.session.execute(select(MarketEvent,NewsArticle,EventMarketReaction,NewsImpactScore).join(NewsArticle,NewsArticle.id==MarketEvent.news_id).outerjoin(EventMarketReaction,EventMarketReaction.event_id==MarketEvent.id).outerjoin(NewsImpactScore,NewsImpactScore.event_id==MarketEvent.id).where(MarketEvent.id==event_id)).one_or_none()
        if row is None: raise NotFoundError(f"Событие не найдено: {event_id}")
        event, article, reaction, impact=row
        return {"id":event.id,"title":article.title,"summary":article.summary,"source":article.source,"source_url":article.source_url,"event_type":event.event_type,"event_timestamp":event.event_timestamp,"issuer_id":event.issuer_id,"instrument_id":event.instrument_id,"importance":event.importance,"sentiment":event.sentiment,"surprise":event.surprise,"entities":event.entities,"impact_score":impact.value if impact else None,"reaction":_reaction(reaction),"historical_analogs":HistoricalEventMatcher(self.session).match(event),"explanation":factual_explanation(event,reaction)}

    def daily_drivers(self, identifier: str) -> dict:
        stock=self._stock(identifier); payload=self.events(identifier,100); items=payload["items"]
        latest_day=max((item["event_timestamp"].date() for item in items),default=None)
        drivers=[item for item in items if latest_day and item["event_timestamp"].date()==latest_day]
        macro_types=("interest_rate","inflation","currency","commodity_change","geopolitics","government_decision")
        macro=[]; sector=[]
        if latest_day:
            macro=[{"id":e.id,"event_type":e.event_type,"event_timestamp":e.event_timestamp,"title":a.title} for e,a in self.session.execute(select(MarketEvent,NewsArticle).join(NewsArticle,NewsArticle.id==MarketEvent.news_id).where(MarketEvent.event_type.in_(macro_types),func.date(MarketEvent.event_timestamp)==latest_day.isoformat()))]
            sector=[{"id":e.id,"event_type":e.event_type,"event_timestamp":e.event_timestamp,"title":a.title} for e,a in self.session.execute(select(MarketEvent,NewsArticle).join(NewsArticle,NewsArticle.id==MarketEvent.news_id).where(MarketEvent.sector==stock.sector,MarketEvent.instrument_id!=stock.instrument_id,func.date(MarketEvent.event_timestamp)==latest_day.isoformat()))]
        corporate=[{"action_type":row.action_type,"event_date":row.event_date,"title":row.title,"source_url":row.source_url} for row in self.session.execute(select(CorporateAction).where(CorporateAction.stock_id==stock.id).order_by(CorporateAction.event_date.desc()).limit(10)).scalars()]
        volume_anomaly=max((item["reaction"]["volume_ratio"] for item in drivers if item["reaction"] and item["reaction"]["volume_ratio"] is not None),default=None)
        benchmark_movement=next((item["reaction"]["market_return"] for item in drivers if item["reaction"] and item["reaction"]["market_return"] is not None),None)
        return {"ticker":payload["ticker"],"as_of":latest_day,"company_news":drivers,"macro":macro,"sector":sector,"benchmark_movement":benchmark_movement,"volume_anomaly":volume_anomaly,"corporate_events":corporate,"drivers":drivers,
            "causality":"unproven","wording":"Движение совпало с перечисленными событиями; причинно-следственная связь не установлена."}

    def statistics(self) -> dict:
        news=int(self.session.scalar(select(func.count(NewsArticle.id))) or 0); events=int(self.session.scalar(select(func.count(MarketEvent.id))) or 0)
        linked=int(self.session.scalar(select(func.count(MarketEvent.id)).where(MarketEvent.instrument_id.is_not(None))) or 0); reactions=int(self.session.scalar(select(func.count(EventMarketReaction.id))) or 0)
        distribution=dict(self.session.execute(select(MarketEvent.event_type,func.count(MarketEvent.id)).group_by(MarketEvent.event_type)).all())
        duplicates=int(sum(max(count-1,0) for count in self.session.scalars(select(EventCluster.article_count))))
        return {"news_collected":news,"events_extracted":events,"linked_to_ticker":linked,"event_types":distribution,"market_reactions_calculated":reactions,"duplicate_articles_clustered":duplicates}


def marker_type(event_type: str) -> str:
    if event_type=="earnings": return "E"
    if event_type=="dividend": return "D"
    if event_type=="product_launch": return "P"
    if event_type in {"interest_rate","inflation","currency","geopolitics"}: return "M"
    if event_type in {"rating_change","regulation","government_decision"}: return "R"
    return "N"


__all__=["NewsQueryService","factual_explanation","marker_type"]
