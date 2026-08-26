from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import InflationSource, RiskProfile, UiMode
from app.db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from app.models.portfolio import Alert, Portfolio, Watchlist


class User(Base, TimestampMixin):
    """A registered user.

    Anonymous usage is fully supported: TOP lists, search, bond cards, compare
    and the calculator never require a user row.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str | None] = mapped_column(String(120))
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    settings: Mapped["UserSettings | None"] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    portfolios: Mapped[list["Portfolio"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    watchlist: Mapped[list["Watchlist"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSettings(Base, TimestampMixin):
    __tablename__ = "user_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    # Anonymous visitors get a settings row keyed by a client-generated token.
    anonymous_token: Mapped[str | None] = mapped_column(
        String(64), unique=True, index=True
    )

    inflation_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    inflation_source: Mapped[str] = mapped_column(
        String(32), default=InflationSource.AUTOMATIC.value
    )
    manual_inflation_rate: Mapped[float | None] = mapped_column(Float, default=None)
    show_real_return: Mapped[bool] = mapped_column(Boolean, default=True)
    base_currency: Mapped[str] = mapped_column(String(3), default="KZT")
    ui_mode: Mapped[str] = mapped_column(String(16), default=UiMode.SIMPLE.value)
    risk_profile: Mapped[str] = mapped_column(
        String(16), default=RiskProfile.BALANCED.value
    )
    theme: Mapped[str] = mapped_column(String(16), default="system")
    remember_calculator_amount: Mapped[bool] = mapped_column(Boolean, default=True)
    calculator_amount: Mapped[float | None] = mapped_column(Float, default=None)
    language: Mapped[str] = mapped_column(String(8), default="ru")
    conservative_missing_data_mode: Mapped[bool] = mapped_column(Boolean, default=True)
    news_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    kase_news_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    external_news_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    chart_news_markers_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    forecast_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    uncertainty_intervals_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    show_dcf_confidence: Mapped[bool] = mapped_column(Boolean, default=True)
    show_dcf_scenario_differences: Mapped[bool] = mapped_column(Boolean, default=True)
    default_chart_range: Mapped[str] = mapped_column(String(8), default="1y")

    user: Mapped["User | None"] = relationship(back_populates="settings")
