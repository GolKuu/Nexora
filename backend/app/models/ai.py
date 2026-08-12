from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class AIAnalysis(Base, TimestampMixin):
    """A stored LLM output.

    The LLM only ever explains numbers that the calculation engine produced;
    `inputs` records exactly which numbers it was given.
    """

    __tablename__ = "ai_analyses"
    __table_args__ = (Index("ix_ai_bond_kind", "bond_id", "kind", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int | None] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    issuer_id: Mapped[int | None] = mapped_column(
        ForeignKey("issuers.id", ondelete="CASCADE"), index=True
    )
    # score_explanation | document_summary | risk_notes | compare
    kind: Mapped[str] = mapped_column(String(48), index=True)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    provider: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    inputs: Mapped[dict | None] = mapped_column(JSON)
    content: Mapped[str] = mapped_column(Text)
    tokens_prompt: Mapped[int | None] = mapped_column(Integer)
    tokens_completion: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[float | None] = mapped_column(Float)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
