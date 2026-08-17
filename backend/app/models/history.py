"""Permanent, source-traceable market history.

Three rules shape every table here:

1. **Nothing is invented.** A day with no trade is recorded as ``no_trade``, not
   as yesterday's price repeated. A source that exposes one price per day gets
   one price - open/high/low stay NULL rather than being manufactured from it.
2. **Nothing is deleted.** The two-year backfill is only the *initial* window;
   observations are never pruned for being old, and a delisted instrument keeps
   its entire history.
3. **Everything can be traced.** Source, URL, source timestamp, fetch time,
   parser version and data mode travel with every row, so the application can
   always answer "where did this number come from?".
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, SourceMixin, TimestampMixin


class MarketObservation(Base, TimestampMixin, SourceMixin):
    """One factual market reading for one instrument at one moment.

    Both the backfill and the ten-minute live monitoring write here, which is
    what makes the transition from imported history to accumulating history
    seamless: same table, same fingerprint rule, no seam to reconcile.
    """

    __tablename__ = "market_observations"
    __table_args__ = (
        # The fingerprint makes re-running the backfill a no-op instead of a
        # duplicate. It is derived from instrument + timestamp + the values.
        UniqueConstraint("instrument_id", "fingerprint", name="uq_market_observation"),
        Index("ix_market_observations_instrument_observed", "instrument_id", "observed_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    trading_date: Mapped[date | None] = mapped_column(Date, index=True)

    price: Mapped[float | None] = mapped_column(Float)
    bid: Mapped[float | None] = mapped_column(Float)
    ask: Mapped[float | None] = mapped_column(Float)
    bid_volume: Mapped[float | None] = mapped_column(Float)
    ask_volume: Mapped[float | None] = mapped_column(Float)
    spread: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    trade_count: Mapped[int | None] = mapped_column(Integer)

    # Only populated when the source actually exposes them. A single daily
    # price never becomes four identical OHLC numbers.
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    previous_close: Mapped[float | None] = mapped_column(Float)

    #: traded | no_trade | market_closed | data_unavailable
    status: Mapped[str] = mapped_column(String(24), default="traded", index=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)
    parser_version: Mapped[str | None] = mapped_column(String(32))
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)


class DailyMarketSnapshot(Base, TimestampMixin, SourceMixin):
    """One trading day, aggregated from the observations actually collected.

    OHLC is computed from real observations only. If the day holds a single
    price, ``open``/``high``/``low`` stay NULL - a flat candle drawn from one
    number is a fabrication, not a summary.
    """

    __tablename__ = "daily_market_snapshots"
    __table_args__ = (
        UniqueConstraint("instrument_id", "trading_date", name="uq_daily_market_snapshot"),
        Index("ix_daily_snapshots_instrument_date", "instrument_id", "trading_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    trading_date: Mapped[date] = mapped_column(Date, index=True)

    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    turnover: Mapped[float | None] = mapped_column(Float)
    trade_count: Mapped[int | None] = mapped_column(Integer)
    bid_close: Mapped[float | None] = mapped_column(Float)
    ask_close: Mapped[float | None] = mapped_column(Float)

    first_observation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_observation_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observation_count: Mapped[int] = mapped_column(Integer, default=0)
    #: full | single_price | quote_only | no_trade | unavailable
    coverage_quality: Mapped[str] = mapped_column(String(24), default="full", index=True)
    status: Mapped[str] = mapped_column(String(24), default="traded", index=True)
    data_mode: Mapped[str] = mapped_column(String(16), index=True)


class HistoricalTrade(Base, TimestampMixin, SourceMixin):
    """An individual historical trade. Append-only.

    Where KASE publishes an official trade id it is the stable key; otherwise a
    fingerprint over instrument, timestamp, price and quantity keeps repeated
    backfills from duplicating the same deal.
    """

    __tablename__ = "historical_trades"
    __table_args__ = (
        UniqueConstraint("instrument_id", "fingerprint", name="uq_historical_trade"),
        Index("ix_historical_trades_instrument_ts", "instrument_id", "trade_timestamp"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    trade_id: Mapped[str | None] = mapped_column(String(64), index=True)
    trade_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    trading_date: Mapped[date | None] = mapped_column(Date, index=True)
    price: Mapped[float] = mapped_column(Float)
    quantity: Mapped[float | None] = mapped_column(Float)
    trade_value: Mapped[float | None] = mapped_column(Float)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    parser_version: Mapped[str | None] = mapped_column(String(32))
    data_mode: Mapped[str] = mapped_column(String(16), index=True)


class HistoricalCoverage(Base, TimestampMixin):
    """What we asked for versus what KASE actually exposed.

    Without this the UI would happily draw a two-year chart from four months of
    data. Coverage is measured, published through the API, and never rounded up.
    """

    __tablename__ = "historical_coverage"
    __table_args__ = (
        UniqueConstraint("instrument_id", "job_type", name="uq_historical_coverage"),
        Index("ix_historical_coverage_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    #: market | trades | quotes | financials | news | corporate_actions | all
    job_type: Mapped[str] = mapped_column(String(32), default="all", index=True)

    requested_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    requested_end: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actual_market_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    actual_market_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    market_days_expected: Mapped[int | None] = mapped_column(Integer)
    market_days_covered: Mapped[int | None] = mapped_column(Integer)
    trade_history_coverage: Mapped[float | None] = mapped_column(Float)
    quote_history_coverage: Mapped[float | None] = mapped_column(Float)
    financial_history_coverage: Mapped[float | None] = mapped_column(Float)
    news_history_coverage: Mapped[float | None] = mapped_column(Float)
    corporate_action_coverage: Mapped[float | None] = mapped_column(Float)

    last_backfilled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: complete | partial | unavailable | failed
    status: Mapped[str] = mapped_column(String(16), default="partial", index=True)
    details: Mapped[dict | None] = mapped_column(JSON)


class BackfillCheckpoint(Base, TimestampMixin):
    """Resume state for a long historical crawl.

    A backfill that dies two thirds of the way through a two-year window must
    resume where it stopped, not start again from the beginning - both to finish
    at all and to keep the request volume against kase.kz honest.
    """

    __tablename__ = "backfill_checkpoints"
    __table_args__ = (
        UniqueConstraint("job_type", "instrument_id", name="uq_backfill_checkpoint"),
        Index("ix_backfill_checkpoints_status", "status", "updated_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_processed_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_processed_cursor: Mapped[str | None] = mapped_column(String(255))
    #: queued | processing | completed | partial | failed | blocked
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=500, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class IngestionAnomaly(Base, TimestampMixin):
    """A parse result that looked wrong enough not to be stored as fact.

    When a page redesign turns prices into navigation labels, the correct
    outcome is a recorded anomaly and the previous validated data left intact -
    not a history full of impossible numbers.
    """

    __tablename__ = "ingestion_anomalies"
    __table_args__ = (
        Index("ix_ingestion_anomalies_instrument_created", "instrument_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str | None] = mapped_column(String(64), index=True)
    job_type: Mapped[str] = mapped_column(String(32), index=True)
    #: impossible_price | broken_timestamp | wrong_ticker | missing_fields |
    #: unrealistic_move | blocked | parser_failure
    kind: Mapped[str] = mapped_column(String(32), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="high", index=True)
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict | None] = mapped_column(JSON)
    source: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    parser_version: Mapped[str | None] = mapped_column(String(32))
    resolved: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class HistoricalCorrection(Base, TimestampMixin):
    """KASE restated something we had already stored.

    The corrected value is what the application shows; the original stays here
    so the change is auditable rather than invisible.
    """

    __tablename__ = "historical_corrections"
    __table_args__ = (
        Index("ix_historical_corrections_instrument_field", "instrument_id", "field"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    record_type: Mapped[str] = mapped_column(String(32), index=True)
    record_id: Mapped[int | None] = mapped_column(Integer)
    field: Mapped[str] = mapped_column(String(64))
    original_value: Mapped[str | None] = mapped_column(Text)
    corrected_value: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[date | None] = mapped_column(Date)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    reason: Mapped[str | None] = mapped_column(Text)


class FinancialReportRelease(Base, TimestampMixin, SourceMixin):
    """A published financial report, versioned by content.

    An older statement is never overwritten by a newer one. A restatement of the
    same period arrives as a new row with a higher ``version`` and its own
    ``available_at`` - which is also what lets point-in-time scoring answer
    "what did we know on this date?" honestly.
    """

    __tablename__ = "financial_report_releases"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "reporting_period", "document_hash",
            name="uq_financial_report_release",
        ),
        Index("ix_financial_reports_instrument_period", "instrument_id", "reporting_period"),
        Index("ix_financial_reports_available", "instrument_id", "available_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    #: The period the report covers, e.g. 2025-12-31.
    reporting_period: Mapped[date] = mapped_column(Date, index=True)
    period_type: Mapped[str | None] = mapped_column(String(8))
    publication_date: Mapped[date | None] = mapped_column(Date)
    #: When the report became usable by the analysis pipeline. Point-in-time
    #: scoring compares against this, never against ``reporting_period``.
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    document_url: Mapped[str | None] = mapped_column(String(1024))
    document_hash: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_restatement: Mapped[bool] = mapped_column(Boolean, default=False)
    title: Mapped[str | None] = mapped_column(String(1024))
    parser_version: Mapped[str | None] = mapped_column(String(32))


class DividendEvent(Base, TimestampMixin, SourceMixin):
    """A published dividend decision, with its full official date set.

    Separate from the summary on ``stocks``: this is the immutable historical
    record, and future dividends are never inferred from past ones.
    """

    __tablename__ = "dividend_events"
    __table_args__ = (
        UniqueConstraint("instrument_id", "fingerprint", name="uq_dividend_event_fingerprint"),
        Index("ix_dividend_events_instrument_ex", "instrument_id", "ex_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), index=True
    )
    announcement_date: Mapped[date | None] = mapped_column(Date)
    ex_date: Mapped[date | None] = mapped_column(Date, index=True)
    record_date: Mapped[date | None] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date)
    amount_per_share: Mapped[float | None] = mapped_column(Float)
    currency: Mapped[str] = mapped_column(String(3), default="KZT")
    #: announced | approved | paid | cancelled | unknown
    status: Mapped[str] = mapped_column(String(16), default="announced", index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    parser_version: Mapped[str | None] = mapped_column(String(32))
