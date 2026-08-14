"""Persistence for change-aware ingestion.

Current rows are optimized for reads. Versions and change sets are appended
only when normalized source data actually changes. Lightweight checks live in
``SourceCheckLog`` and therefore do not create heavyweight raw snapshots.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class DataCurrentState(Base, TimestampMixin):
    __tablename__ = "data_current_state"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id", "section", name="uq_current_entity_section"),
        Index("ix_current_entity_section", "entity_id", "section"),
        Index("ix_current_last_checked", "last_checked_at"),
        Index("ix_current_last_changed", "last_changed_at"),
        Index("ix_current_source_timestamp", "source_timestamp"),
        Index("ix_current_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    ticker: Mapped[str | None] = mapped_column(String(64), index=True)
    isin: Mapped[str | None] = mapped_column(String(12), index=True)
    section: Mapped[str] = mapped_column(String(64), index=True)
    source_url: Mapped[str] = mapped_column(String(1024), index=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
    normalized_json: Mapped[dict | list] = mapped_column(JSON)
    parser_version: Mapped[str] = mapped_column(String(32))
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_browser_snapshots.id", ondelete="SET NULL")
    )


class DataStateVersion(Base, TimestampMixin):
    __tablename__ = "data_state_versions"
    __table_args__ = (
        UniqueConstraint("current_state_id", "content_hash", name="uq_state_version_hash"),
        Index("ix_state_version_entity_section", "entity_id", "section", "detected_at"),
        Index("ix_state_version_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    current_state_id: Mapped[int] = mapped_column(
        ForeignKey("data_current_state.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    section: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    normalized_json: Mapped[dict | list] = mapped_column(JSON)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_browser_snapshots.id", ondelete="SET NULL")
    )
    parser_version: Mapped[str] = mapped_column(String(32))


class DataChangeSet(Base, TimestampMixin):
    __tablename__ = "data_change_sets"
    __table_args__ = (
        Index("ix_changes_entity_section", "entity_id", "section", "detected_at"),
        Index("ix_changes_source_url", "source_url"),
        Index("ix_changes_source_timestamp", "source_timestamp"),
        Index("ix_changes_type", "change_type"),
        Index("ix_changes_importance", "importance"),
        UniqueConstraint(
            "entity_type", "entity_id", "section", "field", "snapshot_after_id", "change_fingerprint",
            name="uq_change_event",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    ticker: Mapped[str | None] = mapped_column(String(64), index=True)
    isin: Mapped[str | None] = mapped_column(String(12), index=True)
    section: Mapped[str] = mapped_column(String(64), index=True)
    field: Mapped[str] = mapped_column(String(128), index=True)
    old_value: Mapped[object | None] = mapped_column(JSON)
    new_value: Mapped[object | None] = mapped_column(JSON)
    change_type: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str] = mapped_column(String(1024))
    source_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    snapshot_before_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_state_versions.id", ondelete="SET NULL")
    )
    snapshot_after_id: Mapped[int | None] = mapped_column(
        ForeignKey("data_state_versions.id", ondelete="SET NULL")
    )
    parser_version: Mapped[str] = mapped_column(String(32))
    importance: Mapped[int] = mapped_column(Integer, default=0)
    material: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    suspected_anomaly: Mapped[bool] = mapped_column(Boolean, default=False)
    change_fingerprint: Mapped[str] = mapped_column(String(64))


class SourceCheckLog(Base):
    __tablename__ = "source_check_logs"
    __table_args__ = (
        Index("ix_check_source_checked", "source_url", "checked_at"),
        Index("ix_check_entity_checked", "entity_id", "checked_at"),
        Index("ix_check_content_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_url: Mapped[str] = mapped_column(String(1024), index=True)
    entity_id: Mapped[str | None] = mapped_column(String(128), index=True)
    section: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    http_status: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    changed: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("ingestion_jobs.id", ondelete="SET NULL"))


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"
    __table_args__ = (Index("ix_ingestion_job_type_started", "job_type", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(24), index=True)
    entities_checked: Mapped[int] = mapped_column(Integer, default=0)
    entities_changed: Mapped[int] = mapped_column(Integer, default=0)
    entities_unchanged: Mapped[int] = mapped_column(Integer, default=0)
    entities_failed: Mapped[int] = mapped_column(Integer, default=0)
    new_records: Mapped[int] = mapped_column(Integer, default=0)
    updated_records: Mapped[int] = mapped_column(Integer, default=0)
    ai_tasks_created: Mapped[int] = mapped_column(Integer, default=0)
    metrics_json: Mapped[dict | None] = mapped_column(JSON)
    error_summary: Mapped[str | None] = mapped_column(Text)


class RecalculationTask(Base, TimestampMixin):
    __tablename__ = "recalculation_tasks"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_recalculation_dedupe"),
        Index("ix_recalc_status_priority", "status", "priority"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    reason: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    priority: Mapped[int] = mapped_column(Integer, default=50)
    dedupe_key: Mapped[str] = mapped_column(String(64))


class AIChangeTask(Base, TimestampMixin):
    __tablename__ = "ai_change_tasks"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_ai_change_task_dedupe"),
        Index("ix_ai_change_status", "status", "task_type"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    ticker: Mapped[str | None] = mapped_column(String(64), index=True)
    task_type: Mapped[str] = mapped_column(String(64), index=True)
    payload_json: Mapped[dict] = mapped_column(JSON)
    model_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    dedupe_key: Mapped[str] = mapped_column(String(64))


class KaseDocument(Base, TimestampMixin):
    __tablename__ = "kase_documents"
    __table_args__ = (UniqueConstraint("document_url", name="uq_document_url"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    ticker: Mapped[str | None] = mapped_column(String(64), index=True)
    issuer_code: Mapped[str | None] = mapped_column(String(64), index=True)
    document_url: Mapped[str] = mapped_column(String(1024), index=True)
    document_name: Mapped[str] = mapped_column(String(512))
    document_type: Mapped[str | None] = mapped_column(String(64))
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    current_version_id: Mapped[int | None] = mapped_column(Integer)


class DocumentVersion(Base, TimestampMixin):
    __tablename__ = "document_versions"
    __table_args__ = (
        UniqueConstraint("document_id", "content_hash", name="uq_document_version_hash"),
        Index("ix_document_version_hash", "content_hash"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("kase_documents.id", ondelete="CASCADE"), index=True)
    version_number: Mapped[int] = mapped_column(Integer)
    file_size: Mapped[int | None] = mapped_column(Integer)
    etag: Mapped[str | None] = mapped_column(String(255))
    last_modified: Mapped[str | None] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64))
    downloaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    storage_path: Mapped[str | None] = mapped_column(String(1024))
    analysis_status: Mapped[str] = mapped_column(String(16), default="pending")


class KaseNewsItem(Base, TimestampMixin):
    __tablename__ = "kase_news_items"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_news_fingerprint"),
        Index("ix_news_entity_published", "entity_id", "publication_date"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[str] = mapped_column(String(128), index=True)
    ticker: Mapped[str | None] = mapped_column(String(64), index=True)
    issuer_code: Mapped[str | None] = mapped_column(String(64), index=True)
    stable_identifier: Mapped[str | None] = mapped_column(String(128), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(1024))
    publication_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    url: Mapped[str] = mapped_column(String(1024))
    content_hash: Mapped[str | None] = mapped_column(String(64))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

