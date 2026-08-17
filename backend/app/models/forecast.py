"""Persistence for reproducible stock forecasts and their track record."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class ForecastModelVersion(Base, TimestampMixin):
    __tablename__ = "forecast_model_versions"
    __table_args__ = (Index("ix_forecast_models_market_status", "market", "production_status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    market: Mapped[str] = mapped_column(String(32), default="KASE", index=True)
    training_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    training_dataset_version: Mapped[str] = mapped_column(String(64))
    features_version: Mapped[str] = mapped_column(String(32))
    hyperparameters: Mapped[dict] = mapped_column(JSON)
    evaluation_metrics: Mapped[dict] = mapped_column(JSON)
    production_status: Mapped[str] = mapped_column(String(24), default="candidate", index=True)


class ForecastSnapshot(Base, TimestampMixin):
    """Immutable inference output; a new observation always creates a new row."""

    __tablename__ = "forecast_snapshots"
    __table_args__ = (
        UniqueConstraint("instrument_id", "model_version", "generated_at", "horizon", name="uq_forecast_snapshot"),
        Index("ix_forecast_snapshots_instrument_generated", "instrument_id", "generated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    model_version: Mapped[str] = mapped_column(String(64), index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    as_of_market_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_mode: Mapped[str] = mapped_column(String(24))
    features_hash: Mapped[str] = mapped_column(String(64))
    horizon: Mapped[int] = mapped_column(Integer, index=True)
    current_price: Mapped[float] = mapped_column(Float)
    prediction: Mapped[dict] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("market_events.id", ondelete="SET NULL"), index=True)


class ForecastEvaluation(Base, TimestampMixin):
    __tablename__ = "forecast_evaluations"
    __table_args__ = (UniqueConstraint("snapshot_id", name="uq_forecast_evaluation_snapshot"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("forecast_snapshots.id", ondelete="CASCADE"), index=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    realized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    realized_price: Mapped[float] = mapped_column(Float)
    realized_return: Mapped[float] = mapped_column(Float)
    direction_correct: Mapped[bool] = mapped_column(Boolean)
    interval_50_hit: Mapped[bool] = mapped_column(Boolean)
    interval_80_hit: Mapped[bool] = mapped_column(Boolean)
    brier_score: Mapped[float] = mapped_column(Float)
    absolute_error: Mapped[float] = mapped_column(Float)


class ForecastChange(Base, TimestampMixin):
    __tablename__ = "forecast_changes"
    __table_args__ = (Index("ix_forecast_changes_instrument_created", "instrument_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    previous_snapshot_id: Mapped[int] = mapped_column(ForeignKey("forecast_snapshots.id", ondelete="CASCADE"))
    current_snapshot_id: Mapped[int] = mapped_column(ForeignKey("forecast_snapshots.id", ondelete="CASCADE"))
    horizon: Mapped[int] = mapped_column(Integer)
    probability_change: Mapped[float] = mapped_column(Float)
    expected_return_change: Mapped[float] = mapped_column(Float)
    interval_width_change: Mapped[float] = mapped_column(Float)
    confidence_change: Mapped[float] = mapped_column(Float)
    reason: Mapped[str] = mapped_column(Text)
