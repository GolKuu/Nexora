"""News intelligence persistence.

Sentiment (language) and observed market impact are deliberately stored in
different tables.  No model-produced number is allowed into a reaction row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class NewsArticle(Base, TimestampMixin):
    __tablename__ = "news_articles"
    __table_args__ = (
        UniqueConstraint("source", "canonical_url", name="uq_news_article_source_url"),
        UniqueConstraint("fingerprint", name="uq_news_article_fingerprint"),
        Index("ix_news_articles_published", "published_at"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str] = mapped_column(String(2048))
    canonical_url: Mapped[str] = mapped_column(String(2048))
    title: Mapped[str] = mapped_column(String(1024))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    language: Mapped[str | None] = mapped_column(String(8))
    section: Mapped[str | None] = mapped_column(String(128))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    short_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    source_confidence: Mapped[float] = mapped_column(Float)
    is_processed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class CompanyAlias(Base, TimestampMixin):
    __tablename__ = "company_aliases"
    __table_args__ = (UniqueConstraint("normalized_alias", "issuer_id", name="uq_company_alias_issuer"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(512))
    normalized_alias: Mapped[str] = mapped_column(String(512), index=True)
    alias_type: Mapped[str] = mapped_column(String(32), default="name")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)


class EventCluster(Base, TimestampMixin):
    __tablename__ = "event_clusters"
    id: Mapped[int] = mapped_column(primary_key=True)
    canonical_news_id: Mapped[int | None] = mapped_column(ForeignKey("news_articles.id", ondelete="SET NULL"), index=True)
    cluster_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(1024))
    article_count: Mapped[int] = mapped_column(Integer, default=1)


class NewsClusterMember(Base, TimestampMixin):
    __tablename__ = "news_cluster_members"
    __table_args__ = (UniqueConstraint("news_id", name="uq_news_cluster_member"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("event_clusters.id", ondelete="CASCADE"), index=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"), index=True)
    similarity: Mapped[float] = mapped_column(Float)


class MarketEvent(Base, TimestampMixin):
    __tablename__ = "market_events"
    __table_args__ = (Index("ix_market_events_instrument_ts", "instrument_id", "event_timestamp"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    news_id: Mapped[int] = mapped_column(ForeignKey("news_articles.id", ondelete="CASCADE"), index=True)
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("event_clusters.id", ondelete="SET NULL"), index=True)
    event_type: Mapped[str] = mapped_column(String(32), index=True)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    issuer_id: Mapped[int | None] = mapped_column(ForeignKey("issuers.id", ondelete="SET NULL"), index=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id", ondelete="SET NULL"), index=True)
    sector: Mapped[str | None] = mapped_column(String(128), index=True)
    country: Mapped[str | None] = mapped_column(String(2), index=True)
    importance: Mapped[float] = mapped_column(Float)
    sentiment: Mapped[float | None] = mapped_column(Float)
    surprise: Mapped[float | None] = mapped_column(Float)
    market_regime: Mapped[str | None] = mapped_column(String(32), index=True)
    source_confidence: Mapped[float] = mapped_column(Float)
    analysis_confidence: Mapped[float] = mapped_column(Float)
    relevance: Mapped[float] = mapped_column(Float, default=1.0)
    entities: Mapped[dict | None] = mapped_column(JSON)


class EventMarketReaction(Base, TimestampMixin):
    __tablename__ = "event_market_reactions"
    __table_args__ = (UniqueConstraint("event_id", "instrument_id", name="uq_event_instrument_reaction"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("market_events.id", ondelete="CASCADE"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    benchmark_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id", ondelete="SET NULL"), index=True)
    price_before: Mapped[float | None] = mapped_column(Float)
    return_5m: Mapped[float | None] = mapped_column(Float)
    return_30m: Mapped[float | None] = mapped_column(Float)
    return_1h: Mapped[float | None] = mapped_column(Float)
    return_same_day: Mapped[float | None] = mapped_column(Float)
    return_1d: Mapped[float | None] = mapped_column(Float)
    return_5d: Mapped[float | None] = mapped_column(Float)
    return_20d: Mapped[float | None] = mapped_column(Float)
    volume_ratio: Mapped[float | None] = mapped_column(Float)
    volatility_change: Mapped[float | None] = mapped_column(Float)
    market_return: Mapped[float | None] = mapped_column(Float)
    sector_return: Mapped[float | None] = mapped_column(Float)
    abnormal_return_1d: Mapped[float | None] = mapped_column(Float)
    abnormal_return_5d: Mapped[float | None] = mapped_column(Float)
    formula_version: Mapped[str] = mapped_column(String(32), default="event-study-v1")
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class NewsImpactScore(Base, TimestampMixin):
    __tablename__ = "news_impact_scores"
    __table_args__ = (UniqueConstraint("event_id", "formula_version", name="uq_event_impact_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("market_events.id", ondelete="CASCADE"), index=True)
    value: Mapped[float] = mapped_column(Float)
    formula_version: Mapped[str] = mapped_column(String(32))
    components: Mapped[dict] = mapped_column(JSON)


class NotificationCandidate(Base, TimestampMixin):
    __tablename__ = "notification_candidates"
    __table_args__ = (UniqueConstraint("event_id", "audience_type", "audience_key", name="uq_event_notification_audience"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("market_events.id", ondelete="CASCADE"), index=True)
    audience_type: Mapped[str] = mapped_column(String(32))
    audience_key: Mapped[str] = mapped_column(String(128))
    importance: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="candidate", index=True)
    reason: Mapped[str] = mapped_column(Text)
