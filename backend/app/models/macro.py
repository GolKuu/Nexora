from __future__ import annotations

from datetime import date

from sqlalchemy import Date, Float, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SourceMixin, TimestampMixin


class YieldCurve(Base, TimestampMixin, SourceMixin):
    """Risk-free curve points, used for credit spread calculation."""

    __tablename__ = "yield_curves"
    __table_args__ = (
        Index("ix_curve_lookup", "curve_code", "as_of_date", "tenor_years"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    curve_code: Mapped[str] = mapped_column(String(32), default="KZ_GOV")
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    tenor_years: Mapped[float] = mapped_column(Float)
    yield_rate: Mapped[float] = mapped_column(Float)  # decimal


class InflationData(Base, TimestampMixin, SourceMixin):
    __tablename__ = "inflation_data"
    __table_args__ = (
        Index("ix_inflation_lookup", "country", "period_end", "kind"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    country: Mapped[str] = mapped_column(String(2), default="KZ")
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date, index=True)
    # official | forecast | manual
    kind: Mapped[str] = mapped_column(String(16), default="official")
    # annualised rate as decimal, e.g. 0.089 == 8.9 %
    annual_rate: Mapped[float] = mapped_column(Float)
    monthly_rate: Mapped[float | None] = mapped_column(Float)
    horizon_years: Mapped[float | None] = mapped_column(Float)
    note: Mapped[str | None] = mapped_column(String(512))


class FxRate(Base, TimestampMixin, SourceMixin):
    __tablename__ = "fx_rates"
    __table_args__ = (Index("ix_fx_pair_date", "base_currency", "quote_currency", "as_of_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3))
    quote_currency: Mapped[str] = mapped_column(String(3))
    as_of_date: Mapped[date] = mapped_column(Date, index=True)
    rate: Mapped[float] = mapped_column(Float)
