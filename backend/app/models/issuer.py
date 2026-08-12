from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SourceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.bond import Bond
    from app.models.financials import CreditRating, FinancialStatement, IssuerMetric


class Issuer(Base, TimestampMixin, SourceMixin):
    __tablename__ = "issuers"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512))
    short_name: Mapped[str | None] = mapped_column(String(255))
    bin: Mapped[str | None] = mapped_column(String(32), index=True)
    country: Mapped[str] = mapped_column(String(2), default="KZ")
    # sovereign | bank | financial | corporate | quasi_sovereign
    sector: Mapped[str | None] = mapped_column(String(32), index=True)
    industry: Mapped[str | None] = mapped_column(String(128))
    # Banks are scored with a dedicated credit model, never the corporate one.
    is_financial_institution: Mapped[bool] = mapped_column(Boolean, default=False)
    is_state_owned: Mapped[bool] = mapped_column(Boolean, default=False)
    website: Mapped[str | None] = mapped_column(String(512))
    kase_url: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    bonds: Mapped[list["Bond"]] = relationship(back_populates="issuer")
    statements: Mapped[list["FinancialStatement"]] = relationship(
        back_populates="issuer", cascade="all, delete-orphan"
    )
    metrics: Mapped[list["IssuerMetric"]] = relationship(
        back_populates="issuer", cascade="all, delete-orphan"
    )
    ratings: Mapped[list["CreditRating"]] = relationship(
        back_populates="issuer", cascade="all, delete-orphan"
    )
