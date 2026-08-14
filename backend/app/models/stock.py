from __future__ import annotations

from datetime import date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, ComputedMixin, SourceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.instrument import Instrument


class Stock(Base, TimestampMixin, SourceMixin):
    __tablename__ = "stocks"
    __table_args__ = (Index("ix_stocks_instrument_id", "instrument_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), unique=True)
    share_class: Mapped[str | None] = mapped_column(String(32))
    shares_outstanding: Mapped[float | None] = mapped_column(Float)
    free_float: Mapped[float | None] = mapped_column(Float)
    market_cap: Mapped[float | None] = mapped_column(Float)
    sector: Mapped[str | None] = mapped_column(String(64), index=True)
    industry: Mapped[str | None] = mapped_column(String(128), index=True)
    listing_date: Mapped[date | None] = mapped_column(Date)
    dividend_frequency: Mapped[int | None] = mapped_column(Integer)
    last_dividend: Mapped[float | None] = mapped_column(Float)
    last_dividend_date: Mapped[date | None] = mapped_column(Date)
    next_expected_dividend_date: Mapped[date | None] = mapped_column(Date)
    next_dividend_is_scenario: Mapped[bool] = mapped_column(Boolean, default=False)
    lot_size: Mapped[int] = mapped_column(Integer, default=1)
    liquidity_class: Mapped[int | None] = mapped_column(Integer)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    instrument: Mapped["Instrument"] = relationship(back_populates="stock")
    quotes: Mapped[list["StockQuote"]] = relationship(back_populates="stock", cascade="all, delete-orphan")
    financials: Mapped[list["StockFinancialPeriod"]] = relationship(back_populates="stock", cascade="all, delete-orphan")
    dividends: Mapped[list["Dividend"]] = relationship(back_populates="stock", cascade="all, delete-orphan")
    metrics: Mapped[list["StockMetric"]] = relationship(back_populates="stock", cascade="all, delete-orphan")
    scores: Mapped[list["StockScore"]] = relationship(back_populates="stock", cascade="all, delete-orphan")


class StockQuote(Base, TimestampMixin, SourceMixin):
    __tablename__ = "stock_quotes"
    __table_args__ = (Index("ix_stock_quotes_stock_ts", "stock_id", "timestamp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    bid_volume: Mapped[float | None] = mapped_column(Float)
    ask_volume: Mapped[float | None] = mapped_column(Float)
    last: Mapped[float | None] = mapped_column(Float)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    previous_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    number_of_trades: Mapped[int | None] = mapped_column(Integer)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)

    stock: Mapped["Stock"] = relationship(back_populates="quotes")


class StockFinancialPeriod(Base, TimestampMixin, SourceMixin):
    __tablename__ = "stock_financial_periods"
    __table_args__ = (
        UniqueConstraint("stock_id", "period_end", "period_type", name="uq_stock_financial_period"),
        Index("ix_stock_financials_stock_period", "stock_id", "period_end"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(8))
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    is_audited: Mapped[bool | None] = mapped_column(Boolean)
    revenue: Mapped[float | None] = mapped_column(Float)
    ebitda: Mapped[float | None] = mapped_column(Float)
    operating_profit: Mapped[float | None] = mapped_column(Float)
    net_income: Mapped[float | None] = mapped_column(Float)
    total_assets: Mapped[float | None] = mapped_column(Float)
    total_equity: Mapped[float | None] = mapped_column(Float)
    total_debt: Mapped[float | None] = mapped_column(Float)
    cash: Mapped[float | None] = mapped_column(Float)
    operating_cash_flow: Mapped[float | None] = mapped_column(Float)
    free_cash_flow: Mapped[float | None] = mapped_column(Float)
    eps: Mapped[float | None] = mapped_column(Float)
    book_value: Mapped[float | None] = mapped_column(Float)
    shares_outstanding: Mapped[float | None] = mapped_column(Float)
    # Bank-only lines. They remain NULL for non-banks.
    capital_adequacy: Mapped[float | None] = mapped_column(Float)
    npl_ratio: Mapped[float | None] = mapped_column(Float)
    loans: Mapped[float | None] = mapped_column(Float)
    deposits: Mapped[float | None] = mapped_column(Float)
    net_interest_margin: Mapped[float | None] = mapped_column(Float)
    cost_to_income: Mapped[float | None] = mapped_column(Float)
    provisions: Mapped[float | None] = mapped_column(Float)

    stock: Mapped["Stock"] = relationship(back_populates="financials")


class Dividend(Base, TimestampMixin, SourceMixin):
    __tablename__ = "dividends"
    __table_args__ = (UniqueConstraint("stock_id", "record_date", "dividend_per_share", name="uq_dividend_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    ex_date: Mapped[date | None] = mapped_column(Date)
    record_date: Mapped[date | None] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date)
    dividend_per_share: Mapped[float] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    status: Mapped[str] = mapped_column(String(16), default="unknown", index=True)

    stock: Mapped["Stock"] = relationship(back_populates="dividends")


class StockMetric(Base, TimestampMixin, ComputedMixin):
    __tablename__ = "stock_metrics"
    __table_args__ = (Index("ix_stock_metrics_stock_asof", "stock_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    pe: Mapped[float | None] = mapped_column(Float)
    pb: Mapped[float | None] = mapped_column(Float)
    ev_ebitda: Mapped[float | None] = mapped_column(Float)
    fcf_yield: Mapped[float | None] = mapped_column(Float)
    trailing_dividend_yield: Mapped[float | None] = mapped_column(Float)
    forward_dividend_yield: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)
    roa: Mapped[float | None] = mapped_column(Float)
    net_margin: Mapped[float | None] = mapped_column(Float)
    revenue_growth: Mapped[float | None] = mapped_column(Float)
    earnings_growth: Mapped[float | None] = mapped_column(Float)
    eps_growth: Mapped[float | None] = mapped_column(Float)
    net_debt: Mapped[float | None] = mapped_column(Float)
    volatility: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)

    stock: Mapped["Stock"] = relationship(back_populates="metrics")


class StockScore(Base, TimestampMixin):
    __tablename__ = "stock_scores"
    __table_args__ = (Index("ix_stock_scores_stock_kind", "stock_id", "kind", "calculated_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    stock_id: Mapped[int] = mapped_column(ForeignKey("stocks.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)
    value: Mapped[float | None] = mapped_column(Float)
    version: Mapped[str] = mapped_column(String(32))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confidence: Mapped[float | None] = mapped_column(Float)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    inputs: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)

    stock: Mapped["Stock"] = relationship(back_populates="scores")
