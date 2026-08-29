from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import re

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.collectors.news import NewsSourceItem, NewsSourceProvider, article_fingerprint, canonicalize_url, normalize_title
from app.models.instrument import Instrument
from app.models.news import (EventCluster, EventMarketReaction, MarketEvent, NewsArticle,
    NewsClusterMember, NewsImpactScore, NotificationCandidate)
from app.models.portfolio import Alert, PortfolioPosition, Watchlist
from app.models.stock import Stock
from app.services.price_service import PriceService
from app.services.event_study import QuotePoint, abnormal_return, align_event_to_quotes
from app.services.news_entity_linker import NewsEntityLinker

EVENT_TAXONOMY = ("earnings", "revenue", "profit", "guidance", "dividend", "product_launch", "new_contract", "M&A", "acquisition", "sale", "management_change", "rating_change", "debt", "default", "lawsuit", "regulation", "government_decision", "capital_raise", "share_issue", "buyback", "accident", "production_change", "commodity_change", "interest_rate", "inflation", "currency", "tax", "geopolitics", "other")
KEYWORDS = {
    "earnings": ("earnings", "eps", "отчетност", "результат"), "revenue": ("revenue", "выручк"), "profit": ("profit", "прибыл"),
    "guidance": ("guidance", "прогноз компании"), "dividend": ("dividend", "дивиденд"), "product_launch": ("launch", "представил", "анонсировал", "новая линейка"),
    "new_contract": ("contract", "контракт", "договор"), "M&A": ("merger", "слияни"), "acquisition": ("acquisition", "поглощен", "приобрета"),
    "sale": ("sale", "продаж"), "management_change": ("ceo", "директор", "руководител"), "rating_change": ("rating", "рейтинг"),
    "debt": ("debt", "долг", "облигац"), "default": ("default", "дефолт"), "lawsuit": ("lawsuit", "иск", "суд"),
    "regulation": ("regulat", "регулятор"), "government_decision": ("government", "правительств", "постановлен"),
    "capital_raise": ("capital raise", "дополнительный капитал"), "share_issue": ("share issue", "выпуск акций", "эмисси"), "buyback": ("buyback", "обратный выкуп"),
    "accident": ("accident", "авари", "пожар"), "production_change": ("production", "производств", "добыч"), "commodity_change": ("oil price", "нефть", "сырь"),
    "interest_rate": ("interest rate", "ставк"), "inflation": ("inflation", "инфляц"), "currency": ("currency", "тенге", "курс валют"),
    "tax": ("tax", "налог"), "geopolitics": ("sanction", "санкци", "геополит")}
POSITIVE = ("рост", "вырос", "увелич", "прибыль", "контракт", "дивиденд", "launch", "gain", "beat")
NEGATIVE = ("паден", "сниз", "убыт", "дефолт", "авари", "иск", "sanction", "miss", "loss")


def classify_event(text: str) -> str:
    value = text.casefold()
    scores = {kind: sum(token in value for token in tokens) for kind, tokens in KEYWORDS.items()}
    best = max(scores, key=scores.get)
    return best if scores[best] else "other"


def language_sentiment(text: str) -> float:
    value = text.casefold(); pos = sum(word in value for word in POSITIVE); neg = sum(word in value for word in NEGATIVE)
    return (pos - neg) / max(pos + neg, 1)


def calculate_surprise(actual: float | None, consensus: float | None, *, direction: float = 1.0) -> float | None:
    """Standardized surprise; unknown consensus always stays NULL."""
    if actual is None or consensus is None: return None
    scale = max(abs(consensus), 1e-9)
    return max(-1.0, min(1.0, direction * (actual - consensus) / scale))


def source_tier(source: str) -> tuple[int, float]:
    key = source.casefold()
    if any(v in key for v in ("kase", "issuer", "regulator", "national_bank", "stat.gov")): return 1, .98
    if any(v in key for v in ("tengri", "reuters", "bloomberg")): return 2, .75
    return 3, .55


class NewsIntelligencePipeline:
    def __init__(self, session: Session): self.session = session

    async def collect(self, provider: NewsSourceProvider, *, since: datetime | None = None) -> dict:
        items = await provider.fetch_new(since=since)
        stats = {"fetched": len(items), "created": 0, "duplicates": 0, "events": 0, "linked": 0}
        for item in items:
            article, created = self.ingest(item)
            if not created: stats["duplicates"] += 1; continue
            stats["created"] += 1
            events = self.process_article(article)
            stats["events"] += len(events); stats["linked"] += sum(event.instrument_id is not None for event in events)
        self.session.commit()
        return stats

    def ingest(self, item: NewsSourceItem) -> tuple[NewsArticle, bool]:
        canonical = canonicalize_url(item.url)
        fingerprint = article_fingerprint(item.source, canonical, item.title, item.published_at)
        existing = self.session.execute(select(NewsArticle).where(or_(NewsArticle.fingerprint == fingerprint,
            (NewsArticle.source == item.source) & (NewsArticle.canonical_url == canonical)))).scalar_one_or_none()
        if existing: return existing, False
        _, default_conf = source_tier(item.source)
        short = (item.short_text or "")[:700] or None
        digest = sha256(f"{item.title}|{short or ''}".encode()).hexdigest()
        article = NewsArticle(source=item.source, source_url=item.url, canonical_url=canonical, title=item.title,
            published_at=item.published_at, fetched_at=datetime.now(timezone.utc), language=item.language, section=item.section,
            content_hash=digest, fingerprint=fingerprint, short_text=short, summary=short, source_confidence=item.source_confidence or default_conf)
        self.session.add(article); self.session.flush(); return article, True

    @staticmethod
    def _tokens(title: str) -> set[str]: return {word for word in normalize_title(title).split() if len(word) > 2}

    def _cluster(self, article: NewsArticle) -> tuple[EventCluster, bool]:
        recent = self.session.execute(select(EventCluster, NewsArticle).join(NewsArticle, NewsArticle.id == EventCluster.canonical_news_id).where(
            NewsArticle.published_at >= article.published_at - timedelta(hours=72))).all()
        tokens = self._tokens(article.title); best = None; best_score = 0.0
        for cluster, canonical in recent:
            other = self._tokens(canonical.title); score = len(tokens & other) / max(len(tokens | other), 1)
            if score > best_score: best, best_score = cluster, score
        duplicate = best is not None and best_score >= .58
        if not duplicate:
            key = sha256(f"{normalize_title(article.title)}|{article.published_at.date()}".encode()).hexdigest()
            best = EventCluster(canonical_news_id=article.id, cluster_key=key, title=article.title, article_count=0); self.session.add(best); self.session.flush(); best_score = 1.0
        else:
            canonical = self.session.get(NewsArticle, best.canonical_news_id)
            article_rank=(source_tier(article.source)[0], article.published_at.replace(tzinfo=None))
            canonical_rank=(source_tier(canonical.source)[0], canonical.published_at.replace(tzinfo=None)) if canonical else None
            if canonical and canonical_rank is not None and article_rank < canonical_rank:
                best.canonical_news_id = article.id; best.title = article.title
        self.session.add(NewsClusterMember(cluster_id=best.id, news_id=article.id, similarity=best_score)); best.article_count += 1
        return best, duplicate

    def process_article(self, article: NewsArticle) -> list[MarketEvent]:
        if article.is_processed: return []
        cluster, duplicate = self._cluster(article)
        # Reprints enrich the cluster but do not create a second market event.
        if duplicate:
            article.is_processed = True; self.session.flush(); return []
        text = f"{article.title} {article.short_text or ''}"
        matches = NewsEntityLinker(self.session).link(text)
        event_type = classify_event(text); sentiment = language_sentiment(text)
        events: list[MarketEvent] = []
        if not matches:
            events.append(MarketEvent(news_id=article.id, cluster_id=cluster.id, event_type=event_type, event_timestamp=article.published_at,
                country="KZ", importance=.45, sentiment=sentiment, surprise=None, source_confidence=article.source_confidence,
                analysis_confidence=.45, relevance=.4, entities={"companies": [], "tickers": []}))
        else:
            for match in matches:
                instrument = self.session.get(Instrument, match.instrument_id)
                issuer = instrument.issuer
                events.append(MarketEvent(news_id=article.id, cluster_id=cluster.id, event_type=event_type, event_timestamp=article.published_at,
                    issuer_id=match.issuer_id, instrument_id=match.instrument_id, sector=issuer.sector, country=issuer.country,
                    importance=min(.55 + .25 * match.relevance + (.1 if source_tier(article.source)[0] == 1 else 0), 1), sentiment=sentiment,
                    surprise=None, source_confidence=article.source_confidence, analysis_confidence=match.relevance, relevance=match.relevance,
                    entities={"companies": [issuer.name], "tickers": [match.ticker], "isin": match.isin, "matched_by": match.alias_type}))
        self.session.add_all(events); self.session.flush()
        for event in events:
            self.calculate_reaction(event); self._score(event); self._notification_candidates(event)
        article.is_processed = True; self.session.flush(); return events

    def _quote_points(self, instrument_id: int) -> list[QuotePoint]:
        """Prices for the event study, from the canonical observation record.

        The same readings the chart draws, so an abnormal return can always be
        traced to a visible point rather than to a parallel price series.
        """
        stock = self.session.execute(select(Stock).where(Stock.instrument_id == instrument_id)).scalar_one_or_none()
        points = PriceService(self.session).intraday_points(
            instrument_id, stock_id=stock.id if stock else None
        )
        return [QuotePoint(when, price, size) for when, price, size in points]

    def _benchmark(self, event: MarketEvent) -> Instrument | None:
        # Prefer a configured local broad index/security ticker; otherwise do
        # not invent a benchmark. NULL abnormal returns are truthful.
        return self.session.execute(select(Instrument).where(func.upper(Instrument.ticker).in_(("KASE", "KASE_INDEX", "SPY")), Instrument.id != event.instrument_id).limit(1)).scalar_one_or_none()

    def calculate_reaction(self, event: MarketEvent) -> EventMarketReaction | None:
        if event.instrument_id is None: return None
        existing = self.session.execute(select(EventMarketReaction).where(EventMarketReaction.event_id == event.id, EventMarketReaction.instrument_id == event.instrument_id)).scalar_one_or_none()
        if existing: return existing
        points = self._quote_points(event.instrument_id)
        observed = align_event_to_quotes(event.event_timestamp, points)
        benchmark = self._benchmark(event); benchmark_result = align_event_to_quotes(event.event_timestamp, self._quote_points(benchmark.id)) if benchmark else {}
        market_1d = benchmark_result.get("return_1d"); market_5d = benchmark_result.get("return_5d")
        row = EventMarketReaction(event_id=event.id, instrument_id=event.instrument_id, benchmark_id=benchmark.id if benchmark else None,
            **{key: observed.get(key) for key in ("price_before", "return_5m", "return_30m", "return_1h", "return_same_day", "return_1d", "return_5d", "return_20d", "volume_ratio", "volatility_change")},
            market_return=market_1d, sector_return=None, abnormal_return_1d=abnormal_return(observed.get("return_1d"), market_1d),
            abnormal_return_5d=abnormal_return(observed.get("return_5d"), market_5d), calculated_at=datetime.now(timezone.utc))
        self.session.add(row); self.session.flush(); return row

    def _score(self, event: MarketEvent) -> NewsImpactScore:
        reaction = self.session.execute(select(EventMarketReaction).where(EventMarketReaction.event_id == event.id)).scalar_one_or_none()
        def bounded(value, scale): return max(-1.0, min(1.0, (value or 0) / scale))
        components = {"importance": event.importance, "sentiment": event.sentiment or 0, "surprise": event.surprise,
            "abnormal_return_1d": reaction.abnormal_return_1d if reaction else None, "volume_ratio": reaction.volume_ratio if reaction else None}
        direction = .25 * (event.sentiment or 0) + .20 * (event.surprise or 0) + .35 * bounded(reaction.abnormal_return_1d if reaction else None, .05)
        attention = .15 * bounded((reaction.volume_ratio - 1) if reaction and reaction.volume_ratio else 0, 2) + .05 * event.importance
        value = max(-100, min(100, round(100 * (direction + (attention if direction >= 0 else -attention)), 2)))
        score = NewsImpactScore(event_id=event.id, value=value, formula_version="news-impact-v1", components=components); self.session.add(score); return score

    def _notification_candidates(self, event: MarketEvent) -> int:
        if event.instrument_id is None or event.importance < .75: return 0
        stock = self.session.execute(select(Stock).where(Stock.instrument_id == event.instrument_id)).scalar_one_or_none()
        if not stock: return 0
        audiences = set()
        for row in self.session.execute(select(Watchlist).where(Watchlist.stock_id == stock.id)).scalars(): audiences.add(("watchlist", str(row.user_id or row.anonymous_token)))
        for row in self.session.execute(select(PortfolioPosition).where(
            PortfolioPosition.stock_id == stock.id,
            PortfolioPosition.status == "EXECUTED",
        )).scalars(): audiences.add(("portfolio", str(row.portfolio_id)))
        for row in self.session.execute(select(Alert).where(Alert.stock_id == stock.id, Alert.is_active.is_(True))).scalars(): audiences.add(("alert", str(row.id)))
        for kind, key in audiences:
            self.session.add(NotificationCandidate(event_id=event.id, audience_type=kind, audience_key=key, importance=event.importance, reason="Важное связанное событие"))
        return len(audiences)


__all__ = ["EVENT_TAXONOMY", "NewsIntelligencePipeline", "calculate_surprise", "classify_event", "language_sentiment", "source_tier"]
