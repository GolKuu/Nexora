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
    JSON,
    Integer,
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
    goal_id: Mapped[int | None] = mapped_column(
        ForeignKey("investment_goals.id", ondelete="SET NULL"), index=True
    )

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
    status: Mapped[str] = mapped_column(String(16), default="EXECUTED", index=True)
    goal_id: Mapped[int | None] = mapped_column(
        ForeignKey("investment_goals.id", ondelete="SET NULL"), index=True
    )
    planned_quantity: Mapped[float | None] = mapped_column(Float)
    planned_reference_price: Mapped[float | None] = mapped_column(Float)
    planned_allocation: Mapped[float | None] = mapped_column(Float)
    actual_quantity: Mapped[float | None] = mapped_column(Float)
    actual_price: Mapped[float | None] = mapped_column(Float)
    actual_commission: Mapped[float | None] = mapped_column(Float)
    execution_date: Mapped[date | None] = mapped_column(Date)
    source_goal_plan_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("goal_plan_versions.id", ondelete="SET NULL"), index=True
    )

    portfolio: Mapped["Portfolio"] = relationship(back_populates="positions")
    bond: Mapped["Bond | None"] = relationship()
    stock: Mapped["Stock | None"] = relationship()


class InvestmentGoal(Base, TimestampMixin):
    __tablename__ = "investment_goals"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    anonymous_token: Mapped[str | None] = mapped_column(String(64), index=True)
    starting_capital: Mapped[float] = mapped_column(Float)
    target_type: Mapped[str] = mapped_column(String(16))
    target_amount: Mapped[float] = mapped_column(Float)
    target_final_value: Mapped[float] = mapped_column(Float)
    horizon_months: Mapped[int] = mapped_column(Integer)
    monthly_contribution: Mapped[float] = mapped_column(Float, default=0.0)
    risk_profile: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE", index=True)
    current_version: Mapped[int] = mapped_column(Integer, default=1)

    versions: Mapped[list["GoalPlanVersion"]] = relationship(
        back_populates="goal", cascade="all, delete-orphan", order_by="GoalPlanVersion.version"
    )


class GoalPlanVersion(Base, TimestampMixin):
    __tablename__ = "goal_plan_versions"
    __table_args__ = (UniqueConstraint("goal_id", "version", name="uq_goal_plan_version"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("investment_goals.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    methodology_version: Mapped[str] = mapped_column(String(32), default="goal-planner-1.0.0")
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    plan_snapshot: Mapped[dict] = mapped_column(JSON)

    goal: Mapped["InvestmentGoal"] = relationship(back_populates="versions")


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
    kind: Mapped[str] = mapped_column(String(32))
    threshold: Mapped[float | None] = mapped_column(Float)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message: Mapped[str | None] = mapped_column(Text)

    user: Mapped["User | None"] = relationship(back_populates="alerts")
    bond: Mapped["Bond | None"] = relationship()
    stock: Mapped["Stock | None"] = relationship()
