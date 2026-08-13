"""Persistence for what the browser agent saw and did.

``RawBrowserSnapshot`` is the browser's counterpart to ``RawKaseData`` (§32):
the page as it was, so any extracted figure can be traced back to the render it
came from. ``BrowserNavigationLog`` records the click path that produced it
(§40) - never any credential.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class RawBrowserSnapshot(Base, TimestampMixin):
    """One rendered page, hashed and kept for provenance."""

    __tablename__ = "raw_browser_snapshots"
    __table_args__ = (
        Index("ix_browser_snapshot_kind_key", "kind", "key", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), index=True)
    page_title: Mapped[str | None] = mapped_column(String(512))
    #: catalog | bond | issuer | tab | search
    kind: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str | None] = mapped_column(String(128))
    section: Mapped[str | None] = mapped_column(String(128))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    html_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    visible_text: Mapped[str | None] = mapped_column(Text)
    #: Structured payload: tables, documents, tabs, accepted values, warnings.
    extracted_json: Mapped[dict | list | None] = mapped_column(JSON)
    screenshot_path: Mapped[str | None] = mapped_column(String(1024))
    browser_version: Mapped[str | None] = mapped_column(String(64))
    extractor_version: Mapped[str | None] = mapped_column(String(32))
    browser_session_id: Mapped[str | None] = mapped_column(String(32), index=True)
    language: Mapped[str | None] = mapped_column(String(8))
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[float | None] = mapped_column(Float)
    #: ok | error | timeout | blocked_by_captcha | requires_authentication | ...
    status: Mapped[str] = mapped_column(String(32), default="ok")
    blocked_by_captcha: Mapped[bool] = mapped_column(Boolean, default=False)
    requires_authentication: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(Text)


class BrowserNavigationLog(Base, TimestampMixin):
    """One browser action, for debugging a flow after the fact (§40)."""

    __tablename__ = "browser_navigation_log"
    __table_args__ = (
        Index("ix_browser_nav_session_action", "session_id", "action_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String(32), index=True)
    action_number: Mapped[int] = mapped_column(Integer)
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str | None] = mapped_column(String(512))
    url_before: Mapped[str | None] = mapped_column(String(1024))
    url_after: Mapped[str | None] = mapped_column(String(1024))
    status: Mapped[str] = mapped_column(String(32))
    duration_ms: Mapped[float | None] = mapped_column(Float)
    error: Mapped[str | None] = mapped_column(Text)
