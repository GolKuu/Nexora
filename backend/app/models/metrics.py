from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, ComputedMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.bond import Bond


class BondMetric(Base, TimestampMixin, ComputedMixin):
    """Everything the calculation engine derives for one bond at one moment.

    Nothing here is ever produced by an LLM.
    """

    __tablename__ = "bond_metrics"
    __table_args__ = (Index("ix_metrics_bond_ts", "bond_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bond_id: Mapped[int] = mapped_column(
        ForeignKey("bonds.id", ondelete="CASCADE"), index=True
    )
    quote_id: Mapped[int | None] = mapped_column(
        ForeignKey("bond_quotes.id", ondelete="SET NULL")
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    clean_price: Mapped[float | None] = mapped_column(Float)
    dirty_price: Mapped[float | None] = mapped_column(Float)
    accrued_interest: Mapped[float | None] = mapped_column(Float)

    current_yield: Mapped[float | None] = mapped_column(Float)
    ytm: Mapped[float | None] = mapped_column(Float)
    ytm_source: Mapped[str | None] = mapped_column(String(32))  # market | calculated

    macaulay_duration: Mapped[float | None] = mapped_column(Float)
    modified_duration: Mapped[float | None] = mapped_column(Float)
    convexity: Mapped[float | None] = mapped_column(Float)

    credit_spread: Mapped[float | None] = mapped_column(Float)
    risk_free_rate: Mapped[float | None] = mapped_column(Float)
    bid_ask_spread: Mapped[float | None] = mapped_column(Float)
    bid_ask_spread_pct: Mapped[float | None] = mapped_column(Float)

    pull_to_par: Mapped[float | None] = mapped_column(Float)
    years_to_maturity: Mapped[float | None] = mapped_column(Float)

    real_ytm: Mapped[float | None] = mapped_column(Float)
    inflation_rate_used: Mapped[float | None] = mapped_column(Float)
    inflation_source_used: Mapped[str | None] = mapped_column(String(32))

    avg_daily_turnover_30d: Mapped[float | None] = mapped_column(Float)
    trading_days_30d: Mapped[float | None] = mapped_column(Float)
    price_volatility_90d: Mapped[float | None] = mapped_column(Float)

    data_mode: Mapped[str | None] = mapped_column(String(16))

    bond: Mapped["Bond"] = relationship(back_populates="metrics")
