"""Append-only score snapshots.

Historical scores are never rewritten. A change in the facts, or a change in the
formula version, produces a *new* row; the old row keeps meaning exactly what it
meant on the day it was written. That is the whole point of storing the model
version and the input fingerprint alongside the number.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin


class StrictScoreSnapshot(Base, TimestampMixin):
    """One immutable scoring run for one instrument."""

    __tablename__ = "strict_score_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "ticker", "kind", "model_version", "as_of", "facts_fingerprint",
            name="uq_strict_score_snapshot",
        ),
        Index("ix_strict_scores_ticker_calculated", "ticker", "calculated_at"),
        Index("ix_strict_scores_kind_score", "kind", "final_score"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # bond | stock | bank
    kind: Mapped[str] = mapped_column(String(16), index=True)
    ticker: Mapped[str] = mapped_column(String(64), index=True)
    bond_id: Mapped[int | None] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    stock_id: Mapped[int | None] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )

    #: e.g. bond_score_v1 - never reused after a weight, threshold, cap or red
    #: flag rule changes.
    model_version: Mapped[str] = mapped_column(String(32), index=True)
    versions: Mapped[dict] = mapped_column(JSON)

    #: The valuation moment for point-in-time scores; NULL means "latest".
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    facts_fingerprint: Mapped[str] = mapped_column(String(64), index=True)

    final_score: Mapped[float] = mapped_column(Float)
    base_score: Mapped[float] = mapped_column(Float)
    penalised_score: Mapped[float] = mapped_column(Float)
    data_quality: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    band: Mapped[str] = mapped_column(String(16))

    #: The full result: components with raw values, weights, reasons, sources
    #: and timestamps, plus red flags and caps. Stored so an old score can be
    #: explained years later without re-running an old formula.
    breakdown: Mapped[dict] = mapped_column(JSON)
