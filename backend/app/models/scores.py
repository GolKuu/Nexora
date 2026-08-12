from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.bond import Bond


class BondScore(Base, TimestampMixin):
    """One score of one kind for one bond, always on a 0-100 scale."""

    __tablename__ = "bond_scores"
    __table_args__ = (Index("ix_scores_bond_kind", "bond_id", "kind", "calculated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    # investment | credit | liquidity | ... | personal
    kind: Mapped[str] = mapped_column(String(32), index=True)
    value: Mapped[float | None] = mapped_column(Float)
    version: Mapped[str] = mapped_column(String(32))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # 0..1 - how much of the required input data was actually available
    confidence: Mapped[float | None] = mapped_column(Float)
    # Personal scores belong to one user and never affect the base scores.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    inputs: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)

    bond: Mapped["Bond"] = relationship(back_populates="scores")
    components: Mapped[list["ScoreComponent"]] = relationship(
        back_populates="score", cascade="all, delete-orphan"
    )


class ScoreComponent(Base, TimestampMixin):
    """A weighted contributor to a score, kept so the UI can explain itself."""

    __tablename__ = "score_components"

    id: Mapped[int] = mapped_column(primary_key=True)
    score_id: Mapped[int] = mapped_column(
        ForeignKey("bond_scores.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(64))
    label: Mapped[str] = mapped_column(String(255))
    value: Mapped[float | None] = mapped_column(Float)  # 0..100
    weight: Mapped[float] = mapped_column(Float)
    contribution: Mapped[float | None] = mapped_column(Float)
    raw_value: Mapped[float | None] = mapped_column(Float)
    raw_unit: Mapped[str | None] = mapped_column(String(32))
    available: Mapped[bool] = mapped_column(default=True)
    explanation: Mapped[str | None] = mapped_column(Text)

    score: Mapped["BondScore"] = relationship(back_populates="components")
