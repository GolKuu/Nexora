"""Persist news, forecast and chart display preferences.

Revision ID: f6a3c92d8b14
Revises: e5f2b78c4a03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6a3c92d8b14"
down_revision: str | None = "e5f2b78c4a03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = (
        sa.Column("conservative_missing_data_mode", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("news_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("kase_news_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("external_news_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("chart_news_markers_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("forecast_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("uncertainty_intervals_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("default_chart_range", sa.String(length=8), nullable=False, server_default="1y"),
    )
    with op.batch_alter_table("user_settings") as batch_op:
        for column in columns:
            batch_op.add_column(column)


def downgrade() -> None:
    with op.batch_alter_table("user_settings") as batch_op:
        for name in (
            "default_chart_range", "uncertainty_intervals_enabled", "forecast_enabled",
            "chart_news_markers_enabled", "external_news_enabled", "kase_news_enabled",
            "news_enabled", "conservative_missing_data_mode",
        ):
            batch_op.drop_column(name)
