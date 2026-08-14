from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SourceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.issuer import Issuer
    from app.models.market import BondQuote, BondTrade
    from app.models.metrics import BondMetric
    from app.models.scores import BondScore


class Bond(Base, TimestampMixin, SourceMixin):
    __tablename__ = "bonds"
    __table_args__ = (Index("ix_bonds_active_maturity", "is_active", "maturity_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    isin: Mapped[str | None] = mapped_column(String(12), unique=True, index=True)
    issuer_id: Mapped[int] = mapped_column(
        ForeignKey("issuers.id", ondelete="RESTRICT"), index=True
    )
    name: Mapped[str] = mapped_column(String(512))
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    nominal: Mapped[float | None] = mapped_column(Float)

    issue_date: Mapped[date | None] = mapped_column(Date)
    maturity_date: Mapped[date | None] = mapped_column(Date, index=True)

    coupon_rate: Mapped[float | None] = mapped_column(Float)  # decimal, 0.145 == 14.5%
    coupon_type: Mapped[str | None] = mapped_column(String(16))
    coupon_frequency: Mapped[int | None] = mapped_column(Integer)  # payments per year
    next_coupon_date: Mapped[date | None] = mapped_column(Date)
    day_count: Mapped[str] = mapped_column(String(16), default="ACT/365F")

    issue_size: Mapped[float | None] = mapped_column(Float)
    outstanding_amount: Mapped[float | None] = mapped_column(Float)

    market_segment: Mapped[str | None] = mapped_column(String(64))
    bond_type: Mapped[str | None] = mapped_column(String(32), index=True)

    secured: Mapped[bool | None] = mapped_column(Boolean)
    subordinated: Mapped[bool | None] = mapped_column(Boolean)
    callable: Mapped[bool | None] = mapped_column(Boolean)
    putable: Mapped[bool | None] = mapped_column(Boolean)
    guarantee: Mapped[str | None] = mapped_column(Text)

    kase_url: Mapped[str | None] = mapped_column(String(512))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    peer_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("peer_groups.id", ondelete="SET NULL"), index=True
    )

    issuer: Mapped["Issuer"] = relationship(back_populates="bonds")
    peer_group: Mapped["PeerGroup | None"] = relationship(back_populates="bonds")
    quotes: Mapped[list["BondQuote"]] = relationship(
        back_populates="bond", cascade="all, delete-orphan"
    )
    trades: Mapped[list["BondTrade"]] = relationship(
        back_populates="bond", cascade="all, delete-orphan"
    )
    cashflows: Mapped[list["BondCashFlow"]] = relationship(
        back_populates="bond", cascade="all, delete-orphan"
    )
    metrics: Mapped[list["BondMetric"]] = relationship(
        back_populates="bond", cascade="all, delete-orphan"
    )
    scores: Mapped[list["BondScore"]] = relationship(
        back_populates="bond", cascade="all, delete-orphan"
    )


class BondCashFlow(Base, TimestampMixin, SourceMixin):
    """A single scheduled payment (coupon and/or principal)."""

    __tablename__ = "bond_cashflows"
    __table_args__ = (Index("ix_cashflows_bond_date", "bond_id", "payment_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    payment_date: Mapped[date] = mapped_column(Date)
    period_start: Mapped[date | None] = mapped_column(Date)
    coupon_amount: Mapped[float | None] = mapped_column(Float)
    principal_amount: Mapped[float | None] = mapped_column(Float)
    total_amount: Mapped[float | None] = mapped_column(Float)
    is_estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)

    bond: Mapped["Bond"] = relationship(back_populates="cashflows")


class PeerGroup(Base, TimestampMixin):
    """A comparison bucket used for Relative Value scoring."""

    __tablename__ = "peer_groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255))
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    bond_type: Mapped[str | None] = mapped_column(String(32))
    sector: Mapped[str | None] = mapped_column(String(32))
    maturity_bucket: Mapped[str | None] = mapped_column(String(32))  # e.g. "1-3y"
    description: Mapped[str | None] = mapped_column(Text)

    bonds: Mapped[list["Bond"]] = relationship(back_populates="peer_group")
