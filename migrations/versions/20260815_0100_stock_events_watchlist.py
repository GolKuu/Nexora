"""Add verified stock corporate actions and stock-aware watchlist/alerts.

Revision ID: a5b1c20d7e44
Revises: e8f1a24c9b70
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "a5b1c20d7e44"
down_revision: str | None = "e8f1a24c9b70"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "corporate_actions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("stock_id", sa.Integer(), sa.ForeignKey("stocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("action_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("event_date", sa.Date()),
        sa.Column("title", sa.String(1024), nullable=False),
        sa.Column("details", sa.JSON()),
        sa.Column("source", sa.String(64)),
        sa.Column("source_identifier", sa.String(255)),
        sa.Column("source_url", sa.String(1024)),
        sa.Column("source_timestamp", sa.DateTime(timezone=True)),
        sa.Column("fetched_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("stock_id", "action_type", "event_date", "source_url", name="uq_stock_corporate_action"),
    )
    op.create_index("ix_corporate_actions_stock_id", "corporate_actions", ["stock_id"])
    op.create_index("ix_corporate_actions_action_type", "corporate_actions", ["action_type"])
    op.create_index("ix_corporate_actions_status", "corporate_actions", ["status"])
    op.create_index("ix_corporate_actions_stock_date", "corporate_actions", ["stock_id", "event_date"])

    with op.batch_alter_table("watchlist") as batch:
        batch.alter_column("bond_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("stock_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("instrument_type", sa.String(16), nullable=False, server_default="bond"))
        batch.create_foreign_key("fk_watchlist_stock_id_stocks", "stocks", ["stock_id"], ["id"], ondelete="CASCADE")
        batch.create_unique_constraint("uq_watchlist_user_stock", ["user_id", "stock_id"])
        batch.create_unique_constraint("uq_watchlist_anon_stock", ["anonymous_token", "stock_id"])
    op.create_index("ix_watchlist_stock_id", "watchlist", ["stock_id"])
    op.create_index("ix_watchlist_instrument_type", "watchlist", ["instrument_type"])

    with op.batch_alter_table("alerts") as batch:
        batch.alter_column("bond_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("stock_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("instrument_type", sa.String(16), nullable=False, server_default="bond"))
        batch.create_foreign_key("fk_alerts_stock_id_stocks", "stocks", ["stock_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_alerts_stock_id", "alerts", ["stock_id"])
    op.create_index("ix_alerts_instrument_type", "alerts", ["instrument_type"])
    op.create_index("ix_alerts_stock_active", "alerts", ["is_active", "stock_id"])


def downgrade() -> None:
    op.drop_index("ix_alerts_stock_active", table_name="alerts")
    op.drop_index("ix_alerts_instrument_type", table_name="alerts")
    op.drop_index("ix_alerts_stock_id", table_name="alerts")
    with op.batch_alter_table("alerts") as batch:
        batch.drop_constraint("fk_alerts_stock_id_stocks", type_="foreignkey")
        batch.drop_column("instrument_type")
        batch.drop_column("stock_id")
        batch.alter_column("bond_id", existing_type=sa.Integer(), nullable=False)
    op.drop_index("ix_watchlist_instrument_type", table_name="watchlist")
    op.drop_index("ix_watchlist_stock_id", table_name="watchlist")
    with op.batch_alter_table("watchlist") as batch:
        batch.drop_constraint("uq_watchlist_anon_stock", type_="unique")
        batch.drop_constraint("uq_watchlist_user_stock", type_="unique")
        batch.drop_constraint("fk_watchlist_stock_id_stocks", type_="foreignkey")
        batch.drop_column("instrument_type")
        batch.drop_column("stock_id")
        batch.alter_column("bond_id", existing_type=sa.Integer(), nullable=False)
    op.drop_table("corporate_actions")
