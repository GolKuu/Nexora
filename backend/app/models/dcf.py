from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin


class DCFSubscription(Base, TimestampMixin):
    __tablename__ = "dcf_subscriptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True)
    plan: Mapped[str] = mapped_column(String(32), default="free")
    status: Mapped[str] = mapped_column(String(24), default="active")
    monthly_limit: Mapped[int] = mapped_column(Integer, default=0)
    current_period_end: Mapped[date | None] = mapped_column(Date)


class DCFRun(Base, TimestampMixin):
    __tablename__ = "dcf_runs"
    __table_args__ = (Index("ix_dcf_run_cache", "instrument_id", "valuation_cache_hash", "status"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    anonymous_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), index=True)
    ticker: Mapped[str] = mapped_column(String(64), index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), index=True)
    financial_statement_id: Mapped[int | None] = mapped_column(ForeignKey("financial_statements.id", ondelete="SET NULL"))
    market_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    macro_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    dcf_model_version: Mapped[str] = mapped_column(String(48))
    assumption_version: Mapped[str] = mapped_column(String(48))
    prompt_version: Mapped[str | None] = mapped_column(String(48))
    ai_model_version: Mapped[str | None] = mapped_column(String(96))
    input_snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    valuation_cache_hash: Mapped[str] = mapped_column(String(64), index=True)
    data_quality_score: Mapped[float] = mapped_column(Float)
    analysis_confidence: Mapped[str] = mapped_column(String(16))
    bear_target_price: Mapped[float | None] = mapped_column(Float)
    base_target_price: Mapped[float | None] = mapped_column(Float)
    bull_target_price: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3))
    warnings: Mapped[list | None] = mapped_column(JSON)
    failure_reason: Mapped[str | None] = mapped_column(Text)
    disclaimer_version: Mapped[str] = mapped_column(String(32))
    shown_to_user_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stale_due_to_new_financials: Mapped[bool] = mapped_column(Boolean, default=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    total_latency_ms: Mapped[float | None] = mapped_column(Float)
    scenarios: Mapped[list["DCFScenarioResult"]] = relationship(cascade="all, delete-orphan", back_populates="run")
    assumptions: Mapped[list["DCFAssumption"]] = relationship(cascade="all, delete-orphan", back_populates="run")
    snapshots: Mapped[list["DCFInputSnapshot"]] = relationship(cascade="all, delete-orphan", back_populates="run")
    validations: Mapped[list["DCFValidationResult"]] = relationship(cascade="all, delete-orphan", back_populates="run")


class DCFScenarioResult(Base, TimestampMixin):
    __tablename__ = "dcf_scenario_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("dcf_runs.id", ondelete="CASCADE"), index=True)
    scenario_type: Mapped[str] = mapped_column(String(8))
    fair_value: Mapped[float] = mapped_column(Float)
    enterprise_value: Mapped[float] = mapped_column(Float)
    equity_value: Mapped[float] = mapped_column(Float)
    assumptions: Mapped[dict] = mapped_column(JSON)
    calculation: Mapped[dict] = mapped_column(JSON)
    run: Mapped[DCFRun] = relationship(back_populates="scenarios")


class DCFInputSnapshot(Base, TimestampMixin):
    __tablename__ = "dcf_input_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("dcf_runs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    version: Mapped[str] = mapped_column(String(64))
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(128))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    payload: Mapped[dict] = mapped_column(JSON)
    payload_hash: Mapped[str] = mapped_column(String(64))
    run: Mapped[DCFRun] = relationship(back_populates="snapshots")


class DCFAssumption(Base, TimestampMixin):
    __tablename__ = "dcf_assumptions"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("dcf_runs.id", ondelete="CASCADE"), index=True)
    scenario_type: Mapped[str] = mapped_column(String(8), index=True)
    name: Mapped[str] = mapped_column(String(64), index=True)
    value: Mapped[float | None] = mapped_column(Float)
    values: Mapped[list | None] = mapped_column(JSON)
    unit: Mapped[str] = mapped_column(String(24), default="decimal")
    source: Mapped[str] = mapped_column(String(256))
    reason: Mapped[str] = mapped_column(String(512))
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    run: Mapped[DCFRun] = relationship(back_populates="assumptions")


class DCFValidationResult(Base, TimestampMixin):
    __tablename__ = "dcf_validation_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("dcf_runs.id", ondelete="CASCADE"), index=True)
    rule: Mapped[str] = mapped_column(String(96))
    passed: Mapped[bool] = mapped_column(Boolean)
    severity: Mapped[str] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(String(512))
    run: Mapped[DCFRun] = relationship(back_populates="validations")


class DCFUsageEvent(Base, TimestampMixin):
    __tablename__ = "dcf_usage_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    anonymous_token_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("dcf_runs.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(24), index=True)
    period_start: Mapped[date] = mapped_column(Date)
    counted: Mapped[bool] = mapped_column(Boolean, default=True)


class DCFCostEvent(Base, TimestampMixin):
    __tablename__ = "dcf_cost_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("dcf_runs.id", ondelete="CASCADE"), index=True)
    ai_provider: Mapped[str | None] = mapped_column(String(48))
    ai_model: Mapped[str | None] = mapped_column(String(96))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    ai_cost: Mapped[float] = mapped_column(Float, default=0.0)
    compute_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    document_parsing_duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)


class DisclaimerConfig(Base, TimestampMixin):
    __tablename__ = "disclaimer_configs"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    text: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    require_acknowledgement: Mapped[bool] = mapped_column(Boolean, default=False)


class DCFModelVersion(Base, TimestampMixin):
    __tablename__ = "dcf_model_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(48), unique=True)
    methodology: Mapped[str] = mapped_column(String(48), default="FCFF")
    status: Mapped[str] = mapped_column(String(24), default="pilot")
    approved_by: Mapped[str | None] = mapped_column(String(128))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
