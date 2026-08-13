from __future__ import annotations

from datetime import date
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

from app.db.base import Base, ComputedMixin, SourceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.issuer import Issuer


class FinancialStatement(Base, TimestampMixin, SourceMixin):
    """Reported figures. Absent lines stay NULL - never zero."""

    __tablename__ = "financial_statements"
    __table_args__ = (
        Index("ix_statements_issuer_period", "issuer_id", "period_end", "period_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer_id: Mapped[int] = mapped_column(
        ForeignKey("issuers.id", ondelete="CASCADE"), index=True
    )
    period_end: Mapped[date] = mapped_column(Date)
    period_type: Mapped[str] = mapped_column(String(8))  # FY | H1 | Q1..Q4
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    is_audited: Mapped[bool | None] = mapped_column(Boolean)
    is_consolidated: Mapped[bool | None] = mapped_column(Boolean)
    standard: Mapped[str | None] = mapped_column(String(16))  # IFRS | NAS

    # income statement
    revenue: Mapped[float | None] = mapped_column(Float)
    operating_profit: Mapped[float | None] = mapped_column(Float)
    ebitda: Mapped[float | None] = mapped_column(Float)
    net_profit: Mapped[float | None] = mapped_column(Float)
    interest_expense: Mapped[float | None] = mapped_column(Float)

    # balance sheet
    total_assets: Mapped[float | None] = mapped_column(Float)
    total_equity: Mapped[float | None] = mapped_column(Float)
    #: Everything owed, not just borrowings. This is what KASE publishes; it
    #: is a superset of ``total_debt`` and the two must not be conflated.
    total_liabilities: Mapped[float | None] = mapped_column(Float)
    total_debt: Mapped[float | None] = mapped_column(Float)
    short_term_debt: Mapped[float | None] = mapped_column(Float)
    long_term_debt: Mapped[float | None] = mapped_column(Float)
    cash_and_equivalents: Mapped[float | None] = mapped_column(Float)
    current_assets: Mapped[float | None] = mapped_column(Float)
    current_liabilities: Mapped[float | None] = mapped_column(Float)
    inventory: Mapped[float | None] = mapped_column(Float)

    # cash flow
    operating_cash_flow: Mapped[float | None] = mapped_column(Float)
    investing_cash_flow: Mapped[float | None] = mapped_column(Float)
    financing_cash_flow: Mapped[float | None] = mapped_column(Float)
    capex: Mapped[float | None] = mapped_column(Float)
    free_cash_flow: Mapped[float | None] = mapped_column(Float)

    # bank-specific (financial institutions use a separate credit model)
    net_interest_income: Mapped[float | None] = mapped_column(Float)
    net_fee_income: Mapped[float | None] = mapped_column(Float)
    loans_gross: Mapped[float | None] = mapped_column(Float)
    loans_net: Mapped[float | None] = mapped_column(Float)
    npl_amount: Mapped[float | None] = mapped_column(Float)
    loan_loss_provisions: Mapped[float | None] = mapped_column(Float)
    customer_deposits: Mapped[float | None] = mapped_column(Float)
    liquid_assets: Mapped[float | None] = mapped_column(Float)
    tier1_capital: Mapped[float | None] = mapped_column(Float)
    total_capital: Mapped[float | None] = mapped_column(Float)
    risk_weighted_assets: Mapped[float | None] = mapped_column(Float)
    capital_adequacy_ratio: Mapped[float | None] = mapped_column(Float)

    issuer: Mapped["Issuer"] = relationship(back_populates="statements")


class IssuerMetric(Base, TimestampMixin, ComputedMixin):
    """Derived credit ratios for one reporting period."""

    __tablename__ = "issuer_metrics"
    __table_args__ = (Index("ix_issuer_metrics_period", "issuer_id", "period_end"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer_id: Mapped[int] = mapped_column(
        ForeignKey("issuers.id", ondelete="CASCADE"), index=True
    )
    statement_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_statements.id", ondelete="SET NULL")
    )
    period_end: Mapped[date] = mapped_column(Date)
    # "corporate" or "bank" - which credit model produced these numbers
    model_kind: Mapped[str] = mapped_column(String(16), default="corporate")

    debt_to_ebitda: Mapped[float | None] = mapped_column(Float)
    net_debt_to_ebitda: Mapped[float | None] = mapped_column(Float)
    debt_to_equity: Mapped[float | None] = mapped_column(Float)
    interest_coverage: Mapped[float | None] = mapped_column(Float)
    current_ratio: Mapped[float | None] = mapped_column(Float)
    quick_ratio: Mapped[float | None] = mapped_column(Float)
    operating_cash_flow: Mapped[float | None] = mapped_column(Float)
    free_cash_flow: Mapped[float | None] = mapped_column(Float)
    roa: Mapped[float | None] = mapped_column(Float)
    roe: Mapped[float | None] = mapped_column(Float)
    ebitda_margin: Mapped[float | None] = mapped_column(Float)
    net_margin: Mapped[float | None] = mapped_column(Float)
    revenue_growth: Mapped[float | None] = mapped_column(Float)
    profit_growth: Mapped[float | None] = mapped_column(Float)

    # bank model
    capital_adequacy_ratio: Mapped[float | None] = mapped_column(Float)
    tier1_ratio: Mapped[float | None] = mapped_column(Float)
    npl_ratio: Mapped[float | None] = mapped_column(Float)
    provision_coverage: Mapped[float | None] = mapped_column(Float)
    loan_to_deposit: Mapped[float | None] = mapped_column(Float)
    liquid_assets_ratio: Mapped[float | None] = mapped_column(Float)
    net_interest_margin: Mapped[float | None] = mapped_column(Float)
    cost_to_income: Mapped[float | None] = mapped_column(Float)
    equity_to_assets: Mapped[float | None] = mapped_column(Float)

    issuer: Mapped["Issuer"] = relationship(back_populates="metrics")


class CreditRating(Base, TimestampMixin, SourceMixin):
    __tablename__ = "credit_ratings"
    __table_args__ = (Index("ix_ratings_issuer_agency", "issuer_id", "agency"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer_id: Mapped[int] = mapped_column(
        ForeignKey("issuers.id", ondelete="CASCADE"), index=True
    )
    agency: Mapped[str] = mapped_column(String(64))  # S&P | Moody's | Fitch | KASE
    rating: Mapped[str] = mapped_column(String(16))
    scale: Mapped[str | None] = mapped_column(String(32))  # international | national
    outlook: Mapped[str | None] = mapped_column(String(32))
    # 1 == AAA ... 21 == D. Used by the credit engine, never re-derived in the UI.
    numeric_grade: Mapped[int | None] = mapped_column(Integer)
    rating_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    comment: Mapped[str | None] = mapped_column(Text)

    issuer: Mapped["Issuer"] = relationship(back_populates="ratings")
