"""Add independent KASE stock domain without changing bond contracts.

Revision ID: e8f1a24c9b70
Revises: d4c8a10f6e21
Create Date: 2026-08-14 22:00:00+00:00
"""

from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "e8f1a24c9b70"
down_revision: str | None = "d4c8a10f6e21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _ts():
    return [sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False)]


def _source():
    return [sa.Column("source", sa.String(64)), sa.Column("source_identifier", sa.String(255)), sa.Column("source_url", sa.String(1024)), sa.Column("source_timestamp", sa.DateTime(timezone=True)), sa.Column("fetched_at", sa.DateTime(timezone=True))]


def _computed():
    return [sa.Column("formula_version", sa.String(32)), sa.Column("model_version", sa.String(32)), sa.Column("calculated_at", sa.DateTime(timezone=True))]


def upgrade() -> None:
    op.create_table(
        "instruments", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("ticker", sa.String(64), nullable=False), sa.Column("isin", sa.String(16)),
        sa.Column("issuer_id", sa.Integer(), sa.ForeignKey("issuers.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("instrument_type", sa.String(24), nullable=False), sa.Column("security_type", sa.String(64)),
        sa.Column("currency", sa.String(3), nullable=False), sa.Column("market_segment", sa.String(64)),
        sa.Column("listing_status", sa.String(32)), sa.Column("kase_url", sa.String(1024)),
        sa.Column("is_active", sa.Boolean(), nullable=False), *_source(), *_ts(),
        sa.UniqueConstraint("instrument_type", "ticker", name="uq_instrument_type_ticker"),
    )
    for name in ("ticker", "isin", "issuer_id", "instrument_type", "is_active"):
        op.create_index(f"ix_instruments_{name}", "instruments", [name])
    op.create_index("ix_instruments_type_active", "instruments", ["instrument_type", "is_active"])

    op.create_table(
        "stocks", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("instrument_id", sa.Integer(), sa.ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("share_class", sa.String(32)), sa.Column("shares_outstanding", sa.Float()),
        sa.Column("free_float", sa.Float()), sa.Column("market_cap", sa.Float()), sa.Column("sector", sa.String(64)),
        sa.Column("industry", sa.String(128)), sa.Column("listing_date", sa.Date()), sa.Column("dividend_frequency", sa.Integer()),
        sa.Column("last_dividend", sa.Float()), sa.Column("last_dividend_date", sa.Date()),
        sa.Column("next_expected_dividend_date", sa.Date()), sa.Column("next_dividend_is_scenario", sa.Boolean(), nullable=False),
        sa.Column("lot_size", sa.Integer(), nullable=False), sa.Column("liquidity_class", sa.Integer()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)), sa.Column("last_changed_at", sa.DateTime(timezone=True)),
        *_source(), *_ts(),
    )
    for name in ("instrument_id", "sector", "industry", "last_checked_at", "last_changed_at"):
        op.create_index(f"ix_stocks_{name}", "stocks", [name])

    op.create_table(
        "stock_quotes", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        *[sa.Column(n, sa.Float()) for n in ("bid", "ask", "bid_volume", "ask_volume", "last", "open", "high", "low", "close", "previous_close", "volume", "turnover")],
        sa.Column("number_of_trades", sa.Integer()), sa.Column("data_mode", sa.String(16), nullable=False),
        sa.Column("content_hash", sa.String(64)), *_source(), *_ts(),
    )
    for name in ("stock_id", "timestamp", "data_mode", "content_hash"):
        op.create_index(f"ix_stock_quotes_{name}", "stock_quotes", [name])
    op.create_index("ix_stock_quotes_stock_ts", "stock_quotes", ["stock_id", "timestamp"])

    op.create_table(
        "stock_financial_periods", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False), sa.Column("period_type", sa.String(8), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False), sa.Column("is_audited", sa.Boolean()),
        *[sa.Column(n, sa.Float()) for n in ("revenue", "ebitda", "operating_profit", "net_income", "total_assets", "total_equity", "total_debt", "cash", "operating_cash_flow", "free_cash_flow", "eps", "book_value", "shares_outstanding", "capital_adequacy", "npl_ratio", "loans", "deposits", "net_interest_margin", "cost_to_income", "provisions")],
        *_source(), *_ts(), sa.UniqueConstraint("stock_id", "period_end", "period_type", name="uq_stock_financial_period"),
    )
    op.create_index("ix_stock_financial_periods_stock_id", "stock_financial_periods", ["stock_id"])
    op.create_index("ix_stock_financials_stock_period", "stock_financial_periods", ["stock_id", "period_end"])

    op.create_table(
        "dividends", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("ex_date", sa.Date()), sa.Column("record_date", sa.Date()), sa.Column("payment_date", sa.Date()),
        sa.Column("dividend_per_share", sa.Float(), nullable=False), sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False), *_source(), *_ts(),
        sa.UniqueConstraint("stock_id", "record_date", "dividend_per_share", name="uq_dividend_event"),
    )
    op.create_index("ix_dividends_stock_id", "dividends", ["stock_id"]); op.create_index("ix_dividends_status", "dividends", ["status"])

    op.create_table(
        "stock_metrics", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        *[sa.Column(n, sa.Float()) for n in ("pe", "pb", "ev_ebitda", "fcf_yield", "trailing_dividend_yield", "forward_dividend_yield", "roe", "roa", "net_margin", "revenue_growth", "earnings_growth", "eps_growth", "net_debt", "volatility", "max_drawdown")],
        *_computed(), *_ts(),
    )
    op.create_index("ix_stock_metrics_stock_id", "stock_metrics", ["stock_id"]); op.create_index("ix_stock_metrics_as_of", "stock_metrics", ["as_of"]); op.create_index("ix_stock_metrics_stock_asof", "stock_metrics", ["stock_id", "as_of"])

    op.create_table(
        "stock_scores", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("value", sa.Float()), sa.Column("version", sa.String(32), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False), sa.Column("confidence", sa.Float()),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE")), sa.Column("inputs", sa.JSON()), sa.Column("notes", sa.Text()), *_ts(),
    )
    for name in ("stock_id", "kind", "user_id"):
        op.create_index(f"ix_stock_scores_{name}", "stock_scores", [name])
    op.create_index("ix_stock_scores_stock_kind", "stock_scores", ["stock_id", "kind", "calculated_at"])

    with op.batch_alter_table("portfolio_positions") as batch:
        batch.alter_column("bond_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("stock_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("instrument_type", sa.String(16), nullable=False, server_default="bond"))
        batch.add_column(sa.Column("purchase_price", sa.Float(), nullable=True))
        batch.create_foreign_key("fk_portfolio_positions_stock_id_stocks", "stocks", ["stock_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_portfolio_positions_stock_id", "portfolio_positions", ["stock_id"])
    op.create_index("ix_portfolio_positions_instrument_type", "portfolio_positions", ["instrument_type"])


def downgrade() -> None:
    op.drop_index("ix_portfolio_positions_instrument_type", table_name="portfolio_positions")
    op.drop_index("ix_portfolio_positions_stock_id", table_name="portfolio_positions")
    with op.batch_alter_table("portfolio_positions") as batch:
        batch.drop_constraint("fk_portfolio_positions_stock_id_stocks", type_="foreignkey")
        batch.drop_column("purchase_price"); batch.drop_column("instrument_type"); batch.drop_column("stock_id")
        batch.alter_column("bond_id", existing_type=sa.Integer(), nullable=False)
    for table in ("stock_scores", "stock_metrics", "dividends", "stock_financial_periods", "stock_quotes", "stocks", "instruments"):
        op.drop_table(table)
