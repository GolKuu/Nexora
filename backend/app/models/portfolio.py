from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.bond import Bond
    from app.models.stock import Stock
    from app.models.user import User


class Portfolio(Base, TimestampMixin):
    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    anonymous_token: Mapped[str | None] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(255), default="Мой портфель")
    base_currency: Mapped[str] = mapped_column(String(3), default="KZT")
    description: Mapped[str | None] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped["User | None"] = relationship(back_populates="portfolios")
    positions: Mapped[list["PortfolioPosition"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class PortfolioPosition(Base, TimestampMixin):
    __tablename__ = "portfolio_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    bond_id: Mapped[int | None] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    stock_id: Mapped[int | None] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    instrument_type: Mapped[str] = mapped_column(String(16), default="bond", index=True)
    quantity: Mapped[float] = mapped_column(Float)
    purchase_clean_price: Mapped[float | None] = mapped_column(Float)
    purchase_price: Mapped[float | None] = mapped_column(Float)
    purchase_date: Mapped[date | None] = mapped_column(Date)
    purchase_accrued_interest: Mapped[float | None] = mapped_column(Float)
    fees: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(Text)

    portfolio: Mapped["Portfolio"] = relationship(back_populates="positions")
    bond: Mapped["Bond | None"] = relationship()
    stock: Mapped["Stock | None"] = relationship()


class Watchlist(Base, TimestampMixin):
    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("user_id", "bond_id", name="uq_watchlist_user_bond"),
        UniqueConstraint(
            "anonymous_token", "bond_id", name="uq_watchlist_anon_bond"
        ),
        UniqueConstraint("user_id", "stock_id", name="uq_watchlist_user_stock"),
        UniqueConstraint("anonymous_token", "stock_id", name="uq_watchlist_anon_stock"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    anonymous_token: Mapped[str | None] = mapped_column(String(64), index=True)
    bond_id: Mapped[int | None] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    stock_id: Mapped[int | None] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    instrument_type: Mapped[str] = mapped_column(String(16), default="bond", index=True)
    note: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User | None"] = relationship(back_populates="watchlist")
    bond: Mapped["Bond | None"] = relationship()
    stock: Mapped["Stock | None"] = relationship()


class Alert(Base, TimestampMixin):
    __tablename__ = "alerts"
    __table_args__ = (
        Index("ix_alerts_active", "is_active", "bond_id"),
        Index("ix_alerts_stock_active", "is_active", "stock_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    bond_id: Mapped[int | None] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    stock_id: Mapped[int | None] = mapped_column(
        ForeignKey("stocks.id", ondelete="CASCADE"), index=True
    )
    instrument_type: Mapped[str] = mapped_column(String(16), default="bond", index=True)
    kind: Mapped[str] = mapped_column(String(32))
    threshold: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User"] = relationship(back_populates="alerts")
    bond: Mapped["Bond | None"] = relationship()
    stock: Mapped["Stock | None"] = relationship()
