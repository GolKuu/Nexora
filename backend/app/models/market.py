from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SourceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.bond import Bond


class BondQuote(Base, TimestampMixin, SourceMixin):
    """A market data snapshot. Every field may legitimately be NULL."""

    __tablename__ = "bond_quotes"
    __table_args__ = (Index("ix_quotes_bond_ts", "bond_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    bid_volume: Mapped[float | None] = mapped_column(Float)
    ask_volume: Mapped[float | None] = mapped_column(Float)
    last: Mapped[float | None] = mapped_column(Float)

    clean_price: Mapped[float | None] = mapped_column(Float)  # % of nominal
    dirty_price: Mapped[float | None] = mapped_column(Float)
    accrued_interest: Mapped[float | None] = mapped_column(Float)
    ytm: Mapped[float | None] = mapped_column(Float)  # decimal

    volume: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    number_of_trades: Mapped[int | None] = mapped_column(Integer)

    # live | delayed | end_of_day | cached | mock
    data_mode: Mapped[str] = mapped_column(String(16), index=True)

    bond: Mapped["Bond"] = relationship(back_populates="quotes")


class BondTrade(Base, TimestampMixin, SourceMixin):
    __tablename__ = "bond_trades"
    __table_args__ = (
        Index("ix_trades_bond_ts", "bond_id", "timestamp"),
        UniqueConstraint("fingerprint", name="uq_trade_fingerprint"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[str | None] = mapped_column(String(64), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    price: Mapped[float | None] = mapped_column(Float)
    clean_price: Mapped[float | None] = mapped_column(Float)
    ytm: Mapped[float | None] = mapped_column(Float)
    quantity: Mapped[float | None] = mapped_column(Float)
    amount: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str | None] = mapped_column(String(3))
    settlement: Mapped[str | None] = mapped_column(String(16))
    data_mode: Mapped[str] = mapped_column(String(16))
    fingerprint: Mapped[str | None] = mapped_column(String(64), index=True)

    bond: Mapped["Bond"] = relationship(back_populates="trades")


class BondQuoteCurrent(Base, TimestampMixin, SourceMixin):
    """One current quote per bond; ``BondQuote`` remains meaningful history."""

    __tablename__ = "bond_quote_current"
    __table_args__ = (UniqueConstraint("bond_id", name="uq_quote_current_bond"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(ForeignKey("bonds.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    bid_volume: Mapped[float | None] = mapped_column(Float)
    ask_volume: Mapped[float | None] = mapped_column(Float)
    last: Mapped[float | None] = mapped_column(Float)
    clean_price: Mapped[float | None] = mapped_column(Float)
    dirty_price: Mapped[float | None] = mapped_column(Float)
    accrued_interest: Mapped[float | None] = mapped_column(Float)
    ytm: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    number_of_trades: Mapped[int | None] = mapped_column(Integer)
    data_mode: Mapped[str] = mapped_column(String(16))
    last_checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    last_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    content_hash: Mapped[str] = mapped_column(String(64), index=True)
