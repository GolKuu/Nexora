"""Versioned technical-indicator configuration and on-demand result cache."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class TechnicalIndicatorConfigVersion(Base, TimestampMixin):
    __tablename__ = "technical_indicator_config_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    parameters: Mapped[dict] = mapped_column(JSON)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class TechnicalAnalysisCache(Base, TimestampMixin):
    __tablename__ = "technical_analysis_cache"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "latest_market_observation_id", "config_version",
            name="uq_technical_analysis_cache_key",
        ),
        Index("ix_technical_cache_instrument_created", "instrument_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    latest_market_observation_id: Mapped[int] = mapped_column(BigInteger, index=True)
    config_version: Mapped[str] = mapped_column(String(64), index=True)
    result: Mapped[dict] = mapped_column(JSON)


__all__ = ["TechnicalAnalysisCache", "TechnicalIndicatorConfigVersion"]
