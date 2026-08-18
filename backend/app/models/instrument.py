from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, SourceMixin, TimestampMixin

if TYPE_CHECKING:
    from app.models.issuer import Issuer
    from app.models.stock import Stock

#: Every instrument type that is a share on KASE. Preferred shares are listed,
#: traded and discovered exactly like ordinary ones, so discovery, backfill,
#: monitoring and the admin counts must all agree on this single definition -
#: otherwise a preferred share is backfilled and then silently never observed
#: again.
SHARE_INSTRUMENT_TYPES: tuple[str, ...] = ("stock", "preferred_stock")


class Instrument(Base, TimestampMixin, SourceMixin):
    """Identity shared by asset classes; analytics remain asset-specific."""

    __tablename__ = "instruments"
    __table_args__ = (
        UniqueConstraint("instrument_type", "ticker", name="uq_instrument_type_ticker"),
        Index("ix_instruments_type_active", "instrument_type", "is_active"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(64), index=True)
    isin: Mapped[str | None] = mapped_column(String(16), index=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.id", ondelete="RESTRICT"), index=True)
    instrument_type: Mapped[str] = mapped_column(String(24), index=True)
    security_type: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    market_segment: Mapped[str | None] = mapped_column(String(64))
    listing_status: Mapped[str | None] = mapped_column(String(32))
    kase_url: Mapped[str | None] = mapped_column(String(1024))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)

    issuer: Mapped["Issuer"] = relationship(back_populates="instruments")
    stock: Mapped["Stock | None"] = relationship(back_populates="instrument", uselist=False)
