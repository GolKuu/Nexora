"""Permanent market history, coverage, checkpoints and anomalies.

Revision ID: a2b8e4c15d73
Revises: f1c7d3a90b42
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a2b8e4c15d73"
down_revision: str | None = "f1c7d3a90b42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def _source() -> list[sa.Column]:
    return [
        sa.Column("source", sa.String(64)),
        sa.Column("source_identifier", sa.String(255)),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
    ]


def _instrument_fk() -> sa.Column:
    return sa.Column(
        "instrument_id", sa.Integer(),
        sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False,
    )


def upgrade() -> None:
    op.create_table(
        "market_observations",
        sa.Column("id", sa.Integer(), primary_key=True),
        _instrument_fk(),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trading_date", sa.Date()),
        sa.Column("price", sa.Float()), sa.Column("bid", sa.Float()), sa.Column("ask", sa.Float()),
        sa.Column("bid_volume", sa.Float()), sa.Column("ask_volume", sa.Float()),
        sa.Column("spread", sa.Float()), sa.Column("volume", sa.Float()),
        sa.Column("turnover", sa.Float()), sa.Column("trade_count", sa.Integer()),
        sa.Column("open", sa.Float()), sa.Column("high", sa.Float()), sa.Column("low", sa.Float()),
        sa.Column("close", sa.Float()), sa.Column("previous_close", sa.Float()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("parser_version", sa.String(32)),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        *_source(), *_timestamps(),
        sa.UniqueConstraint("instrument_id", "fingerprint", name="uq_market_observation"),
    )
    op.create_table(
        "daily_market_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        _instrument_fk(),
        sa.Column("trading_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Float()), sa.Column("high", sa.Float()), sa.Column("low", sa.Float()),
        sa.Column("close", sa.Float()), sa.Column("volume", sa.Float()),
        sa.Column("turnover", sa.Float()), sa.Column("trade_count", sa.Integer()),
        sa.Column("bid_close", sa.Float()), sa.Column("ask_close", sa.Float()),
        sa.Column("first_observation_at", sa.DateTime(timezone=True)),
        sa.Column("last_observation_at", sa.DateTime(timezone=True)),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("coverage_quality", sa.String(24), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("data_mode", sa.String(16), nullable=False),
        *_source(), *_timestamps(),
        sa.UniqueConstraint("instrument_id", "trading_date", name="uq_daily_market_snapshot"),
    )
    op.create_table(
        "historical_trades",
        sa.Column("id", sa.Integer(), primary_key=True),
        _instrument_fk(),
        sa.Column("trade_id", sa.String(64)),
        sa.Column("trade_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("trading_date", sa.Date()),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("quantity", sa.Float()), sa.Column("trade_value", sa.Float()),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(32)),
        sa.Column("data_mode", sa.String(16), nullable=False),
        *_source(), *_timestamps(),
        sa.UniqueConstraint("instrument_id", "fingerprint", name="uq_historical_trade"),
    )
    op.create_table(
        "historical_coverage",
        sa.Column("id", sa.Integer(), primary_key=True),
        _instrument_fk(),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("requested_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actual_market_start", sa.DateTime(timezone=True)),
        sa.Column("actual_market_end", sa.DateTime(timezone=True)),
        sa.Column("market_days_expected", sa.Integer()),
        sa.Column("market_days_covered", sa.Integer()),
        sa.Column("trade_history_coverage", sa.Float()),
        sa.Column("quote_history_coverage", sa.Float()),
        sa.Column("financial_history_coverage", sa.Float()),
        sa.Column("news_history_coverage", sa.Float()),
        sa.Column("corporate_action_coverage", sa.Float()),
        sa.Column("last_backfilled_at", sa.DateTime(timezone=True)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("details", sa.JSON()),
        *_timestamps(),
        sa.UniqueConstraint("instrument_id", "job_type", name="uq_historical_coverage"),
    )
    op.create_table(
        "backfill_checkpoints",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("instrument_id", sa.Integer(),
                  sa.ForeignKey("instruments.id", ondelete="CASCADE")),
        sa.Column("range_start", sa.DateTime(timezone=True)),
        sa.Column("range_end", sa.DateTime(timezone=True)),
        sa.Column("last_processed_timestamp", sa.DateTime(timezone=True)),
        sa.Column("last_processed_cursor", sa.String(255)),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.UniqueConstraint("job_type", "instrument_id", name="uq_backfill_checkpoint"),
    )
    op.create_table(
        "ingestion_anomalies",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(),
                  sa.ForeignKey("instruments.id", ondelete="CASCADE")),
        sa.Column("ticker", sa.String(64)),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("severity", sa.String(16), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("payload", sa.JSON()),
        sa.Column("source", sa.String(64)),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("parser_version", sa.String(32)),
        sa.Column("resolved", sa.Boolean(), nullable=False),
        *_timestamps(),
    )
    op.create_table(
        "historical_corrections",
        sa.Column("id", sa.Integer(), primary_key=True),
        _instrument_fk(),
        sa.Column("record_type", sa.String(32), nullable=False),
        sa.Column("record_id", sa.Integer()),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("original_value", sa.Text()),
        sa.Column("corrected_value", sa.Text()),
        sa.Column("effective_date", sa.Date()),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(64)),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("reason", sa.Text()),
        *_timestamps(),
    )
    op.create_table(
        "financial_report_releases",
        sa.Column("id", sa.Integer(), primary_key=True),
        _instrument_fk(),
        sa.Column("reporting_period", sa.Date(), nullable=False),
        sa.Column("period_type", sa.String(8)),
        sa.Column("publication_date", sa.Date()),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("document_url", sa.String(1024)),
        sa.Column("document_hash", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_restatement", sa.Boolean(), nullable=False),
        sa.Column("title", sa.String(1024)),
        sa.Column("parser_version", sa.String(32)),
        *_source(), *_timestamps(),
        sa.UniqueConstraint("instrument_id", "reporting_period", "document_hash",
                            name="uq_financial_report_release"),
    )
    op.create_table(
        "dividend_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        _instrument_fk(),
        sa.Column("announcement_date", sa.Date()),
        sa.Column("ex_date", sa.Date()),
        sa.Column("record_date", sa.Date()),
        sa.Column("payment_date", sa.Date()),
        sa.Column("amount_per_share", sa.Float()),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(32)),
        *_source(), *_timestamps(),
        sa.UniqueConstraint("instrument_id", "fingerprint",
                            name="uq_dividend_event_fingerprint"),
    )

    for table, indexes in {
        "market_observations": [
            ("ix_market_observations_instrument_id", ["instrument_id"]),
            ("ix_market_observations_observed_at", ["observed_at"]),
            ("ix_market_observations_trading_date", ["trading_date"]),
            ("ix_market_observations_status", ["status"]),
            ("ix_market_observations_data_mode", ["data_mode"]),
            ("ix_market_observations_fingerprint", ["fingerprint"]),
            ("ix_market_observations_instrument_observed", ["instrument_id", "observed_at"]),
        ],
        "daily_market_snapshots": [
            ("ix_daily_market_snapshots_instrument_id", ["instrument_id"]),
            ("ix_daily_market_snapshots_trading_date", ["trading_date"]),
            ("ix_daily_market_snapshots_coverage_quality", ["coverage_quality"]),
            ("ix_daily_market_snapshots_status", ["status"]),
            ("ix_daily_market_snapshots_data_mode", ["data_mode"]),
            ("ix_daily_snapshots_instrument_date", ["instrument_id", "trading_date"]),
        ],
        "historical_trades": [
            ("ix_historical_trades_instrument_id", ["instrument_id"]),
            ("ix_historical_trades_trade_id", ["trade_id"]),
            ("ix_historical_trades_trade_timestamp", ["trade_timestamp"]),
            ("ix_historical_trades_trading_date", ["trading_date"]),
            ("ix_historical_trades_fingerprint", ["fingerprint"]),
            ("ix_historical_trades_data_mode", ["data_mode"]),
            ("ix_historical_trades_instrument_ts", ["instrument_id", "trade_timestamp"]),
        ],
        "historical_coverage": [
            ("ix_historical_coverage_instrument_id", ["instrument_id"]),
            ("ix_historical_coverage_job_type", ["job_type"]),
            ("ix_historical_coverage_status", ["status"]),
        ],
        "backfill_checkpoints": [
            ("ix_backfill_checkpoints_job_type", ["job_type"]),
            ("ix_backfill_checkpoints_instrument_id", ["instrument_id"]),
            ("ix_backfill_checkpoints_priority", ["priority"]),
            ("ix_backfill_checkpoints_next_attempt_at", ["next_attempt_at"]),
            ("ix_backfill_checkpoints_status", ["status", "updated_at"]),
        ],
        "ingestion_anomalies": [
            ("ix_ingestion_anomalies_instrument_id", ["instrument_id"]),
            ("ix_ingestion_anomalies_ticker", ["ticker"]),
            ("ix_ingestion_anomalies_job_type", ["job_type"]),
            ("ix_ingestion_anomalies_kind", ["kind"]),
            ("ix_ingestion_anomalies_severity", ["severity"]),
            ("ix_ingestion_anomalies_resolved", ["resolved"]),
            ("ix_ingestion_anomalies_instrument_created", ["instrument_id", "created_at"]),
        ],
        "historical_corrections": [
            ("ix_historical_corrections_instrument_id", ["instrument_id"]),
            ("ix_historical_corrections_record_type", ["record_type"]),
            ("ix_historical_corrections_instrument_field", ["instrument_id", "field"]),
        ],
        "financial_report_releases": [
            ("ix_financial_report_releases_instrument_id", ["instrument_id"]),
            ("ix_financial_report_releases_reporting_period", ["reporting_period"]),
            ("ix_financial_report_releases_available_at", ["available_at"]),
            ("ix_financial_report_releases_document_hash", ["document_hash"]),
            ("ix_financial_reports_instrument_period", ["instrument_id", "reporting_period"]),
            ("ix_financial_reports_available", ["instrument_id", "available_at"]),
        ],
        "dividend_events": [
            ("ix_dividend_events_instrument_id", ["instrument_id"]),
            ("ix_dividend_events_ex_date", ["ex_date"]),
            ("ix_dividend_events_status", ["status"]),
            ("ix_dividend_events_fingerprint", ["fingerprint"]),
            ("ix_dividend_events_instrument_ex", ["instrument_id", "ex_date"]),
        ],
    }.items():
        for name, cols in indexes:
            op.create_index(name, table, cols)


def downgrade() -> None:
    for table in (
        "dividend_events", "financial_report_releases", "historical_corrections",
        "ingestion_anomalies", "backfill_checkpoints", "historical_coverage",
        "historical_trades", "daily_market_snapshots", "market_observations",
    ):
        op.drop_table(table)
